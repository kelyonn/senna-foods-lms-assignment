"""
Read SAP_delivery_sample.csv, group rows into deliveries, and print each
delivery's shop, number of items and total weight.

Problem rows (missing weight, unknown shop, bad quantity) are flagged instead
of crashing the run. Usage:  python3 check_deliveries.py [path/to/file.csv]
"""
import csv
import sys
from collections import OrderedDict

# Assumption from the brief: the LMS knows every shop in the file except 0000100999.
# Codes are stored the LMS way (no leading zeros).
UNKNOWN_SHOPS = {"100999"}


def lms_code(value):
    """SAP codes come with leading zeros; the LMS stores them without."""
    return value.strip().lstrip("0") or "0"


def to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def main(path):
    deliveries = OrderedDict()

    with open(path, newline="", encoding="utf-8-sig") as f:
        # Read every column as text so leading zeros are never lost.
        for line_no, row in enumerate(csv.DictReader(f), start=2):
            # Pad back to SAP length in case a spreadsheet stripped the zeros.
            vbeln = row["VBELN"].strip().zfill(10)
            d = deliveries.setdefault(vbeln, {
                "shop_code": lms_code(row["KUNNR"]),
                "shop_name": row["NAME1"].strip(),
                "items": 0,
                "weight_kg": 0.0,
                "problems": [],
            })
            d["items"] += 1
            item = row["POSNR"].strip().lstrip("0")

            weight = to_float(row["BRGEW"])
            if weight is None:
                d["problems"].append(f"item {item}: missing weight (CSV line {line_no})")
            elif row["GEWEI"].strip().upper() != "KG":
                d["problems"].append(f"item {item}: weight unit '{row['GEWEI']}' is not KG")
            else:
                d["weight_kg"] += weight

            if to_float(row["LFIMG"]) is None:
                d["problems"].append(f"item {item}: missing quantity (CSV line {line_no})")

            if d["shop_code"] in UNKNOWN_SHOPS and "unknown shop" not in " ".join(d["problems"]):
                d["problems"].append(f"unknown shop {d['shop_code']} - not in LMS shop master")

    ok = flagged = 0
    print(f"{'DELIVERY':<11} {'SHOP':<7} {'SHOP NAME':<25} {'ITEMS':>5} {'WEIGHT_KG':>10}  STATUS")
    print("-" * 80)
    for vbeln, d in deliveries.items():
        status = "OK" if not d["problems"] else "FLAGGED"
        weight = f"{d['weight_kg']:.1f}" if not any("weight" in p for p in d["problems"]) else "?"
        print(f"{vbeln:<11} {d['shop_code']:<7} {d['shop_name']:<25} {d['items']:>5} {weight:>10}  {status}")
        for p in d["problems"]:
            print(f"{'':<11} -> {p}")
        ok += status == "OK"
        flagged += status == "FLAGGED"

    total_items = sum(d["items"] for d in deliveries.values())
    print("-" * 80)
    print(f"{len(deliveries)} deliveries, {total_items} item rows: {ok} OK, {flagged} flagged")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "SAP_delivery_sample.csv")
