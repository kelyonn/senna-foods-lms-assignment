"""
Tiny HTTP front for the mini-LMS (standard library only).

    POST /api/v1/webhooks/partner-app   partner app events (needs X-Signature)
    GET  /api/v1/holds                  deliveries on hold, with reasons
    GET  /api/v1/sap/results.csv        results file for SAP (new results only)

Run:  python3 server.py   (loads ../SAP_delivery_sample.csv, listens on :8080)
"""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from lms import LMS

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLE_FILE = os.path.join(HERE, "..", "SAP_delivery_sample.csv")
SECRET = os.environ.get("WEBHOOK_SECRET", "demo-secret")

# From the brief: the LMS knows every shop in the sample file except 0000100999.
KNOWN_SHOPS = ["100245", "100317", "100402", "100118", "100533", "100274",
               "100650", "100381", "100712", "100159", "100826"]


def make_handler(lms):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status, body, content_type="application/json"):
            data = body if isinstance(body, bytes) else json.dumps(body, indent=2).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            if self.path != "/api/v1/webhooks/partner-app":
                return self._send(404, {"error": "not found"})
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            status, response = lms.handle_webhook(body, self.headers.get("X-Signature", ""))
            self._send(status, response)

        def do_GET(self):
            if self.path == "/api/v1/holds":
                holds = {k: d["hold_reasons"] for k, d in lms.deliveries.items()
                         if d["status"] == "ON_HOLD"}
                return self._send(200, holds)
            if self.path == "/api/v1/sap/results.csv":
                return self._send(200, lms.export_sap_results().encode(), "text/csv")
            self._send(404, {"error": "not found"})

        def log_message(self, fmt, *args):
            pass  # keep the demo output clean

    return Handler


def build_lms():
    lms = LMS(KNOWN_SHOPS, SECRET)
    with open(SAMPLE_FILE, encoding="utf-8-sig") as f:
        lms.import_sap_file(f.read())
    return lms


def make_server(lms, port=8080):
    return ThreadingHTTPServer(("127.0.0.1", port), make_handler(lms))


if __name__ == "__main__":
    lms = build_lms()
    print("Mini-LMS listening on http://127.0.0.1:8080")
    make_server(lms).serve_forever()
