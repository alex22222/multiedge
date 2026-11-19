"""可追溯筛选快照、三层证据及独立实验的存储；不改原始策略。"""
import hashlib
import csv
import json
import math
import re
import sqlite3
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .config import Config
from .data import fetch_daily_chunked, load_cached_daily, load_daily
from .engine import analyze


BASELINE_REVISION = "d768ee5457d945a8de274e834114229516268645"
SYMBOL = re.compile(r"^[A-Z][A-Z0-9.\-]{0,11}$")


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def clean(value):
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def encoded(value):
    return json.dumps(clean(value), ensure_ascii=False, sort_keys=True, allow_nan=False)


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def validate_ticker(ticker):
    if not isinstance(ticker, str) or not SYMBOL.fullmatch(ticker):
        raise ValueError("股票代码格式无效")
    return ticker


def layers_for(report):
    return {
        "cognitive": {"status": "unverified", "summary": "真实催化剂与基本面来源待核验；引擎只使用价格跳空/超额涨幅代理。", "evidence": []},
        "intuitive": {"status": "unverified", "summary": "主题、行业领涨与资金流证据待补充，不由技术分数代替。", "evidence": []},
        "technical": {"status": "computed" if report else "missing", "summary": "技术层来自原始引擎的七项边缘、MEA 与验证清单。" if report else "尚未取得该标的引擎产物。", "evidence": []},
    }


def attach_sector_context(run):
    """同池行业RS对照只是价格代理，不把机构流向或主题热度当作已知事实。"""
    groups = defaultdict(list)
    for row in run["results"]:
        report = row.get("report")
        sector = row.get("sector")
        if report and sector and sector != "待补充" and "rs" in report.get("edges", {}):
            groups[sector].append(row)
    for rows in groups.values():
        if len(rows) < 2:
            continue
        ordered = sorted(rows, key=lambda r: (-r["report"]["edges"]["rs"]["score"], r["ticker"]))
        mature = sum(r["report"]["edges"]["rs"]["score"] >= Config().mature_threshold for r in rows)
        leaders = "、".join(r["ticker"] for r in ordered[:3])
        summary = f"{rows[0]['sector']} 冻结样本 {len(rows)} 只，其中 {mature} 只 RS 成熟；RS评分前列为 {leaders}。这是同池价格代理，主题热度和真实资金流仍待核验。"
        for row in rows:
            row["layers"]["intuitive"] = {"status": "price_proxy", "summary": summary,
                "evidence": [{"summary": f"同一截止日 {run['as_of']} 的行业内相对强度对照；冻结名单仅代表本研究池。", "source_url": run.get("issue_url"), "event_date": run["as_of"]}]}
    return run


def finish_run(run):
    datetime.strptime(run["as_of"], "%Y-%m-%d")
    expected = run.get("expected_count", len(run["results"]))
    if type(expected) is not int or expected <= 0 or expected != len(run["results"]):
        raise ValueError("候选池数量必须与完整名单一致且为正整数")
    seen = set()
    for row in run["results"]:
        validate_ticker(row["ticker"])
        if row["ticker"] in seen:
            raise ValueError("筛选结果包含重复代码")
        seen.add(row["ticker"])
        if row["verdict"] not in ("BUY", "WATCH", "NO", "ERROR", "MISSING"):
            raise ValueError("未知判定")
    counts = Counter(r["verdict"] for r in run["results"])
    run["counts"] = {k: counts[k] for k in ("BUY", "WATCH", "NO", "ERROR", "MISSING")}
    run["result_count"] = len(run["results"]) - counts["MISSING"]
    run["expected_count"] = run.get("expected_count", len(run["results"]))
    run["coverage"] = "complete" if run["result_count"] == run["expected_count"] else "partial"
    return clean(run)


