"""研究平台应用逻辑。HTTP 只接收明确的研究配置，不接受任意路径或命令。"""
import fcntl
import hashlib
import json
import math
from dataclasses import fields
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from .data import load_daily
from .config import Config
from .engine import Report
from .experiments import ExperimentConfig, evaluate_experiment
from .paper import run_paper
from .report import render_snapshot
from .research import BASELINE_REVISION, ResearchStore, digest, load_prices, utc_now, validate_ticker
from .trailing import POLICY, POLICY_VERSION, build_stop_update


STRUCTURAL_V2 = "structural_retest_v2"


def _account_trailing_policy(account, observed_at, strategy_version=None):
    if account is not None:
        return account.get("trailing_policy") or account.get("state", {}).get("trailing_policy")
    if strategy_version == STRUCTURAL_V2:
        from .structure_v2 import POLICY as policy
        return {**policy, "version": STRUCTURAL_V2, "enabled_at": observed_at}
    return {**POLICY, "version": POLICY_VERSION, "enabled_at": observed_at}

def _intraday_config(config):
    result = {k: config[k] for k in ("initial_cash", "risk_fraction", "fee_bps", "slippage_bps")}
    if config.get("exit_mode", "structural") == "structural":
        result["exit_mode"] = "structural"
    else:
        result.update({k: config[k] for k in ("stop_loss_pct", "take_profit_pct")})
    if config.get("strategy_version"):
        result["strategy_version"] = config["strategy_version"]
    return result


def _structural_plan(run, report):
    edges = report.get("edges", {})
    markers = (report.get("chart") or {}).get("markers", {})
    return {"basis": "frozen_engine_structure", "source_run_id": run["run_id"],
            "signal_date": run["as_of"], "source_revision": run.get("engine_revision"),
            "report_close": report.get("close"), "stop": report.get("stop"), "target": report.get("target"),
            "stop_anchor": report.get("stop_anchor"), "anchor_date": markers.get("anchor_bar"),
            "ma_zone_level": edges.get("ma_zone", {}).get("level"),
            "trendline_level": edges.get("trendline", {}).get("level"),
            "trendline_frozen": report.get("trendline_frozen"), "mea_zone": report.get("mea_zone"),
            "buffer_atr": Config().stop_buffer_atr if run.get("engine_revision") == BASELINE_REVISION else None,
            "target_basis": "信号日已确认的上一波主要前高；无有效前高时仅跟踪结构止损"}


def latest_complete_date(now=None):
    """完整日线截止：纽约16:30后才允许当日；交易所假日由实际SPX快照再核验。"""
    now = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo("America/New_York"))
    day = now.date()
    if (now.hour, now.minute) < (16, 30):
        day -= timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day.isoformat()


def cached_frames(tickers, data_dir):
    frames = {}
    for ticker in sorted(set(tickers) | {"SPX"}):
        validate_ticker(ticker)
        candidates = []
        splits = {}
        manifests = []
        for path in sorted(Path(data_dir).glob(f"{ticker}_*.csv")):
            meta_path = path.with_suffix(".meta.json")
            if meta_path.exists():
                from .market import fetch_yahoo_daily
                meta = json.loads(meta_path.read_text())
                df = fetch_yahoo_daily(ticker, meta["request_start"], meta["request_end"], data_dir)
                manifests.append({k: meta[k] for k in ("csv_sha256", "raw_sha256", "request_start", "request_end")})
                for event in meta.get("events", {}).get("splits", []):
                    splits[event["date"]] = event
            else:
                df = load_daily(path)
            if not df.empty:
                candidates.append(df)
        if candidates:
            # 同源缓存按结束日/长度排序，重叠日期采用较新快照，保留旧文件独有历史。
            candidates.sort(key=lambda df: (df.index[-1], len(df)))
            combined = pd.concat(candidates).sort_index(kind="stable")
            frames[ticker] = combined[~combined.index.duplicated(keep="last")]
            frames[ticker].attrs = {"splits": list(splits.values()), "snapshots": manifests}
    return frames


