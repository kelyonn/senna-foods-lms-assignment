"""
The Task 2 test cases (Given / When / Then), as automated tests.
Run:  python3 -m unittest -v test_lms
"""
import copy
import csv
import io
import json
import os
import unittest

from lms import LMS, sign

HERE = os.path.dirname(os.path.abspath(__file__))
SECRET = "test-secret"
KNOWN_SHOPS = ["100245", "100317", "100402", "100118", "100533", "100274",
               "100650", "100381", "100712", "100159", "100826"]

# The sample webhook from the brief (section 5), used as the model for every event.
SAMPLE_EVENT = {
    "event_id": "evt_01J9ZQ4K7M2X",
    "event_type": "stop.result_saved",
    "sent_at": "2026-10-02T11:42:15+05:30",
    "trip_id": "TRP-BLR-20261002-07",
    "truck_number": "KA-01-AB-1234",
    "driver": {"name": "Ravi K", "phone": "+91-98450-00000"},
    "stop": {
        "sequence": 2,
        "sap_delivery_number": "0080001235",
        "shop_code": "0000100317",
        "result": "PART_DELIVERED",
        "items": [
            {"material": "FG-1001", "loaded_qty": 10, "delivered_qty": 10, "returned_qty": 0},
            {"material": "FG-3010", "loaded_qty": 40, "delivered_qty": 36, "returned_qty": 4,
             "return_reason": "DAMAGED"},
        ],
        "pod_photo_url": "https://partner.example.com/pod/TRP-BLR-20261002-07/2.jpg",
        "recorded_at": "2026-10-02T11:40:03+05:30",
        "location": {"lat": 12.9352, "lng": 77.6245},
    },
}


def event(event_id, delivery, shop, result, items, recorded_at="2026-10-02T11:40:03+05:30",
          sent_at="2026-10-02T11:42:15+05:30"):
    e = copy.deepcopy(SAMPLE_EVENT)
    e["event_id"], e["sent_at"] = event_id, sent_at
    e["stop"].update(sap_delivery_number=delivery, shop_code=shop, result=result,
                     items=items, recorded_at=recorded_at)
    return e


def item(material, loaded, delivered, returned, reason=None):
    i = {"material": material, "loaded_qty": loaded, "delivered_qty": delivered,
         "returned_qty": returned}
    if reason:
        i["return_reason"] = reason
    return i