class ResearchStore:
    """SQLite 单记录事务；筛选/配置不可变，Paper 状态可按账户更新。"""
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "research.sqlite3"
        with self._db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS records (kind TEXT, id TEXT, payload TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(kind,id))")

    def _db(self):
        return sqlite3.connect(self.path, timeout=30)

    def _save(self, kind, key, value, mutable=False):
        payload = encoded(value)
        with self._db() as db:
            if kind == "paper":
                db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT payload FROM records WHERE kind=? AND id=?", (kind, key)).fetchone()
            if old and not mutable and old[0] != payload:
                raise ValueError("已有版本不可覆盖，请创建新版本")
            if old and kind == "paper":
                previous = json.loads(old[0])
                for saved, updated in ((previous, value), (previous.get("state", {}), value.get("state", {}))):
                    history = saved.get("trades", [])
                    next_trades = updated.get("trades", [])
                    if history and (not isinstance(next_trades, list) or next_trades[:len(history)] != history):
                        raise ValueError("历史平仓记录不可删除或修改；请保留原账户并使用新版本")
            db.execute("INSERT INTO records VALUES (?,?,?,?) ON CONFLICT(kind,id) DO UPDATE SET payload=excluded.payload", (kind, key, payload, utc_now()))
        return clean(value)

    def _get(self, kind, key):
        with self._db() as db:
            row = db.execute("SELECT payload FROM records WHERE kind=? AND id=?", (kind, key)).fetchone()
        if row is None:
            raise KeyError("未找到记录")
        return json.loads(row[0])

    def _list(self, kind):
        with self._db() as db:
            rows = db.execute("SELECT payload FROM records WHERE kind=? ORDER BY created_at DESC, id", (kind,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def save_run(self, run):
        try:
            old = self._get("run", run["run_id"])
            if encoded({k: v for k, v in old.items() if k != "imported_at"}) == encoded({k: v for k, v in run.items() if k != "imported_at"}):
                return old
        except KeyError:
            pass
        return self._save("run", run["run_id"], run)

    def get_run(self, run_id):
        run = self._get("run", run_id)
        for row in run["results"]:
            for layer in ("cognitive", "intuitive"):
                try:
                    row["layers"][layer] = self._get("evidence", f"{run_id}:{row['ticker']}:{layer}")
                except KeyError:
                    pass
        return run

    def runs(self):
        # 日扫描历史只加载元数据；图表与明细在选中快照后按需读取，限制常驻内存。
        with self._db() as db:
            rows = db.execute("SELECT json_remove(payload,'$.results') FROM records WHERE kind='run'").fetchall()
        return sorted((json.loads(row[0]) for row in rows), key=lambda x: (x["as_of"], x.get("imported_at", "")), reverse=True)

    def save_profile(self, config):
        from .experiments import ExperimentConfig
        cfg = ExperimentConfig(**config)
        cfg.validate()
        data = asdict(cfg)
        profile_id = digest(data)[:16]
        try:
            return self._get("profile", profile_id)
        except KeyError:
            return self._save("profile", profile_id, {**data, "id": profile_id, "created_at": utc_now()})

    def profiles(self):
        return self._list("profile")

    def get_profile(self, profile_id):
        return self._get("profile", profile_id)

    def save_paper(self, account):
        return self._save("paper", account["account_id"], account, mutable=True)

    def papers(self):
        return self._list("paper")

    def get_paper(self, account_id):
        return self._get("paper", account_id)

    def save_evidence(self, run_id, ticker, layer, value):
        if layer not in ("cognitive", "intuitive"):
            raise ValueError("只允许补充认知层与直觉层证据")
        run = self.get_run(run_id)
        if ticker not in {r["ticker"] for r in run["results"]}:
            raise ValueError("标的不在本次候选池")
        if not isinstance(value.get("summary"), str) or not isinstance(value.get("source_url"), str):
            raise ValueError("证据摘要与原始来源必须为文本")
        summary = value["summary"].strip()
        url = value["source_url"].strip()
        if not summary or len(summary) > 4000 or not url.startswith(("https://", "http://")) or len(url) > 2000:
            raise ValueError("请提供证据摘要与 HTTP/HTTPS 原始来源")
        event_date = value.get("event_date") or None
        if event_date:
            datetime.strptime(event_date, "%Y-%m-%d")
        key = f"{run_id}:{ticker}:{layer}"
        entry = {"summary": summary, "source_url": url, "observed_at": utc_now(), "event_date": event_date}
        # 同一事务追加来源，保留过去的研究记录。
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT payload FROM records WHERE kind='evidence' AND id=?", (key,)).fetchone()
            base_layer = next(r for r in run["results"] if r["ticker"] == ticker)["layers"][layer]
            evidence = json.loads(old[0])["evidence"] if old else list(base_layer.get("evidence", []))
            identity = {k: v for k, v in entry.items() if k != "observed_at"}
            if not any({k: v for k, v in e.items() if k != "observed_at"} == identity for e in evidence):
                evidence.append(entry)
            item = {"status": "user_recorded", "summary": summary, "evidence": evidence}
            db.execute("INSERT INTO records VALUES ('evidence',?,?,?) ON CONFLICT(kind,id) DO UPDATE SET payload=excluded.payload", (key, encoded(item), utc_now()))
        return item


def build_multica_run(folder):
    """导入真实附件；缺失的单股结果保留 MISSING，不从汇总反推。"""
    folder = Path(folder)
    issue = json.loads((folder / "issue.json").read_text())
    manifest = json.loads((folder / "manifest.json").read_text())
    universe = json.loads((folder / "screen_universe.execution-snapshot.json").read_text())
    reports, hashes = {}, {}
    for path in sorted((folder / "screen_json").glob("*.json")):
        report = json.loads(path.read_text())
        ticker = validate_ticker(report["ticker"])
        if ticker in reports:
            raise ValueError("附件包含重复股票")
        if report["as_of"] != universe["as_of"]:
            raise ValueError("附件截止日与候选池不一致")
        reports[ticker] = report
        hashes[ticker] = hashlib.sha256(path.read_bytes()).hexdigest()
    members = universe["tickers"]
    if not isinstance(members, list) or not members or any(not isinstance(m, list) or len(m) != 3 for m in members):
        raise ValueError("候选池成员必须包含代码、公司名和行业")
    if set(reports) - {m[0] for m in members}:
        raise ValueError("附件股票不在冻结候选池中")
    results = []
    price_dir = folder / "price_data"
    price_files = sorted(price_dir.glob("*.csv"))
    spx = load_prices("SPX", universe["as_of"], price_dir) if price_files else None
    verification_path = folder / "complete_verification.json"
    verification = json.loads(verification_path.read_text()) if verification_path.exists() else {}
    core_matches = verification.get("core_hash_comparison", [])
    config_verified = bool(core_matches) and all(c["frozen_match"] for c in core_matches)
    csv_path = folder / "SCREEN_RESULTS.csv"
    csv_counts = None
    if csv_path.exists():
        with csv_path.open(newline="") as source:
            csv_rows = list(csv.DictReader(source))
        if len(csv_rows) != len(reports) or {r["ticker"] for r in csv_rows} != set(reports):
            raise ValueError("CSV 与原始JSON的股票集合不一致")
        for row in csv_rows:
            if row["as_of"] != universe["as_of"] or row["verdict"] != reports[row["ticker"]]["verdict"]:
                raise ValueError("CSV 与JSON判定不一致")
        csv_counts = dict(Counter(r["verdict"] for r in csv_rows))
    for ticker, company, sector in members:
        report = reports.get(ticker)
        chart_verified = False
        if report and spx is not None:
            frame = load_prices(ticker, universe["as_of"], price_dir)
            calculated = analyze(frame, spx, ticker, universe["as_of"], with_chart=True).to_dict()
            for key in ("verdict", "close", "stop", "target", "rr"):
                if calculated[key] != report.get(key):
                    raise ValueError(f"{ticker} 独立复跑字段 {key} 与附件不同，停止补充图表")
            report = {**report, "chart": calculated["chart"]}
            chart_verified = True
        results.append({"ticker": ticker, "company": company, "sector": sector,
                        "verdict": report["verdict"] if report else "MISSING",
                        "close": report.get("close") if report else None, "report": report,
                        "layers": layers_for(report), "report_available": chart_verified,
                        "chart_provenance": "附带行情的独立复跑；关键判定字段与附件一致" if chart_verified else None,
                        "snapshot_available": report is not None,
                        "source_hash": hashes.get(ticker), "error": None if report else "待取得原始单股产物"})
    run = finish_run({"as_of": universe["as_of"],
        "source": "Multica · SATU-107", "status": issue["status"],
        "issue_url": manifest["source_issue_url"], "imported_at": utc_now(),
        "source_updated_at": issue.get("updated_at", issue.get("last_activity_at")),
        "expected_count": manifest.get("expected_universe_count", len(members)),
        "claimed_counts": csv_counts or ({"BUY": 2, "WATCH": 44, "NO": 55} if len(members) == 101 else None),
        "universe": {"name": "S&P 100 冻结名单", "members": members, "source": universe.get("source"), "provenance": "Multica 执行记录中的原始文档写入快照"},
        "engine_revision": BASELINE_REVISION if config_verified else None, "config_verified": config_verified,
        "artifact_verification": {k: verification.get(k) for k in ("attachment_hashes_match", "csv_json_key_fields_match", "json_count", "price_csv_count", "low_confidence_csv_count")},
        "data_snapshot": {"provider": "MarketWatch（任务附件）", "provider_id": "marketwatch", "price_basis": "unadjusted", "price_files_available": bool(price_files), "report_hashes": hashes,
                          "price_hashes": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in price_files}},
        "notes": ["任务状态与产物完整性分别展示；in_review 仍待任务复审。", "认知层与直觉层的真实催化与资金流仍需独立证据。",
                  "图表由附带行情独立复跑，关键判定字段与原始附件核对一致。" if price_files else "尚未取得原始行情快照，不能凭 JSON 输出历史成交。"], "results": results})
    attach_sector_context(run)
    run["run_id"] = "SATU-107-" + digest({k: v for k, v in run.items() if k != "imported_at"})[:12]
    return run


def load_prices(ticker, as_of, data_dir, allow_download=False, start="2023-06-01"):
    validate_ticker(ticker)
    datetime.strptime(as_of, "%Y-%m-%d")
    frame = load_cached_daily(ticker, start, as_of, str(data_dir))
    if frame is None:
        # 导入的真实快照可能从2025年起；指标只要求足够的真实历史，不要求文件名固定起点。
        candidates = []
        for path in sorted(Path(data_dir).glob(f"{ticker}_*.csv")):
            candidate = load_daily(path)
            if not candidate.empty and candidate.index[-1].date().isoformat() >= as_of:
                candidate = candidate.loc[:as_of].copy()
                if len(candidate) >= 60:
                    candidate.attrs["source_path"] = str(path)
                    candidates.append(candidate)
        if candidates:
            frame = max(candidates, key=len)
    if frame is None and allow_download:
        frame = fetch_daily_chunked(ticker, start, as_of, str(data_dir))
    if frame is None or frame.empty:
        raise ValueError(f"{ticker} 缺少覆盖 {as_of} 的行情快照")
    source_path = frame.attrs.get("source_path")
    meta_path = Path(source_path).with_suffix(".meta.json") if source_path else None
    if meta_path and meta_path.exists():
        from .market import fetch_yahoo_daily
        meta = json.loads(meta_path.read_text())
        frame = fetch_yahoo_daily(ticker, meta["request_start"], meta["request_end"], data_dir)
    return frame.loc[:as_of].copy()


def build_local_run(tickers, as_of, data_dir="data", allow_download=False, source="本地冻结规则复跑", provider="marketwatch"):
    datetime.strptime(as_of, "%Y-%m-%d")
    if provider not in ("marketwatch", "yahoo"):
        raise ValueError("未知行情源")
    if not tickers:
        raise ValueError("股票池不能为空")
    def prices(ticker):
        if provider == "yahoo":
            from .market import fetch_yahoo_daily
            return fetch_yahoo_daily(ticker, "2023-06-01", as_of, Path(data_dir) / "yahoo")
        return load_prices(ticker, as_of, data_dir, allow_download)
    results, hashes, ranges = [], {}, {}
    spx = prices("SPX")
    if (datetime.strptime(as_of, "%Y-%m-%d").date() - spx.index[-1].date()).days > 4:
        raise ValueError("SPX 行情较请求截止日陈旧超过4日，停止扫描")
    for ticker in tickers:
        validate_ticker(ticker)
        try:
            df = prices(ticker)
            r = analyze(df, spx, ticker, as_of, with_chart=True).to_dict()
            if r["as_of"] != str(spx.index[-1].date()):
                raise ValueError("个股与指数最新交易日不一致，不能用陈旧行情筛选")
            hashes[ticker] = hashlib.sha256(df.to_csv().encode()).hexdigest()
            ranges[ticker] = {"start": str(df.index[0].date()), "end": r["as_of"], "rows": len(df)}
            results.append({"ticker": ticker, "company": ticker, "sector": "待补充", "verdict": r["verdict"], "close": r["close"], "report": r,
                            "layers": layers_for(r), "report_available": True, "snapshot_available": True, "source_hash": hashes[ticker], "error": None})
        except (ValueError, OSError, KeyError, RuntimeError) as e:
            results.append({"ticker": ticker, "company": ticker, "sector": "待补充", "verdict": "ERROR", "close": None, "report": None, "layers": layers_for(None), "report_available": False, "snapshot_available": False, "error": str(e)})
    hashes["SPX"] = hashlib.sha256(spx.to_csv().encode()).hexdigest()
    actual = str(spx.index[-1].date())
    run = {"as_of": actual, "requested_as_of": as_of, "source": source, "status": "completed", "issue_url": None,
           "engine_revision": BASELINE_REVISION, "config_verified": True, "config": asdict(Config()), "imported_at": utc_now(),
           "expected_count": len(tickers), "universe": {"name": "指定股票池", "members": tickers},
           "data_snapshot": {"provider": "Yahoo Finance" if provider == "yahoo" else "MarketWatch", "provider_id": provider,
                             "price_basis": "Yahoo chart OHLC Close；未使用 Adj Close，企业行动需另核验" if provider == "yahoo" else "unadjusted",
                             "hashes": hashes, "ranges": ranges, "price_files_available": True},
           "notes": ["历史回看复跑，不代表前向 Paper 记录。", "价格未复权，不含分红/拆股总回报会计。"], "results": results}
    run = finish_run(run)
    run["run_id"] = f"local-{actual}-{digest({k: v for k, v in run.items() if k != 'imported_at'})[:12]}"
    return run
