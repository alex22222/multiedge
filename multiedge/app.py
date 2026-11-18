"""策略研究台：本机 HTTP 服务，展示快照、证据、指标实验与 Paper。

用法：
  .venv/bin/python -m multiedge.app [--port 8321]
默认监听127.0.0.1；/analyze保留单股输入，/report共用静态报告渲染。
技术数字来自保存的引擎输出，认知/直觉证据与原始规则分别记录。
"""
import argparse
import html
import json
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .dashboard import ResearchService
from .research import clean, validate_ticker
from .report import LONG_START, render_report

DEFAULT_START = LONG_START   # 与 report.py 一致：保证 200MA 全程可见

FORM = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>多趋势验证买入系统</title>
<style>
body{font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;background:#0b1118;color:#dbe4ee;
display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0}
form{background:#111a26;border:1px solid #223047;border-radius:12px;padding:28px 32px;display:flex;gap:12px;flex-wrap:wrap;align-items:end}
label{display:block;color:#9fb3c8;font-size:13px;margin-bottom:4px}
input{background:#0f1722;border:1px solid #334155;color:#dbe4ee;border-radius:8px;padding:9px 12px;font-size:15px}
button{background:#16a34a;color:#fff;border:none;border-radius:8px;padding:10px 22px;font-size:15px;cursor:pointer}
p.note{color:#8a97a8;font-size:12px;width:100%;margin:8px 0 0}
</style></head><body><form action="report" method="get">
<div><label>股票代码（如 GOOGL / NVDA）</label><input name="ticker" value="GOOGL" required></div>
<div><label>截止日期（YYYY-MM-DD）</label><input name="as_of" value="2025-08-01" required></div>
<button type="submit">生成报告</button>
<p class="note">只监听 127.0.0.1；数据来自 MarketWatch 缓存/在线获取；结果不构成投资建议。</p>
</form></body></html>"""


def make_handler(data_dir="data", start=None, state_dir="state"):
    start = start or LONG_START
    service = ResearchService(data_dir, state_dir)
    static_dir = Path(__file__).parent / "static"
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # 静默
            pass

        def _send(self, body, ctype="text/html; charset=utf-8", code=200):
            data = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _json(self, body, code=200):
            self._send(json.dumps(clean(body), ensure_ascii=False, allow_nan=False), "application/json; charset=utf-8", code)

        def _query(self):
            return {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).items()}

        def do_POST(self):
            origin = self.headers.get("Origin")
            if origin and urllib.parse.urlparse(origin).netloc != self.headers.get("Host"):
                self._json({"error": "跨站写入被拒绝"}, 403)
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 32768:
                    raise ValueError("请求体大小无效（上限32KB）")
                if self.headers.get_content_type() != "application/json":
                    raise ValueError("请使用 application/json")
                def reject_constant(value):
                    raise ValueError("JSON 不允许非有限数字")
                body = json.loads(self.rfile.read(size), parse_constant=reject_constant)
                if not isinstance(body, dict):
                    raise ValueError("请求体必须是JSON对象")
                path = urllib.parse.urlparse(self.path).path
                if path == "/api/profile":
                    result = service.store.save_profile(body.get("config", body))
                elif path == "/api/experiment":
                    result = service.experiment(**body)
                elif path == "/api/paper":
                    result = service.paper(**body)
                elif path == "/api/evidence":
                    result = service.store.save_evidence(body["run_id"], body["ticker"], body["layer"], body)
                else:
                    self._json({"error": "接口不存在"}, 404)
                    return
                self._json(result)
            except (ValueError, TypeError, UnicodeError) as e:
                self._json({"error": str(e)}, 400)
            except (KeyError, StopIteration):
                self._json({"error": "记录不存在或缺少必需字段"}, 404)

        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            if u.path in ("/", "/index.html"):
                self._send((static_dir / "index.html").read_text())
                return
            assets = {"/dashboard.css": "text/css; charset=utf-8", "/dashboard.js": "application/javascript; charset=utf-8"}
            if u.path in assets:
                self._send((static_dir / u.path[1:]).read_text(), assets[u.path])
                return
            if u.path == "/health":
                self._json({"status": "ok", "service": "multiedge", "execution": "research-and-paper"})
                return
            if u.path.startswith("/api/"):
                q = self._query()
                try:
                    if u.path == "/api/state":
                        result = service.state()
                    elif u.path == "/api/run":
                        result = service.store.get_run(q["id"])
                    elif u.path == "/api/stock":
                        result = service.stock(q["run_id"], q["ticker"])
                    elif u.path == "/api/paper":
                        result = service.paper_account(q["id"])
                    else:
                        raise KeyError("接口不存在")
                    self._json(result)
                except ValueError as e:
                    self._json({"error": str(e)}, 400)
                except (KeyError, StopIteration):
                    self._json({"error": "记录不存在"}, 404)
                return
            if u.path == "/analyze":
                self._send(FORM)
                return
            if u.path == "/report":
                q = urllib.parse.parse_qs(u.query)
                ticker = (q.get("ticker", [""])[0] or "").strip().upper()
                as_of = (q.get("as_of", [""])[0] or "").strip()
                run_id = q.get("run_id", [None])[0]
                if not ticker or (not as_of and not run_id):
                    self._send(FORM)
                    return
                try:
                    validate_ticker(ticker)
                    if run_id:
                        doc = service.snapshot(run_id, ticker)
                    else:
                        doc, _ = render_report(ticker, as_of, data_dir=data_dir, start=start)
                    self._send(doc)
                except (ValueError, KeyError, StopIteration) as e:
                    self._send(f"<h2>报告不可用</h2><p>{html.escape(str(e))}</p>", code=400)
                except Exception as e:  # 数据不足/参数错误时如实展示
                    self._send(f"<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>"
                               f"<title>生成失败</title></head><body style='font-family:sans-serif;"
                               f"background:#0b1118;color:#dbe4ee;padding:40px'>"
                               f"<h2>报告生成失败</h2><p>{type(e).__name__}: {html.escape(str(e))}</p>"
                               f"<p><a href='./'>返回</a></p></body></html>", code=500)
                return
            self._send("not found", ctype="text/plain; charset=utf-8", code=404)

    return Handler


def main():
    ap = argparse.ArgumentParser(prog="multiedge.app")
    ap.add_argument("--port", type=int, default=8321)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--state-dir", default="state")
    ap.add_argument("--start", default=None, help=f"数据起始日（默认 {LONG_START} 长历史）")
    args = ap.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), make_handler(args.data_dir, args.start, args.state_dir))
    print(f"multiedge.app 已启动：http://{args.host}:{args.port}/ （Ctrl+C 停止）")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