class FlowTest(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(HERE, "..", "SAP_delivery_sample.csv"), encoding="utf-8-sig") as f:
            self.sap_file = f.read()
        self.lms = LMS(KNOWN_SHOPS, SECRET)
        self.report = self.lms.import_sap_file(self.sap_file)

    def send(self, evt, secret=SECRET):
        body = json.dumps(evt).encode()
        return self.lms.handle_webhook(body, sign(body, secret))

    def sap_rows(self):
        return list(csv.DictReader(io.StringIO(self.lms.export_sap_results())))

    # ---------------------------------------------------------------- happy paths
    def test_1_full_delivery(self):
        """Given 0080001234 (Sri Balaji, 24 CS FG-1001 + 12 CS FG-2003),
        when both items are fully delivered, then SAP gets 2 DELIVERED rows."""
        status, _ = self.send(event("evt_t1", "0080001234", "0000100245", "DELIVERED",
                                    [item("FG-1001", 24, 24, 0), item("FG-2003", 12, 12, 0)]))
        self.assertEqual(status, 200)
        rows = self.sap_rows()
        self.assertEqual([(r["sap_item_number"], r["delivered_qty"], r["returned_qty"],
                           r["return_reason"], r["delivery_result"]) for r in rows],
                         [("000010", "24", "0", "", "DELIVERED"),
                          ("000020", "12", "0", "", "DELIVERED")])

    def test_2_part_delivery_from_the_brief(self):
        """Given 0080001235 (Ganesh Mart, 40 CS FG-3010), when 4 come back damaged,
        then SAP gets delivered 36, returned 4, reason DAMAGED for that item."""
        status, _ = self.send(SAMPLE_EVENT)
        self.assertEqual(status, 200)
        rows = {r["material"]: r for r in self.sap_rows()}
        fg3010 = rows["FG-3010"]
        self.assertEqual((fg3010["delivered_qty"], fg3010["returned_qty"],
                          fg3010["return_reason"], fg3010["item_result"]),
                         ("36", "4", "DAMAGED", "PART_DELIVERED"))
        self.assertEqual(rows["FG-1001"]["item_result"], "DELIVERED")
        self.assertEqual(fg3010["delivery_result"], "PART_DELIVERED")
        # Codes go back in SAP format, with leading zeros and SAP's unit.
        self.assertEqual((fg3010["sap_delivery_number"], fg3010["sap_item_number"],
                          fg3010["shop_code"], fg3010["unit"]),
                         ("0080001235", "000020", "0000100317", "CS"))

    def test_3_shop_closed_full_return(self):
        """Given 0080001244 (Sai Ram, 10 CS FG-4005), when the shop is closed,
        then SAP gets delivered 0, returned 10, SHOP_CLOSED, result RETURNED."""
        status, _ = self.send(event("evt_t3", "0080001244", "0000100159", "RETURNED",
                                    [item("FG-4005", 10, 0, 10, "SHOP_CLOSED")]))
        self.assertEqual(status, 200)
        [row] = self.sap_rows()
        self.assertEqual((row["delivered_qty"], row["returned_qty"], row["return_reason"],
                          row["delivery_result"]), ("0", "10", "SHOP_CLOSED", "RETURNED"))

    def test_4_offline_driver_uses_recorded_at(self):
        """Given the phone is offline at Daily Needs (0080001241), when the result is
        recorded at 11:40 and synced at 14:05, then SAP gets delivered_at = 11:40."""
        status, _ = self.send(event("evt_t4", "0080001241", "0000100650", "DELIVERED",
                                    [item("FG-1001", 12, 12, 0), item("FG-4005", 4, 4, 0)],
                                    recorded_at="2026-10-02T11:40:00+05:30",
                                    sent_at="2026-10-02T14:05:00+05:30"))
        self.assertEqual(status, 200)
        self.assertTrue(all(r["delivered_at"] == "2026-10-02T11:40:00+05:30"
                            for r in self.sap_rows()))

    def test_4b_late_old_event_does_not_overwrite_newer_result(self):
        """An older event that syncs late must not replace a newer result."""
        newer = event("evt_new", "0080001244", "0000100159", "DELIVERED",
                      [item("FG-4005", 10, 10, 0)], recorded_at="2026-10-02T12:00:00+05:30")
        older = event("evt_old", "0080001244", "0000100159", "RETURNED",
                      [item("FG-4005", 10, 0, 10, "SHOP_CLOSED")],
                      recorded_at="2026-10-02T10:00:00+05:30")
        self.assertEqual(self.send(newer)[0], 200)
        self.assertEqual(self.send(older)[0], 200)
        [row] = self.sap_rows()
        self.assertEqual(row["delivery_result"], "DELIVERED")

    # ------------------------------------------------------------- failure paths
    def test_5_duplicate_event_is_ignored(self):
        """Given evt_01J9ZQ4K7M2X was processed, when the app sends it again,
        then the LMS says 200 but SAP still gets one record (36, not 72)."""
        self.assertEqual(self.send(SAMPLE_EVENT), (200, {"status": "accepted",
                                                         "event_id": "evt_01J9ZQ4K7M2X"}))
        status, body = self.send(SAMPLE_EVENT)
        self.assertEqual((status, body["status"]), (200, "duplicate ignored"))
        rows = self.sap_rows()
        self.assertEqual(len(rows), 2)  # two items, once each
        self.assertEqual(sum(int(r["delivered_qty"]) for r in rows), 46)

    def test_6_return_without_reason_is_rejected(self):
        """Given 40 CS FG-3010 loaded, when 4 are returned with no reason,
        then 422 and nothing goes to SAP until a valid result arrives."""
        bad = copy.deepcopy(SAMPLE_EVENT)
        del bad["stop"]["items"][1]["return_reason"]
        status, body = self.send(bad)
        self.assertEqual(status, 422)
        self.assertIn("return_reason", body["error"])
        self.assertEqual(self.sap_rows(), [])
        # The driver fixes it and the app sends a corrected event: now accepted.
        fixed = copy.deepcopy(SAMPLE_EVENT)
        fixed["event_id"] = "evt_fixed"
        self.assertEqual(self.send(fixed)[0], 200)

    def test_6b_quantities_that_do_not_add_up_are_rejected(self):
        """delivered 36 + returned 2 != loaded 40 -> 422."""
        bad = copy.deepcopy(SAMPLE_EVENT)
        bad["stop"]["items"][1]["returned_qty"] = 2
        status, body = self.send(bad)
        self.assertEqual(status, 422)
        self.assertIn("!= loaded 40", body["error"])

    def test_7_unknown_shop_is_held_then_released(self):
        """Given 0080001237 is for shop 0000100999 (not in the LMS), when the file is
        imported, then it is ON_HOLD, the other 10 good deliveries load, and once the
        shop is created a re-import releases it."""
        self.assertIn("0080001237", self.report["on_hold"])
        self.assertIn("UNKNOWN_SHOP", self.report["on_hold"]["0080001237"][0])
        self.assertEqual(len(self.report["loaded"]), 10)
        # A result for a held delivery is refused: it was never on a truck.
        status, _ = self.send(event("evt_t7", "0080001237", "0000100999", "DELIVERED",
                                    [item("FG-3010", 20, 20, 0)]))
        self.assertEqual(status, 422)
        # SAP team creates the shop -> next import releases the delivery.
        self.lms.add_shop("0000100999")
        report = self.lms.import_sap_file(self.sap_file)
        self.assertIn("0080001237", report["loaded"])
        self.assertEqual(self.lms.deliveries["0080001237"]["status"], "NEW")

    def test_8_missing_weight_is_held_not_guessed(self):
        """Given 0080001236 has a blank BRGEW, when imported, then it is ON_HOLD with
        MISSING_WEIGHT and the LMS does not fill in a weight."""
        self.assertIn("MISSING_WEIGHT", self.report["on_hold"]["0080001236"][0])
        delivery = self.lms.deliveries["0080001236"]
        self.assertIsNone(delivery["items"][10]["weight_kg"])
        self.assertIsNone(delivery["total_weight_kg"])

    def test_9_bad_signature_is_rejected(self):
        """An event not signed with the shared secret gets 401 and is not applied."""
        status, _ = self.send(SAMPLE_EVENT, secret="wrong-secret")
        self.assertEqual(status, 401)
        self.assertEqual(self.sap_rows(), [])

    def test_10_results_are_sent_to_sap_only_once(self):
        """A delivery already exported is not repeated in the next results file."""
        self.send(SAMPLE_EVENT)
        self.assertEqual(len(self.sap_rows()), 2)
        self.assertEqual(self.sap_rows(), [])

    def test_11_leading_zeros_survive_a_spreadsheet(self):
        """If Excel/Sheets stripped the zeros (80001234), the import pads them back."""
        stripped = self.sap_file.replace("0080001234", "80001234")
        lms = LMS(KNOWN_SHOPS, SECRET)
        report = lms.import_sap_file(stripped)
        self.assertIn("0080001234", report["loaded"])
        self.assertEqual(lms.deliveries["0080001234"]["total_weight_kg"], 27.6)


if __name__ == "__main__":
    unittest.main(verbosity=2)
