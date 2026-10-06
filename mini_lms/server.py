"""
Tiny HTTP front for the mini-LMS (standard library only).

    POST /api/v1/webhooks/partner-app   partner app events (needs X-Signature)
    GET  /api/v1/holds                  deliveries on hold, with reasons
    GET  /api/v1/sap/results.csv        results file for SAP (new results only)

Run:  python3 server.py   (loads ../SAP_delivery_sample.csv, listens on :8080)
"""
import copy
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from lms import LMS, sign

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLE_FILE = os.path.join(HERE, "..", "SAP_delivery_sample.csv")
SECRET = os.environ.get("WEBHOOK_SECRET", "demo-secret")

# From the brief: the LMS knows every shop in the sample file except 0000100999.
KNOWN_SHOPS = ["100245", "100317", "100402", "100118", "100533", "100274",
               "100650", "100381", "100712", "100159", "100826"]


PAGE = """<!doctype html><meta charset=utf-8><title>Mini-LMS</title>
<style>body{font:14px system-ui;margin:24px auto;max-width:980px;padding:0 16px}
table{border-collapse:collapse;width:100%;margin:8px 0}td,th{border:1px solid #ccc;padding:4px 8px;text-align:left}
th{background:#f3f1ec}button{margin:2px;padding:6px 10px}pre{background:#f3f1ec;padding:10px;overflow:auto}
.ON_HOLD{background:#fbeeea}.PART_DELIVERED,.DELIVERED,.RETURNED,.SENT_TO_SAP{background:#eaf5ec}</style>
<h2>Mini-LMS</h2>
<p>Play the partner app (webhooks are signed and sent to the server):</p>
<button onclick=send('good')>Send sample result (Ganesh Mart, 4 damaged)</button>
<button onclick=send('dup')>Send same event again</button>
<button onclick=send('noreason')>Return with no reason</button>
<button onclick=send('badsig')>Wrong signature</button>
<button onclick=send('fixshop')>SAP team creates shop 100999</button>
<pre id=resp>Server response appears here.</pre>
<h3>Deliveries</h3><table id=d></table>
<h3>Results file for SAP (preview, not yet sent)</h3><pre id=r></pre>
<script>
async function send(k){const x=await fetch('/demo/'+k,{method:'POST'});
document.getElementById('resp').textContent=x.status+' '+await x.text();load()}
async function load(){const d=await (await fetch('/api/v1/deliveries')).json();
document.getElementById('d').innerHTML='<tr><th>Delivery</th><th>Shop</th><th>Items</th><th>Weight kg</th><th>Status</th><th>Why</th></tr>'+
d.map(x=>`<tr class=${x.status}><td>${x.delivery_number}</td><td>${x.shop_code} ${x.shop_name}</td><td>${x.items}</td><td>${x.total_weight_kg??'?'}</td><td>${x.status}</td><td>${x.hold_reasons.join('; ')}</td></tr>`).join('');
document.getElementById('r').textContent=await (await fetch('/api/v1/sap/results.csv?peek=1')).text()}
load()</script>"""

SAMPLE = {
    "event_id": "evt_01J9ZQ4K7M2X", "event_type": "stop.result_saved",
    "sent_at": "2026-10-02T11:42:15+05:30", "trip_id": "TRP-BLR-20261002-07",
    "truck_number": "KA-01-AB-1234",
    "stop": {"sequence": 2, "sap_delivery_number": "0080001235", "shop_code": "0000100317",
             "result": "PART_DELIVERED",
             "items": [{"material": "FG-1001", "loaded_qty": 10, "delivered_qty": 10, "returned_qty": 0},
                       {"material": "FG-3010", "loaded_qty": 40, "delivered_qty": 36,
                        "returned_qty": 4, "return_reason": "DAMAGED"}],
             "pod_photo_url": "https://partner.example.com/pod/TRP-BLR-20261002-07/2.jpg",
             "recorded_at": "2026-10-02T11:40:03+05:30"},
}


def make_handler(lms):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status, body, content_type="application/json"):
            data = body if isinstance(body, bytes) else json.dumps(body, indent=2).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _demo(self, kind):
            if kind == "fixshop":
                lms.add_shop("100999")
                with open(SAMPLE_FILE, encoding="utf-8-sig") as f:
                    return self._send(200, lms.import_sap_file(f.read()))
            evt = copy.deepcopy(SAMPLE)
            if kind == "noreason":
                evt["event_id"] = "evt_noreason"
                del evt["stop"]["items"][1]["return_reason"]
            body = json.dumps(evt).encode()
            secret = "wrong" if kind == "badsig" else SECRET
            status, response = lms.handle_webhook(body, sign(body, secret))
            self._send(status, response)

        def do_POST(self):
            if self.path.startswith("/demo/"):
                return self._demo(self.path[6:])
            if self.path != "/api/v1/webhooks/partner-app":
                return self._send(404, {"error": "not found"})
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            status, response = lms.handle_webhook(body, self.headers.get("X-Signature", ""))
            self._send(status, response)

        def do_GET(self):
            if self.path == "/":
                return self._send(200, PAGE.encode(), "text/html")
            if self.path == "/api/v1/deliveries":
                return self._send(200, [
                    {"delivery_number": k, "shop_code": d["shop_code"], "shop_name": d["shop_name"],
                     "items": len(d["items"]), "total_weight_kg": d["total_weight_kg"],
                     "status": d["status"], "hold_reasons": d["hold_reasons"]}
                    for k, d in lms.deliveries.items()])
            if self.path == "/api/v1/holds":
                holds = {k: d["hold_reasons"] for k, d in lms.deliveries.items()
                         if d["status"] == "ON_HOLD"}
                return self._send(200, holds)
            if self.path.startswith("/api/v1/sap/results.csv"):
                peek = "peek=1" in self.path   # preview without marking as sent
                return self._send(200, lms.export_sap_results(consume=not peek).encode(), "text/csv")
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
