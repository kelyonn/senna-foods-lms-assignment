"""
Mini-LMS: a small, working model of the Senna Foods flow.

    SAP delivery file  ->  import_sap_file()   (validate, hold bad deliveries)
    partner app event  ->  handle_webhook()    (signature, duplicates, checks)
    results for SAP    ->  export_sap_results() (one row per item, SAP format)

Everything is kept in memory so the logic is easy to read and test.
Python standard library only.
"""
import csv
import hashlib
import hmac
import io
import json
from datetime import datetime

UNIT_MAP = {"CS": "CASE", "ST": "PIECE", "PC": "PIECE"}      # SAP unit -> LMS unit
SAP_UNIT = {"CASE": "CS", "PIECE": "ST"}                       # LMS unit -> SAP unit
RETURN_REASONS = {"DAMAGED", "SHOP_CLOSED", "REFUSED", "SHORT_LOADED",
                  "EXPIRED", "WRONG_ITEM", "NOT_ATTEMPTED"}
FINAL_RESULTS = {"DELIVERED", "PART_DELIVERED", "RETURNED"}


def lms_code(value):
    """SAP codes have leading zeros; the LMS stores them without (0000100245 -> 100245)."""
    return str(value).strip().lstrip("0") or "0"


def sign(body: bytes, secret: str) -> str:
    """X-Signature = HMAC-SHA256 of the raw request body, as hex."""
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class LMS:
    def __init__(self, known_shops, webhook_secret):
        self.shops = {lms_code(s) for s in known_shops}
        self.secret = webhook_secret
        self.deliveries = {}          # "0080001234" -> delivery dict
        self.processed_events = set() # event_ids already applied (duplicate check)
        self.exported = set()         # delivery numbers already sent to SAP

    # ------------------------------------------------------------------ masters
    def add_shop(self, shop_code):
        self.shops.add(lms_code(shop_code))

    # --------------------------------------------------------- 1. SAP -> LMS
    def import_sap_file(self, csv_text):
        """Load the nightly SAP file. Bad deliveries go ON_HOLD; the rest load.
        Returns an exception report: {"loaded": [...], "on_hold": {vbeln: [reasons]}}."""
        grouped = {}
        # Every column is read as text, so leading zeros are never lost.
        for row in csv.DictReader(io.StringIO(csv_text)):
            vbeln = row["VBELN"].strip().zfill(10)
            grouped.setdefault(vbeln, []).append(row)

        report = {"loaded": [], "on_hold": {}, "skipped": []}
        for vbeln, rows in grouped.items():
            existing = self.deliveries.get(vbeln)
            if existing and existing["status"] not in ("NEW", "ON_HOLD"):
                # Already planned or on a trip: a re-send must not change it silently.
                report["skipped"].append(vbeln)
                continue

            delivery, problems = self._build_delivery(vbeln, rows)
            delivery["status"] = "ON_HOLD" if problems else "NEW"
            delivery["hold_reasons"] = problems
            self.deliveries[vbeln] = delivery
            if problems:
                report["on_hold"][vbeln] = problems
            else:
                report["loaded"].append(vbeln)
        return report

    def _build_delivery(self, vbeln, rows):
        problems = []
        first = rows[0]
        shop_code = lms_code(first["KUNNR"])
        if shop_code not in self.shops:
            problems.append(f"UNKNOWN_SHOP: {shop_code} is not in the LMS shop master")

        items = {}
        for row in rows:
            line = int(lms_code(row["POSNR"]))
            qty_text = row["LFIMG"].strip()
            qty = int(float(qty_text)) if qty_text else None
            if qty is None or qty <= 0:
                problems.append(f"BAD_QUANTITY: item {line} quantity '{qty_text}'")

            unit = UNIT_MAP.get(row["VRKME"].strip().upper())
            if unit is None:
                problems.append(f"UNKNOWN_UNIT: item {line} unit '{row['VRKME']}'")

            weight_kg = None
            weight_text = row["BRGEW"].strip()
            weight_unit = row["GEWEI"].strip().upper()
            if not weight_text:
                problems.append(f"MISSING_WEIGHT: item {line} has no weight")  # never guessed
            elif weight_unit == "KG":
                weight_kg = float(weight_text)
            elif weight_unit == "G":
                weight_kg = float(weight_text) / 1000
            else:
                problems.append(f"UNKNOWN_WEIGHT_UNIT: item {line} unit '{weight_unit}'")

            items[line] = {
                "line_number": line,
                "product_code": row["MATNR"].strip(),
                "product_name": row["ARKTX"].strip(),
                "quantity": qty,
                "unit": unit,
                "weight_kg": weight_kg,
            }

        weights = [i["weight_kg"] for i in items.values()]
        delivery = {
            "delivery_number": vbeln,
            "shop_code": shop_code,
            "shop_name": first["NAME1"].strip(),
            "planned_date": first["WADAT"].strip(),
            "total_weight_kg": round(sum(weights), 3) if None not in weights else None,
            "items": items,
            "result": None,
        }
        return delivery, problems

    # ------------------------------------------------- 2. partner app -> LMS
    def handle_webhook(self, body: bytes, signature: str):
        """Returns (http_status, response_dict). Only a 2xx tells the app to stop retrying."""
        if not signature or not hmac.compare_digest(sign(body, self.secret), signature):
            return 401, {"error": "bad signature"}

        try:
            event = json.loads(body)
            event_id = event["event_id"]
            event_type = event["event_type"]
        except (ValueError, KeyError):
            return 400, {"error": "malformed event"}

        # Same event twice (app retried after a timeout): say OK, change nothing.
        if event_id in self.processed_events:
            return 200, {"status": "duplicate ignored", "event_id": event_id}

        if event_type == "stop.result_saved":
            error = self._apply_stop_result(event)
            if error:
                # Not marked as processed: the driver fixes it and the app sends a new event.
                return 422, {"error": error}

        # trip.started, stop.arrived, trip.completed, ... are accepted and logged.
        self.processed_events.add(event_id)
        return 200, {"status": "accepted", "event_id": event_id}

    def _apply_stop_result(self, event):
        stop = event.get("stop", {})
        vbeln = str(stop.get("sap_delivery_number", "")).zfill(10)
        delivery = self.deliveries.get(vbeln)
        if delivery is None:
            return f"unknown delivery {vbeln}"
        if delivery["status"] == "ON_HOLD":
            return f"delivery {vbeln} is on hold and was never dispatched"
        if lms_code(stop.get("shop_code", "")) != delivery["shop_code"]:
            return f"shop {stop.get('shop_code')} does not match delivery {vbeln}"

        result = stop.get("result")
        if result not in FINAL_RESULTS:
            return f"result must be one of {sorted(FINAL_RESULTS)}"

        by_product = {i["product_code"]: i for i in delivery["items"].values()}
        lines = {}
        for it in stop.get("items", []):
            item = by_product.get(it.get("material"))
            if item is None:
                return f"material {it.get('material')} is not in delivery {vbeln}"
            loaded, delivered, returned = it["loaded_qty"], it["delivered_qty"], it["returned_qty"]
            if min(loaded, delivered, returned) < 0 or delivered + returned != loaded:
                return f"{item['product_code']}: delivered {delivered} + returned {returned} != loaded {loaded}"
            reason = it.get("return_reason")
            if returned > 0 and reason not in RETURN_REASONS:
                return f"{item['product_code']}: returned {returned} needs a valid return_reason"
            lines[item["line_number"]] = {"loaded": loaded, "delivered": delivered,
                                          "returned": returned,
                                          "reason": reason if returned > 0 else None}

        if set(lines) != set(delivery["items"]):
            return f"every item of delivery {vbeln} needs a result"
        if result != _overall_result(lines.values()):
            return f"result {result} does not match the quantities ({_overall_result(lines.values())})"

        recorded_at = datetime.fromisoformat(stop["recorded_at"])
        current = delivery["result"]
        if current and current["recorded_at"] >= recorded_at:
            return None  # an older event arrived late: keep the newer result, still say OK

        delivery["result"] = {
            "result": result,
            "lines": lines,
            "recorded_at": recorded_at,                # when the driver saved it (sent to SAP)
            "sent_at": event.get("sent_at"),           # when the phone sent it (delay check only)
            "trip_id": event.get("trip_id"),
            "truck_number": event.get("truck_number"),
            "pod_photo_url": stop.get("pod_photo_url"),
        }
        delivery["status"] = result
        return None

    # --------------------------------------------------------- 3. LMS -> SAP
    def export_sap_results(self):
        """CSV for SAP: one row per item, codes padded back to SAP format.
        Only deliveries not exported before are included, so a late result goes in the next file."""
        out = io.StringIO()
        fields = ["sap_delivery_number", "sap_item_number", "shop_code", "material",
                  "planned_qty", "delivered_qty", "returned_qty", "unit", "return_reason",
                  "item_result", "delivery_result", "delivered_at", "trip_id",
                  "truck_number", "pod_photo_url"]
        writer = csv.DictWriter(out, fieldnames=fields, lineterminator="\n")
        writer.writeheader()

        for vbeln, d in self.deliveries.items():
            res = d["result"]
            if res is None or vbeln in self.exported:
                continue
            for line, item in sorted(d["items"].items()):
                r = res["lines"][line]
                writer.writerow({
                    "sap_delivery_number": vbeln,
                    "sap_item_number": str(line).zfill(6),
                    "shop_code": d["shop_code"].zfill(10),
                    "material": item["product_code"],
                    "planned_qty": item["quantity"],
                    "delivered_qty": r["delivered"],
                    "returned_qty": r["returned"],
                    "unit": SAP_UNIT[item["unit"]],
                    "return_reason": r["reason"] or "",
                    "item_result": _overall_result([r]),
                    "delivery_result": res["result"],
                    "delivered_at": res["recorded_at"].isoformat(),
                    "trip_id": res["trip_id"],
                    "truck_number": res["truck_number"],
                    "pod_photo_url": res["pod_photo_url"],
                })
            self.exported.add(vbeln)
            d["status"] = "SENT_TO_SAP"
        return out.getvalue()


def _overall_result(lines):
    lines = list(lines)
    if all(l["returned"] == 0 for l in lines):
        return "DELIVERED"
    if all(l["delivered"] == 0 for l in lines):
        return "RETURNED"
    return "PART_DELIVERED"
