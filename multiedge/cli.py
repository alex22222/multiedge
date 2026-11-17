"""CLI：对一只股票运行系统。

用法：
  python -m multiedge.cli GOOGL --as-of 2025-08-05 [--start 2024-01-01] [--data-dir data]
  python -m multiedge.cli GOOGL --as-of 2025-08-05 --scan 2025-01-01 2025-08-05   # 历史逐日扫描
"""
import argparse
import json
import sys
from pathlib import Path

from .config import Config
from .data import fetch_daily, load_daily
from .engine import analyze


def _load_pair(args):
    end = args.as_of
    try:
        df = fetch_daily(args.ticker, args.start, end, args.data_dir)
        spx = fetch_daily("SPX", args.start, end, args.data_dir)
    except Exception as e:  # 网络不可用时回退纯缓存
        safe = args.ticker.upper().replace("^", "")
        p1 = Path(args.data_dir) / f"{safe}_{args.start}_{end}.csv"
        p2 = Path(args.data_dir) / f"SPX_{args.start}_{end}.csv"
        if not (p1.exists() and p2.exists()):
            raise
        print(f"[warn] 在线获取失败（{e}），改用缓存", file=sys.stderr)
        df, spx = load_daily(p1), load_daily(p2)
    return df, spx


def _fmt(report) -> str:
    L = []
    L.append(f"== {report.ticker} @ {report.as_of} | 收盘 {report.close} | 判定: {report.verdict} ==")
    L.append(f"成熟边缘数(不含动能): {report.mature_count} | MEA区: {report.mea_zone or '无'}")
    for k, e in report.edges.items():
        L.append(f"  [{e['score']:5.1f} {e['state']:8s}] {k}")
        for ev in e["evidence"]:
            L.append(f"        - {ev}")
    if report.stop:
        rr_txt = f"RR≈{report.rr}R" if report.rr is not None else "RR 不可评估"
        L.append(f"止损参考: {report.stop}（现价下方 {report.stop_pct * 100:.1f}%）  目标(前高): {report.target}  {rr_txt}")
    else:
        L.append("止损参考: 不可用（见 risks）")
    for r in report.risks:
        L.append(f"  ! {r}")
    L.append(f"免责: {report.disclaimers}")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(prog="multiedge")
    ap.add_argument("ticker")
    ap.add_argument("--as-of", required=True, help="分析截止日 YYYY-MM-DD")
    ap.add_argument("--start", default="2024-01-01", help="数据起始日")
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--scan", nargs=2, metavar=("FROM", "TO"), help="逐日扫描")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    df, spx = _load_pair(args)
    cfg = Config()

    if args.scan:
        days = [d for d in df.loc[args.scan[0]:args.scan[1]].index]
        rows = []
        for d in days:
            try:
                r = analyze(df, spx, args.ticker, str(d.date()), cfg)
                rows.append((d.date().isoformat(), r.verdict, r.mature_count,
                             round(r.edges["momentum"]["score"], 1), r.mea_zone != []))
            except ValueError:
                continue
        print(f"{'date':12s} {'verdict':6s} {'mature':6s} {'mom':>6s} {'MEA':3s}")
        for row in rows:
            print(f"{row[0]:12s} {row[1]:6s} {row[2]:<6d} {row[3]:6.1f} {str(row[4]):3s}")
        return

    report = analyze(df, spx, args.ticker, args.as_of, cfg)
    print(_fmt(report))
    if args.json:
        print("\n--- JSON ---")
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
