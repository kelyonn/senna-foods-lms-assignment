# Mini-LMS: the design from the assignment, running

A small working model of the Senna Foods flow (SAP → LMS → partner app → SAP).
Python 3 standard library only. Nothing to install.

| File | What it does |
|---|---|
| `lms.py` | The logic: import the SAP file, handle webhooks, export results for SAP |
| `server.py` | Tiny HTTP server: `POST /api/v1/webhooks/partner-app`, `GET /api/v1/holds`, `GET /api/v1/sap/results.csv` |
| `test_lms.py` | The Task 2 Given/When/Then test cases as automated tests (13 tests) |
| `demo.py` | Starts the server and plays the partner app with real signed HTTP requests |

## Run

```bash
python3 -m unittest -v test_lms   # 13 tests
python3 demo.py                   # end-to-end walk-through
python3 server.py                 # run the server on :8080
```

## Rules it enforces

- **Bad deliveries are held, not dropped.** Unknown shop, missing weight, bad quantity or unit → `ON_HOLD` with a reason; the other deliveries still load. Weights are never guessed.
- **Codes stay text.** Delivery numbers keep leading zeros, shop codes and line numbers lose them, and results go back to SAP padded again. If a spreadsheet stripped the zeros, the import pads them back.
- **Webhooks:** the `X-Signature` (HMAC-SHA256 of the body) is checked → `401` if wrong. A repeated `event_id` → `200`, nothing changes. Quantities that don't add up, or a return with no reason → `422`. Only a saved event gets a 2xx, so the app keeps retrying otherwise.
- **Late events:** results are ordered by `recorded_at`, so an old event can't overwrite a newer one. SAP gets `recorded_at`, not `sent_at`.
- **Results go to SAP per item**, in SAP's format, and each delivery is exported once.

See `test_output.txt` and `demo_output.txt` for sample runs.
