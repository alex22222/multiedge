"""本地交互界面：127.0.0.1 上的最小 HTTP 服务，渲染与静态报告完全相同的页面。

用法：
  .venv/bin/python -m multiedge.app [--port 8321]
只监听 127.0.0.1；页面提供「股票代码 + 截止日期」输入框，提交后渲染同一份报告
（数字全部来自 engine.analyze()，与 multiedge.report 一致）。
"""
import argparse
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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
</style></head><body><form action="/report" method="get">
<div><label>股票代码（如 GOOGL / NVDA）</label><input name="ticker" value="GOOGL" required></div>
<div><label>截止日期（YYYY-MM-DD）</label><input name="as_of" value="2025-08-01" required></div>
<button type="submit">生成报告</button>
<p class="note">只监听 127.0.0.1；数据来自 MarketWatch 缓存/在线获取；结果不构成投资建议。</p>
</form></body></html>"""


def make_handler(data_dir="data", start=None):
    start = start or LONG_START
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # 静默
            pass

        def _send(self, body, ctype="text/html; charset=utf-8", code=200):
            data = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            if u.path in ("/", "/index.html"):
                self._send(FORM)
                return
            if u.path == "/report":
                q = urllib.parse.parse_qs(u.query)
                ticker = (q.get("ticker", [""])[0] or "").strip().upper()
                as_of = (q.get("as_of", [""])[0] or "").strip()
                if not ticker or not as_of:
                    self._send(FORM)
                    return
                try:
                    doc, _ = render_report(ticker, as_of, data_dir=data_dir, start=start)
                    self._send(doc)
                except Exception as e:  # 数据不足/参数错误时如实展示
                    self._send(f"<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>"
                               f"<title>生成失败</title></head><body style='font-family:sans-serif;"
                               f"background:#0b1118;color:#dbe4ee;padding:40px'>"
                               f"<h2>报告生成失败</h2><p>{type(e).__name__}: {e}</p>"
                               f"<p><a href='/'>返回</a></p></body></html>", code=500)
                return
            self._send("not found", ctype="text/plain; charset=utf-8", code=404)

    return Handler


def main():
    ap = argparse.ArgumentParser(prog="multiedge.app")
    ap.add_argument("--port", type=int, default=8321)
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--start", default=None, help=f"数据起始日（默认 {LONG_START} 长历史）")
    args = ap.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(args.data_dir, args.start))
    print(f"multiedge.app 已启动：http://127.0.0.1:{args.port}/ （仅本机；Ctrl+C 停止）")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