class ResearchService:
    def __init__(self, data_dir="data", state_dir="state"):
        self.data_dir = Path(data_dir)
        self.store = ResearchStore(state_dir)

    def _paper_view(self, account):
        from .pnl import build_daily_pnl
        from .pnl_archive import archived_daily_marks
        marks, sessions, issues = archived_daily_marks(account, self.store.root / "intraday_quotes")
        report = build_daily_pnl(account, archived_marks=marks, sessions=sessions)
        report["notes"] = [*report["notes"], *issues]
        audits = [{"ticker": order["ticker"], "signal_id": order["signal_id"],
                   "signal_date": order["signal_date"], "status": order["status"], "reason": order.get("reason"),
                   "observed_at": order.get("observed_at"), "risk_plan": order.get("risk_plan"),
                   "entry_checks": order.get("entry_checks", {}),
                   "entry_confirmation": order.get("entry_confirmation"),
                   "entry_price": order.get("entry_price"), "entry_timestamp": order.get("entry_timestamp")}
                  for order in account.get("orders", [])] if account.get("strategy_version") == STRUCTURAL_V2 else []
        return {**account, "daily_pnl": report, "entry_audits": audits}

    def paper_account(self, account_id):
        return self._paper_view(self.store.get_paper(account_id))

    def state(self):
        from .structure_v2 import ENTRY_POLICY, POLICY as structure_policy
        runs = self.store.runs()
        summary_keys = ("run_id", "as_of", "source", "status", "counts", "claimed_counts", "expected_count", "result_count", "coverage", "issue_url", "imported_at")
        validation_file = self.store.root / "validation.json"
        validation = json.loads(validation_file.read_text()) if validation_file.exists() else {"status": "pending", "notes": ["测试状态尚未生成，请查看部署验证记录。"]}
        job_file = self.store.root / "last-job.json"
        monitor_file = self.store.root / "last-paper-monitor.json"
        return {"runs": [{k: r.get(k) for k in summary_keys} for r in runs],
                "active_run": self.store.get_run(runs[0]["run_id"]) if runs else None,
                "profiles": self.store.profiles(), "paper_accounts": [self._paper_view(account) for account in self.store.papers()],
                "validation": validation,
                "last_job": json.loads(job_file.read_text()) if job_file.exists() else None,
                "paper_automation": {"execution_model": "intraday_5m", "interval_seconds": 300,
                                     "exit_mode": "structural", "strategy_version": STRUCTURAL_V2,
                                     "basis": "BUY先入候选；确认回踩收复、结构止损与成本后至少3R，才允许未来行情成交",
                                     "entry_policy": ENTRY_POLICY, "trailing_policy": structure_policy,
                                     "monitor": json.loads(monitor_file.read_text()) if monitor_file.exists() else {"status": "not_run"}},
                "baseline": {"revision": BASELINE_REVISION, "ticker": "GOOGL", "as_of": "2025-08-01", "verdict": "BUY", "trendline": 185.33, "sr_flip": 184.33, "stop": 184.91, "target": 207.05, "rr": 4.25},
                "methodology": ["七项边缘评分并用硬门槛交集判定", "认知/直觉证据独立记录，技术代理不等于真实催化剂", "历史回放与前向Paper账户独立", "收盘信号下一交易日开盘，迟到导入不回填", "同日止损和目标双触发先止损；费用与滑点独记", "RSI Wilder；MACD EMA adjust=False，MACD高于信号线是状态规则", "未复权日线，不包含分红与拆股总回报", "自定义实验有独立版本，不覆写原始规则"]}

    def stock(self, run_id, ticker):
        validate_ticker(ticker)
        return next(r for r in self.store.get_run(run_id)["results"] if r["ticker"] == ticker)

    def snapshot(self, run_id, ticker):
        run = self.store.get_run(run_id)
        row = self.stock(run_id, ticker)
        if row.get("report") is None:
            raise ValueError("尚未取得该标的的原始引擎产物")
        allowed = {f.name for f in fields(Report)}
        report = Report(**{k: v for k, v in row["report"].items() if k in allowed})
        source = run.get("data_snapshot", {})
        return render_snapshot(report, source_note=f"来源：{run['source']}；任务状态：{run['status']}；截止：{run['as_of']}。本页呈现保存的输出，是否独立复跑请查看验证说明。", price_source=f"{source.get('provider', '待核验')} · {source.get('price_basis', '口径待核验')}")[0]

    def experiment(self, run_id, profile_id):
        run = self.store.get_run(run_id)
        profile = self.store.get_profile(profile_id)
        cfg = ExperimentConfig(**{k: v for k, v in profile.items() if k not in ("id", "created_at")})
        rows = []
        for row in run["results"]:
            if row["verdict"] in ("ERROR", "MISSING"):
                rows.append({"ticker": row["ticker"], "baseline_verdict": row["verdict"], "verdict": "UNAVAILABLE", "values": {}, "checks": [], "error": row.get("error")})
                continue
            try:
                df = load_prices(row["ticker"], run["as_of"], self._data_dir(run))
                result = evaluate_experiment(df, row["verdict"], cfg)
                rows.append({"ticker": row["ticker"], **result, "error": None})
            except ValueError as e:
                rows.append({"ticker": row["ticker"], "baseline_verdict": row["verdict"], "verdict": "UNAVAILABLE", "values": {}, "checks": [], "error": str(e)})
        return {"run_id": run_id, "profile_id": profile_id, "config": profile, "as_of": run["as_of"], "results": rows}

    def _data_dir(self, run):
        return self.data_dir / "yahoo" if run.get("data_snapshot", {}).get("provider_id") == "yahoo" else self.data_dir

    def _trailing_frames(self, tickers, provider, now):
        """完整日线用于持仓管理；独立于当日是否仍为BUY。"""
        cutoff = latest_complete_date(now)
        frames, errors = {}, {}
        for ticker in ["SPX", *sorted(set(tickers) - {"SPX"})]:
            try:
                if provider == "yahoo":
                    from .market import fetch_yahoo_daily
                    frame = fetch_yahoo_daily(ticker, "2023-06-01", cutoff, self.data_dir / "yahoo")
                    meta = frame.attrs
                    frame.attrs = {**meta, "splits": meta.get("events", {}).get("splits", []),
                                   "snapshots": [{k: meta[k] for k in ("raw_sha256", "csv_sha256", "request_start", "request_end", "retrieved_at")}]}
                else:
                    frame = load_prices(ticker, cutoff, self.data_dir, allow_download=True)
                frame = frame.loc[:cutoff].copy()
                if frame.empty or (datetime.fromisoformat(cutoff).date() - frame.index[-1].date()).days > 4:
                    raise ValueError("完整日线缺失或陈旧超过4日")
                if ticker != "SPX" and ("SPX" not in frames or frame.index[-1] != frames["SPX"].index[-1]):
                    raise ValueError("持仓日线与大盘截止日不一致")
                if ticker != "SPX" and not frames["SPX"].index[-21:].difference(frame.index).empty:
                    raise ValueError("持仓日线缺少最近21个大盘交易日中的行情")
                frames[ticker] = frame
            except (ValueError, OSError, KeyError, RuntimeError) as error:
                errors[ticker] = str(error)
        return frames, errors

    def monitor_paper(self, now=None, reconcile=False):
        """只检查已登记的Paper订单/持仓；网络抓取在账本锁外进行。"""
        from .intraday import apply_intraday, fetch_yahoo_intraday
        wall_clock = now is None
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            raise ValueError("盘中检查时间必须带时区")
        local = now.astimezone(ZoneInfo("America/New_York"))
        accounts = [a for a in self.store.papers() if a.get("execution_model") == "intraday_5m" and not a.get("automation_disabled")
                    and (a.get("positions") or any(o["status"] == "pending" for o in a.get("orders", [])))]
        receipt = {"last_checked_at": now.isoformat(), "execution_model": "intraday_5m", "accounts": [], "errors": {}}
        if not accounts:
            return {**receipt, "status": "no_active_orders"}
        minute = local.hour * 60 + local.minute
        if local.weekday() >= 5 or minute < 9 * 60 + 30 or (not reconcile and minute > 16 * 60 + 5):
            return {**receipt, "status": "market_closed"}
        tickers = {p["ticker"] for a in accounts for p in a.get("positions", [])}
        tickers |= {o["ticker"] for a in accounts for o in a.get("orders", []) if o["status"] == "pending"}
        quotes = fetch_yahoo_intraday(sorted(tickers), now=now, cache_dir=self.store.root / "intraday_quotes")
        daily_sources = {}
        for provider in {a.get("provider_id", "marketwatch") for a in accounts if a.get("exit_mode") == "structural" and a.get("positions")}:
            held = {p["ticker"] for a in accounts if a.get("provider_id", "marketwatch") == provider
                    and a.get("exit_mode") == "structural" for p in a.get("positions", [])}
            try:
                daily_sources[provider] = self._trailing_frames(held, provider, now)
            except (ValueError, OSError, KeyError, RuntimeError) as error:
                daily_sources[provider] = ({}, {ticker: str(error) for ticker in held})
        if wall_clock:
            now = datetime.now(timezone.utc)
            receipt["last_checked_at"] = now.isoformat()
            local = now.astimezone(ZoneInfo("America/New_York"))
        if daily_sources:
            cutoff = latest_complete_date(now)
            completed_sessions = [datetime.fromisoformat(b["session_start"]).astimezone(ZoneInfo("America/New_York")).date().isoformat()
                                  for b in quotes["bars"].get("SPX", [])
                                  if datetime.fromisoformat(b["session_end"]) <= now
                                  and datetime.fromisoformat(b["session_start"]).astimezone(ZoneInfo("America/New_York")).date().isoformat() <= cutoff]
            expected_day = max(completed_sessions) if completed_sessions else None
            for frames, frame_errors in daily_sources.values():
                reference = frames.get("SPX")
                if expected_day and reference is not None and str(reference.index[-1].date()) != expected_day:
                    frame_errors["SPX"] = f"完整日线未覆盖已核验的最新交易日 {expected_day}"
        receipt["errors"] = quotes["errors"]
        with (self.store.root / "paper.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            for summary in accounts:
                account = self.store.get_paper(summary["account_id"])
                if account.get("automation_disabled"):
                    continue
                saved_check = account["state"].get("last_checked_at")
                if saved_check and datetime.fromisoformat(saved_check.replace("Z", "+00:00")) > now:
                    receipt["accounts"].append({"account_id": account["account_id"], "positions": len(account["positions"]),
                                                "trades": len(account["trades"]), "status": "superseded"})
                    continue
                config = account["config"]
                ledger_config = _intraday_config(config)
                watched = {p["ticker"] for p in account.get("positions", [])} | {o["ticker"] for o in account.get("orders", []) if o["status"] == "pending"}
                bars = {t: b for t, b in quotes["bars"].items() if t in watched or t == "SPX"}
                errors = {t: e for t, e in quotes["errors"].items() if t in watched or t == "SPX"}
                for ticker in watched | {"SPX"}:
                    if quotes["sources"].get(ticker, {}).get("stale") or not bars.get(ticker):
                        bars.pop(ticker, None)
                        errors.setdefault(ticker, "尚未取得新鲜、完整的5分钟行情")
                for position in account.get("positions", []):
                    source = quotes["sources"].get(position["ticker"], {})
                    daily = daily_sources.get(account.get("provider_id", "marketwatch"), ({}, {}))[0].get(position["ticker"])
                    daily_splits = (daily.attrs.get("splits", daily.attrs.get("events", {}).get("splits", [])) if daily is not None else [])
                    known_splits = [event for event in [*(source.get("splits") or []), *(daily_splits if isinstance(daily_splits, list) else [])]
                                    if isinstance(event, dict) and isinstance(event.get("date"), str)]
                    if any(position["entry_date"] < event["date"] <= local.date().isoformat() for event in known_splits):
                        bars.pop(position["ticker"], None)
                        errors[position["ticker"]] = "持仓期间发生拆股，暂停处理，等待企业行动会计核验"
                if config.get("strategy_version") == STRUCTURAL_V2:
                    for order in account.get("orders", []):
                        if order["status"] != "pending":
                            continue
                        source = quotes["sources"].get(order["ticker"], {})
                        if any(order["signal_date"] < event["date"] <= local.date().isoformat()
                               for event in source.get("splits", [])):
                            bars.pop(order["ticker"], None)
                            errors[order["ticker"]] = "信号后发生拆股，暂停入场，等待结构价位与企业行动核验"
                policy, updates, trailing_errors = None, [], {}
                if config.get("exit_mode", "structural") == "structural":
                    policy = _account_trailing_policy(account, now.isoformat())
                if policy is not None:
                    version = policy["version"]
                    if version == STRUCTURAL_V2:
                        from .structure_v2 import build_stop_update as stop_builder
                    elif version == POLICY_VERSION:
                        stop_builder = build_stop_update
                    else:
                        raise ValueError("账户移动止损版本未知，不能自动切换规则")
                    frames, frame_errors = daily_sources.get(account.get("provider_id", "marketwatch"), ({}, {}))
                    reference = frames.get("SPX")
                    source_day = str(reference.index[-1].date()) if reference is not None and not reference.empty else None
                    known = {(u["source_signal_id"], u["source_as_of"], u["policy_version"]) for u in account.get("stop_updates", [])}
                    for position in account.get("positions", []):
                        ticker = position["ticker"]
                        if ticker in errors or "SPX" in errors:
                            continue
                        try:
                            if ticker in frame_errors or "SPX" in frame_errors:
                                raise ValueError(frame_errors.get(ticker) or frame_errors["SPX"])
                            if source_day is None or ticker not in frames:
                                raise ValueError("尚未取得完整的持仓日线和大盘日线")
                            if (position["source_signal_id"], source_day, version) in known:
                                continue
                            used = frames[ticker].loc[:source_day]
                            if not reference.index[-21:].difference(used.index).empty:
                                raise ValueError("持仓日线缺少最近21个大盘交易日中的行情，暂停计算")
                            for snapshot in frames[ticker].attrs.get("snapshots", []):
                                if snapshot.get("retrieved_at") and datetime.fromisoformat(snapshot["retrieved_at"]) > now:
                                    raise ValueError("日线取得时间晚于本次决定，不能用于更早的移动止损")
                            update = stop_builder(position, frames[ticker], source_as_of=source_day,
                                                  observed_at=now.isoformat(), enabled_at=policy["enabled_at"])
                            if update is not None:
                                updates.append(update)
                        except (ValueError, KeyError, RuntimeError) as error:
                            trailing_errors[ticker] = str(error)
                result = apply_intraday([], bars, now=now, config=ledger_config, state=account["state"],
                                        trailing_policy=policy, stop_updates=updates)
                for ticker, gap in result.get("coverage_gaps", {}).items():
                    errors[ticker] = f"盘中行情覆盖中断：需要 {gap['expected_from']} 起的连续行情，暂停处理"
                account.update(result)
                account["config"] = config
                account["updated_at"] = now.isoformat()
                account["monitor"] = {"status": "data_error" if errors else "trailing_error" if trailing_errors else "checked", "last_checked_at": now.isoformat(),
                                      "quote_sources": {t: s for t, s in quotes["sources"].items() if t in watched or t == "SPX"},
                                      "errors": errors, "trailing_errors": trailing_errors}
                account["data_missing"] = sorted(t for t in watched if not bars.get(t))
                if errors or account["data_missing"]:
                    account["data_stale"] = True
                receipt["errors"].update(errors)
                self.store.save_paper(account)
                receipt["accounts"].append({"account_id": account["account_id"], "positions": len(account["positions"]),
                                            "trades": len(account["trades"]), "status": account["monitor"]["status"]})
        receipt["status"] = "partial" if any(a["status"] in ("data_error", "trailing_error") for a in receipt["accounts"]) else "completed"
        return receipt

    def paper(self, run_id, mode="replay", initial_cash=100000, risk_fraction=.0035, fee_bps=5, slippage_bps=5, profile_id=None,
              exit_mode="structural", stop_loss_pct=None, take_profit_pct=None, execution_model=None, strategy_version=None):
        run = self.store.get_run(run_id)
        if mode == "forward" and run["as_of"] > latest_complete_date():
            raise ValueError("信号日期晚于最近完整交易日")
        config = {"mode": mode, "initial_cash": initial_cash, "risk_fraction": risk_fraction, "fee_bps": fee_bps, "slippage_bps": slippage_bps, "profile_id": profile_id}
        if exit_mode not in ("structural", "fixed_percent"):
            raise ValueError("未知Paper出场方案")
        if exit_mode == "fixed_percent":
            if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in (stop_loss_pct, take_profit_pct)):
                raise ValueError("止损/止盈比例必须为有限数字")
            if not 0 < stop_loss_pct < 1 or not 0 < take_profit_pct <= 10:
                raise ValueError("止损比例须在0到100%之间，止盈须大于0且不超过1000%")
            config.update({"exit_mode": exit_mode, "stop_loss_pct": stop_loss_pct, "take_profit_pct": take_profit_pct})
        elif stop_loss_pct is not None or take_profit_pct is not None:
            raise ValueError("结构出场方案不接受固定百分比参数")
        execution_model = execution_model or ("intraday_5m" if mode == "forward" else "daily_ohlc")
        if execution_model not in ("intraday_5m", "daily_ohlc") or (execution_model == "intraday_5m" and mode != "forward"):
            raise ValueError("盘中模型仅支持前向Paper")
        if strategy_version not in (None, "", STRUCTURAL_V2):
            raise ValueError("未知Paper策略版本")
        strategy_version = strategy_version or None
        if strategy_version:
            if mode != "forward" or exit_mode != "structural" or execution_model != "intraday_5m":
                raise ValueError("v2版本仅支持独立的前向结构盘中Paper")
            config["strategy_version"] = strategy_version
        if execution_model == "intraday_5m":
            config["execution_model"] = execution_model
            if exit_mode == "structural":
                config["exit_mode"] = exit_mode
        if exit_mode == "fixed_percent" or execution_model == "intraday_5m":
            # 网页百分比除以100会产生浮点尾差；同一参数应复用同一账户。
            for key in ("initial_cash", "risk_fraction", "fee_bps", "slippage_bps", "stop_loss_pct", "take_profit_pct"):
                value = config.get(key)
                if not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value):
                    number = float(format(value, ".15g"))
                    config[key] = int(number) if number.is_integer() else number
            initial_cash, risk_fraction, fee_bps, slippage_bps = (
                config[k] for k in ("initial_cash", "risk_fraction", "fee_bps", "slippage_bps"))
            stop_loss_pct, take_profit_pct = config.get("stop_loss_pct"), config.get("take_profit_pct")
        # 进程间锁保护定时任务与网页对同一账户的读-改-写。
        with (self.store.root / "paper.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            selected = {r["ticker"] for r in run["results"] if r["verdict"] == "BUY"}
            if profile_id:
                selected = {r["ticker"] for r in self.experiment(run_id, profile_id)["results"] if r["verdict"] == "BUY"}
            scope = {"config": config, "provider_id": run.get("data_snapshot", {}).get("provider_id", "marketwatch"), "engine_revision": run.get("engine_revision")}
            account_id = f"{mode}-{'baseline' if not profile_id else profile_id}-{digest(scope)[:10]}"
            if mode == "replay":
                account_id += f"-{run_id}"
            old = None
            if mode == "forward":
                try:
                    old = self.store.get_paper(account_id)
                except KeyError:
                    pass
            if old and old.get("automation_disabled"):
                raise ValueError("该示例账户已停用，仅保留历史记录；请使用结构出场或另建实验参数版本")
            observations = {o["signal_id"]: o["observed_at"] for o in (old or {}).get("orders", [])}
            saved_orders = {o["signal_id"]: o for o in (old or {}).get("orders", [])}
            profile_time = self.store.get_profile(profile_id)["created_at"] if profile_id else run["imported_at"]
            first_seen = max((run["imported_at"], profile_time, utc_now()), key=lambda s: datetime.fromisoformat(s.replace("Z", "+00:00")))
            signals = []
            for row in run["results"]:
                if row["ticker"] not in selected or not row.get("report"):
                    continue
                signal_id = (f"{scope['provider_id']}:{run.get('engine_revision') or 'imported'}:{profile_id or 'baseline'}:{run['as_of']}:{row['ticker']}"
                             if mode == "forward" else f"{run_id}:{profile_id or 'baseline'}:{row['ticker']}")
                if strategy_version:
                    signal_id += f":{strategy_version}"
                observed_at = observations.get(signal_id, first_seen) if mode == "forward" else run["imported_at"]
                signals.append({"ticker": row["ticker"], "signal_date": run["as_of"], "stop": row["report"].get("stop"),
                                "target": row["report"].get("target"), "signal_id": signal_id, "observed_at": observed_at})
            if execution_model == "intraday_5m":
                from .intraday import apply_intraday
                for signal in signals:
                    signal["source_run_id"] = run_id
                    if exit_mode == "structural":
                        report = next(r["report"] for r in run["results"] if r["ticker"] == signal["ticker"])
                        saved = saved_orders.get(signal["signal_id"])
                        if saved:
                            signal.update({k: saved[k] for k in ("stop", "target", "risk_plan", "source_run_id")})
                        elif strategy_version == STRUCTURAL_V2:
                            from .structure_v2 import build_entry_plan
                            try:
                                frame = load_prices(signal["ticker"], run["as_of"], self._data_dir(run))
                                source_row = next(row for row in run["results"] if row["ticker"] == signal["ticker"])
                                expected_hash = source_row.get("source_hash") or run.get("data_snapshot", {}).get("hashes", {}).get(signal["ticker"])
                                actual_hash = hashlib.sha256(frame.to_csv().encode()).hexdigest()
                                if not expected_hash or actual_hash != expected_hash:
                                    raise ValueError("日线缓存与冻结信号快照哈希不一致，不能混用结构与行情")
                                metadata = frame.attrs
                                snapshots = list(metadata.get("snapshots") or [])
                                if metadata.get("retrieved_at"):
                                    snapshots.append({key: metadata[key] for key in ("raw_sha256", "csv_sha256", "retrieved_at", "request_start", "request_end") if key in metadata})
                                for snapshot in snapshots:
                                    if snapshot.get("retrieved_at") and datetime.fromisoformat(snapshot["retrieved_at"].replace("Z", "+00:00")) > datetime.fromisoformat(signal["observed_at"].replace("Z", "+00:00")):
                                        raise ValueError("日线取得时间晚于计划观察时间，不能用于过去的入场决定")
                                frame.attrs = {**metadata, "snapshots": snapshots}
                                planned = build_entry_plan(report, frame, source_as_of=run["as_of"],
                                                           observed_at=signal["observed_at"], source_run_id=run_id,
                                                           fee_bps=fee_bps, slippage_bps=slippage_bps)
                                planned["risk_plan"]["source_price_hash"] = actual_hash
                                signal.update({key: planned[key] for key in ("stop", "target", "risk_plan")})
                            except (ValueError, OSError, KeyError, RuntimeError) as error:
                                signal["risk_plan"] = {"strategy_version": STRUCTURAL_V2, "eligible": False,
                                                       "reason": "invalid_daily_plan", "data_error": str(error),
                                                       "source_run_id": run_id, "source_as_of": run["as_of"],
                                                       "observed_at": signal["observed_at"], "stop": signal["stop"],
                                                       "target": signal["target"]}
                        else:
                            signal["risk_plan"] = _structural_plan(run, report)
                ledger_config = _intraday_config(config)
                observed = utc_now()
                policy = _account_trailing_policy(old, observed, strategy_version) if exit_mode == "structural" else None
                result = apply_intraday(signals, {}, now=datetime.fromisoformat(observed.replace("Z", "+00:00")), config=ledger_config,
                                        state=old["state"] if old else None, started_at=first_seen if old is None else None,
                                        trailing_policy=policy)
                result.update({"account_id": account_id, "run_id": run_id, "profile_id": profile_id, "source": run["source"],
                               "updated_at": utc_now(), "config": config, "automatic": True,
                               "validation_status": "forward_paper", "exit_mode": exit_mode,
                               "planned_stop_pct": stop_loss_pct, "planned_target_pct": take_profit_pct,
                               "planned_rr": take_profit_pct / stop_loss_pct if exit_mode == "fixed_percent" else None,
                               "provider_id": scope["provider_id"], "engine_revision": run.get("engine_revision"),
                               "signal_price_basis": run.get("data_snapshot", {}).get("price_basis"),
                               "price_basis": ("Yahoo完整5分钟OHLC；结构价位来自信号日冻结报告" if exit_mode == "structural"
                                               else "Yahoo完整5分钟OHLC；按实际模拟成交价冻结止损/止盈"),
                               "last_checked_at": (old or {}).get("last_checked_at"), "monitor": (old or {}).get("monitor", {"status": "not_run"})})
                return self.store.save_paper(result)
            tickers = selected | {p["ticker"] for p in (old or {}).get("positions", [])} | {o["ticker"] for o in (old or {}).get("orders", []) if o["status"] == "pending"}
            frames = cached_frames(tickers, self._data_dir(run))
            if mode == "replay" and selected and (not selected <= set(frames) or "SPX" not in frames):
                raise ValueError("历史回放需要真实个股与SPX行情快照；当前附件不足，不会根据JSON结果虚构成交。")
            limit = latest_complete_date()
            as_of = min(limit, str(frames["SPX"].index[-1].date())) if "SPX" in frames else run["as_of"]
            if as_of < run["as_of"]:
                if mode == "replay":
                    raise ValueError("现有行情早于信号日，无法回放")
                # 历史示例缓存不能用来处理当前信号。
                frames = {}
                as_of = run["as_of"]
            if old and old["as_of"] > as_of:
                raise ValueError("行情截止日早于账户最新记录")
            if old and old.get("provider_id", "marketwatch") != run.get("data_snapshot", {}).get("provider_id", "marketwatch"):
                raise ValueError("前向账户不能混用行情源，请建立独立账户")
            price_fingerprint = digest({"as_of": as_of, "prices": {ticker: hashlib.sha256(frame.loc[:as_of].to_csv().encode()).hexdigest() for ticker, frame in frames.items()}})
            if mode == "replay":
                # 回放按不可变数据版本重新计算；补齐旧行情不能沿用已跳过历史的前向恢复逻辑。
                account_id += "-" + price_fingerprint[:12]
            for ticker in tickers:
                frame = frames.get(ticker)
                if frame is not None and any(run["as_of"] < event["date"] <= as_of for event in frame.attrs.get("splits", [])):
                    raise ValueError(f"{ticker} 验证区间内发生拆股，需先处理企业行动会计，已暂停该账户推进")
            for position in (old or {}).get("positions", []):
                frame = frames.get(position["ticker"])
                if frame is not None and any(position["entry_date"] < event["date"] <= as_of for event in frame.attrs.get("splits", [])):
                    raise ValueError(f"{position['ticker']} 持仓期间发生拆股，已暂停账户推进")
            result = run_paper(signals, frames, as_of=as_of, mode=mode, initial_cash=initial_cash,
                               risk_fraction=risk_fraction, fee_bps=fee_bps, slippage_bps=slippage_bps,
                               state=old["state"] if old else None,
                               started_at=first_seen if mode == "forward" and old is None else None,
                               exit_mode=exit_mode, stop_loss_pct=stop_loss_pct, take_profit_pct=take_profit_pct)
            result.update({"account_id": account_id, "run_id": run_id, "profile_id": profile_id,
                           "source": run["source"], "updated_at": utc_now(), "config": config,
                           "validation_status": "historical_replay" if mode == "replay" else "forward_paper",
                           "execution_model": "daily_ohlc", "automatic": mode == "forward",
                           "provider_id": run.get("data_snapshot", {}).get("provider_id", "marketwatch"),
                           "engine_revision": run.get("engine_revision"),
                           "price_basis": run.get("data_snapshot", {}).get("price_basis"),
                           "price_fingerprint": price_fingerprint,
                           "price_snapshots": {ticker: frame.attrs.get("snapshots", []) for ticker, frame in frames.items()},
                           "data_missing": sorted(tickers - set(frames))})
            return self.store.save_paper(result)
