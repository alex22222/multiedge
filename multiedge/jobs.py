"""确定性研究任务入口。定时器运行数据与Paper流程，不自动调参。"""
import argparse
import json
import os
from pathlib import Path

from .dashboard import STRUCTURAL_V2, ResearchService, latest_complete_date
from .research import ResearchStore, attach_sector_context, build_local_run, build_multica_run, clean, digest, utc_now


def seed(data_dir, state_dir):
    store = ResearchStore(state_dir)
    for ticker, day in (("GOOGL", "2025-08-01"), ("GOOGL", "2025-06-30"), ("NVDA", "2025-11-14")):
        store.save_run(build_local_run([ticker], day, data_dir, source="历史验收案例"))
    if not store.profiles():
        store.save_profile({"name": "原始策略 · 无附加过滤器"})
        store.save_profile({"name": "RSI 40–70 + MACD 线上实验", "rsi_enabled": True, "macd_enabled": True})
    # 历史回放是独立样例，不记入前向Paper绩效。
    service = ResearchService(data_dir, state_dir)
    googl = next(r for r in store.runs() if r["as_of"] == "2025-08-01")
    service.paper(googl["run_id"], mode="replay")
    return {"runs": len(store.runs()), "profiles": len(store.profiles()), "paper_accounts": len(store.papers())}


def daily(data_dir, state_dir, universe_path=None, provider="yahoo"):
    service = ResearchService(data_dir, state_dir)
    store = service.store
    if universe_path:
        universe = json.loads(Path(universe_path).read_text())
        members = universe.get("tickers", universe.get("members", [])) if isinstance(universe, dict) else universe
    else:
        snapshots = [r for r in store.runs() if r.get("expected_count", 0) >= 100]
        if not snapshots:
            raise ValueError("没有冻结股票池，请先导入任务产物或指定 --universe")
        members = snapshots[0]["universe"]["members"]
    tickers = [m[0] if isinstance(m, list) else m for m in members]
    as_of = latest_complete_date()
    # 网络或数据错误向调度器报告失败，不将空结果伪装成成功。
    run = build_local_run(tickers, as_of, data_dir, allow_download=True, source="服务器日线扫描 · 冻结规则", provider=provider)
    names = {m[0]: m for m in members if isinstance(m, list) and len(m) == 3}
    for row in run["results"]:
        if row["ticker"] in names:
            _, row["company"], row["sector"] = names[row["ticker"]]
    if names:
        run["universe"] = {"name": "S&P 100 冻结名单", "members": members}
    attach_sector_context(run)
    run["run_id"] = "daily-" + digest({k: v for k, v in run.items() if k not in ("run_id", "imported_at")})[:16]
    run = store.save_run(run)
    if run["counts"]["ERROR"]:
        raise RuntimeError(f"扫描包含 {run['counts']['ERROR']} 个失败标的，见快照 {run['run_id']}")
    account = service.paper(run["run_id"], mode="forward", exit_mode="structural", strategy_version=STRUCTURAL_V2)
    automatic_id = account["account_id"]
    for account in store.papers():
        if (account["mode"] == "forward" and account["account_id"] != automatic_id and not account.get("automation_disabled")
                and account.get("provider_id") == run["data_snapshot"]["provider_id"]
                and account.get("engine_revision") == run.get("engine_revision")):
            # Legacy automatic structural accounts finish their already saved
            # orders/positions under their own policy; only v2 enrolls new BUYs.
            if (account.get("execution_model") == "intraday_5m" and account.get("exit_mode") == "structural"
                    and not account.get("profile_id") and account.get("strategy_version") != STRUCTURAL_V2):
                continue
            cfg = {**account["config"], "execution_model": account.get("execution_model", "daily_ohlc")}
            service.paper(run["run_id"], **cfg)
    monitor = service.monitor_paper(reconcile=True)
    path = Path(state_dir) / "last-paper-monitor.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(clean(monitor), ensure_ascii=False, allow_nan=False))
    os.replace(temporary, path)
    return {"run_id": run["run_id"], "as_of": run["as_of"], "counts": run["counts"]}


def main():
    parser = argparse.ArgumentParser(prog="multiedge.jobs")
    parser.add_argument("command", choices=("seed", "import-multica", "import-run", "daily", "monitor", "status"))
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--state-dir", default="state")
    parser.add_argument("--path")
    parser.add_argument("--universe")
    parser.add_argument("--provider", choices=("yahoo", "marketwatch"), default="yahoo")
    args = parser.parse_args()
    store = ResearchStore(args.state_dir)
    try:
        if args.command == "seed":
            result = seed(args.data_dir, args.state_dir)
        elif args.command in ("import-multica", "import-run"):
            if not args.path:
                raise ValueError("导入必须指定 --path")
            run = build_multica_run(args.path) if args.command == "import-multica" else json.loads(Path(args.path).read_text())
            run = store.save_run(run)
            result = {"run_id": run["run_id"], "coverage": run["coverage"], "result_count": run["result_count"], "expected_count": run["expected_count"]}
        elif args.command == "daily":
            result = daily(args.data_dir, args.state_dir, args.universe, args.provider)
        elif args.command == "monitor":
            result = ResearchService(args.data_dir, args.state_dir).monitor_paper()
        else:
            result = ResearchService(args.data_dir, args.state_dir).state()
        if args.command != "status":
            path = Path(args.state_dir) / ("last-paper-monitor.json" if args.command == "monitor" else "last-job.json")
            temp = path.with_suffix(".tmp")
            receipt = ({**result, "command": args.command, "observed_at": utc_now()} if args.command == "monitor"
                       else {"status": "completed", "command": args.command, "observed_at": utc_now(), "result": result})
            temp.write_text(json.dumps(clean(receipt), ensure_ascii=False, allow_nan=False))
            os.replace(temp, path)
        print(json.dumps(clean(result), ensure_ascii=False, allow_nan=False))
    except (ValueError, RuntimeError, OSError) as error:
        failure = {"status": "failed", "command": args.command, "observed_at": utc_now(), "error": str(error)}
        path = Path(args.state_dir) / ("last-paper-monitor.json" if args.command == "monitor" else "last-job.json")
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(failure, ensure_ascii=False))
        os.replace(temp, path)
        print(json.dumps(failure, ensure_ascii=False))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
