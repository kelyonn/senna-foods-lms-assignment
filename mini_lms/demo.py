"""
End-to-end demo: starts the mini-LMS server, then plays the partner app,
sending real signed HTTP webhooks, and finally fetches the results file for SAP.

Run:  python3 demo.py
"""
import json
import threading
import urllib.error
import urllib.request

from lms import sign
from server import SECRET, build_lms, make_server
from test_lms import SAMPLE_EVENT

PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"


def post(event, secret=SECRET):
    body = json.dumps(event).encode()
    req = urllib.request.Request(f"{BASE}/api/v1/webhooks/partner-app", data=body,
                                 headers={"X-Signature": sign(body, secret),
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


def get(path):
    with urllib.request.urlopen(BASE + path) as r:
        return r.read().decode()


def step(title):
    print(f"\n=== {title}")


lms = build_lms()
server = make_server(lms, PORT)
threading.Thread(target=server.serve_forever, daemon=True).start()

step("1. SAP file imported: bad deliveries are on hold, the rest loaded")
print(get("/api/v1/holds"))

step("2. Partner app sends the sample webhook (Ganesh Mart, 4 damaged)")
print(post(SAMPLE_EVENT))

step("3. Same event again (phone retried after a timeout): duplicate ignored")
print(post(SAMPLE_EVENT))

step("4. Return with no reason: rejected, nothing reaches SAP")
bad = json.loads(json.dumps(SAMPLE_EVENT))
bad["event_id"] = "evt_bad_1"
bad["stop"]["items"][1].pop("return_reason")
print(post(bad))

step("5. Event signed with the wrong secret: rejected")
print(post(SAMPLE_EVENT, secret="not-the-shared-secret"))

step("6. Results file for SAP (one row per item)")
print(get("/api/v1/sap/results.csv"))

step("7. Asking again: already sent, so only the header comes back")
print(get("/api/v1/sap/results.csv"))

server.shutdown()
