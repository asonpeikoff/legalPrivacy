"""Real-browser integration test against a local page (skipped if no browser/playwright)."""
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from legalprivacy.browser import BrowserTransport, browser_support  # noqa: E402
from legalprivacy.transports import Target  # noqa: E402

EVENTS = []
PAGE = ("<html><body style='margin:0'>" + "".join(f"<p style='height:300px'>para {i}</p>" for i in range(40))
        + "<script>let n=0,m=0;addEventListener('wheel',()=>n++);addEventListener('mousemove',()=>m++);"
          "addEventListener('touchstart',()=>n++);"
          "setInterval(()=>fetch('/ev?w='+n+'&m='+m+'&y='+Math.round(scrollY)),300);</script></body></html>")
SERP = ("<html><body><h1>results</h1><a id='r' href='/l/?uddg=http%3A%2F%2F127.0.0.1%3A{port}%2Fpage'>"
        "127.0.0.1 result</a><p style='height:3000px'></p></body></html>")


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def do_GET(self):
        if self.path.startswith("/ev"):
            EVENTS.append(self.path)
            body = b"ok"
        elif self.path.startswith("/serp"):
            body = SERP.format(port=self.server.server_address[1]).encode()
        elif self.path.startswith("/l/"):
            self.send_response(302)
            self.send_header("Location", "/page")
            self.end_headers()
            return
        else:
            body = PAGE.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body)


class Ident:
    id = 1
    accept_language = "en-US,en;q=0.9"


def test_real_browser_scrolls_moves_and_clicks_like_a_person():
    ok, _ = browser_support()
    if not ok:
        print("    (skipped: no browser)")
        return
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    import random
    tr = BrowserTransport(scale=lambda: 0.08, rng=random.Random(7))
    try:
        best = None
        for seed in range(6):                    # depth is random; take the deepest of a few visits
            tr.rng = random.Random(seed)
            r = tr.hit(Ident(), Target(url=base + "/page"))
            assert r.ok and "para 39" in r.html, r.error
            ys = [int(e.split("y=")[1]) for e in EVENTS if "y=" in e]
            best = max(best or 0, max(ys) if ys else 0)
        assert best > 300, f"page never scrolled (max scrollY {best})"
        assert any("w=0" not in e for e in EVENTS), "no wheel/touch events seen"
        # click-through from a results page
        tr.hit(Ident(), Target(url=base + "/serp", role="search", dwell=1))
        r = tr.hit(Ident(), Target(url=base + "/page", referer=base + "/serp", role="result", dwell=1))
        assert r.ok and r.detail and (r.detail.startswith("click") or r.detail.startswith("tap")), r.detail
        # after reading the result, going "back" to the results and clicking again also works
        r = tr.hit(Ident(), Target(url=base + "/page", referer=base + "/serp", role="result", dwell=1))
        assert r.ok and r.detail and (r.detail.startswith("click") or r.detail.startswith("tap")), r.detail
    finally:
        tr.close()
        srv.shutdown()
