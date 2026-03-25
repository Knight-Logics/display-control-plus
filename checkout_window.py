"""checkout_window.py — Stripe checkout in a native EdgeChromium popup.

Opens the given Stripe checkout URL inside an embedded pywebview window
(no OS browser).  A tiny HTTP server on port 8200 listens for the
success/cancel redirect that Stripe performs when the user finishes or
abandons checkout.

Usage (called as a subprocess by overlay.py):
    python checkout_window.py <checkout_url>

Writes ONE JSON line to stdout before exiting:
    {"ok": true}                            — success redirect received
    {"ok": false, "reason": "cancelled"}   — cancel redirect received
    {"ok": false, "reason": "closed"}      — user closed the window early
    {"ok": false, "reason": "error", "detail": "..."}
"""

import sys
import json
import threading
import socketserver
import http.server
import time


_SUCCESS_PATHS = ("/success",)
_CANCEL_PATHS  = ("/cancel",)

SUCCESS_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
  body{background:#0f1115;color:#d4dde9;font-family:'Segoe UI',sans-serif;
       display:flex;align-items:center;justify-content:center;height:100vh;margin:0;}
  .card{text-align:center;padding:2em;}
  .icon{font-size:3em;margin-bottom:.4em;}
  h2{font-size:1.3em;margin:0 0 .5em;}
  p{color:#9aa7b7;font-size:.9em;margin:0;}
</style></head><body>
<div class="card">
  <div class="icon">&#10003;</div>
  <h2>Payment complete!</h2>
  <p>Returning to Display Control+&hellip;</p>
</div>
</body></html>"""

CANCEL_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
  body{background:#0f1115;color:#d4dde9;font-family:'Segoe UI',sans-serif;
       display:flex;align-items:center;justify-content:center;height:100vh;margin:0;}
  .card{text-align:center;padding:2em;}
  .icon{font-size:3em;margin-bottom:.4em;color:#ff9098;}
  h2{font-size:1.3em;margin:0 0 .5em;}
  p{color:#9aa7b7;font-size:.9em;margin:0;}
</style></head><body>
<div class="card">
  <div class="icon">&#10007;</div>
  <h2>Checkout cancelled.</h2>
  <p>Closing window&hellip;</p>
</div>
</body></html>"""


def main():
    if len(sys.argv) < 2:
        print(json.dumps({"ok": False, "reason": "no_url"}), flush=True)
        sys.exit(1)

    checkout_url = sys.argv[1]
    result = {"ok": False, "reason": "closed"}
    shutdown_event = threading.Event()
    window_ref = [None]

    # ── Tiny HTTP server that catches the Stripe success/cancel redirect ──────

    class _Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # silence access logs

        def do_GET(self):
            path = self.path.split("?")[0]
            if path in _SUCCESS_PATHS:
                result.update({"ok": True})
                html = SUCCESS_HTML.encode("utf-8")
            elif path in _CANCEL_PATHS:
                result.update({"ok": False, "reason": "cancelled"})
                html = CANCEL_HTML.encode("utf-8")
            else:
                self.send_response(404)
                self.end_headers()
                return

            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)

            # Close the webview after a brief delay so the HTML renders
            def _close_later():
                time.sleep(1.8)
                shutdown_event.set()
                try:
                    srv.shutdown()
                except Exception:
                    pass
            threading.Thread(target=_close_later, daemon=True).start()

        def do_OPTIONS(self):
            self.send_response(200)
            self.end_headers()

    srv = socketserver.TCPServer(("127.0.0.1", 8200), _Handler)
    srv.allow_reuse_address = True
    srv_thread = threading.Thread(target=srv.serve_forever, daemon=True)
    srv_thread.start()

    # ── Watch for shutdown signal and destroy the webview ────────────────────

    def _watchdog():
        shutdown_event.wait(timeout=660)  # 11-min hard limit
        try:
            if window_ref[0] is not None:
                window_ref[0].destroy()
        except Exception:
            pass

    threading.Thread(target=_watchdog, daemon=True).start()

    # ── pywebview window ──────────────────────────────────────────────────────

    try:
        import webview  # type: ignore

        win = webview.create_window(
            "Complete Purchase — Display Control+",
            checkout_url,
            width=520,
            height=740,
            resizable=True,
        )
        window_ref[0] = win
        webview.start(gui="edgechromium", debug=False, private_mode=True)
    except Exception as exc:
        result.update({"ok": False, "reason": "error", "detail": str(exc)})

    # ── Emit result and exit ─────────────────────────────────────────────────

    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
