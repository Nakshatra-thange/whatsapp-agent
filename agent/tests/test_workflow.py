"""
End-to-end tests for order -> shop delivery -> bill + QR -> verified payment.

Claude (agent.extract_customer_events) and Kapso/QR sending are mocked.
Any real HTTP call fails the test. Run from the agent/ directory:

    python -m unittest discover -s tests -v
"""
import importlib
import os
import shutil
import sys
import tempfile
import threading
import unittest
from decimal import Decimal
from pathlib import Path
from unittest import mock

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

ADMIN = {"x-shop-admin-token": "test-admin-token"}
CUSTOMER = "919999000001"
OTHER = "919999000002"

os.environ["SHOP_ADMIN_TOKEN"] = ADMIN["x-shop-admin-token"]

from fastapi.testclient import TestClient  # noqa: E402

import webhook  # noqa: E402
from src import db, normalizer, notify, payments  # noqa: E402


def ev(type_, **kw):
    base = {"type": type_, "date": "2026-10-10", "items": None, "from_item": None,
            "to_item": None, "amount": None, "note": None}
    base.update(kw)
    return base


def item(name, qty, unit, unclear=False, reason=None):
    return {"item": name, "qty": qty, "unit": unit,
            "needs_clarification": unclear, "reason": reason}


# What the (mocked) Claude extractor returns for each customer message.
EXTRACTIONS = {
    "Bhaiya, 2 kilo aloo dena": [ev("ORDER", items=[item("aloo", 2, "kilo")])],
    "4 kg cheeni bhejna": [ev("ORDER", items=[item("cheeni", 4, "kg")])],
    "1 kg pyaz bhi chahiye": [ev("ORDER", items=[item("onion", 1, "kg")])],
    "2 kg dragonfruit dena": [ev("ORDER", items=[item("dragonfruit", 2, "kg")])],
    "thoda aloo bhej do": [ev("ORDER", items=[item("aloo", None, None, True, "quantity not stated")])],
    "bhaiya delivery done": [ev("DELIVERED")],
    "kal de dunga": [ev("PROMISE", amount=60)],
    "60 gpay kar diya": [ev("PAYMENT", amount=60)],
    "aloo aaya hi nahi": [ev("DISPUTE", note="aloo did not arrive")],
}


class Recorder:
    """Stands in for Kapso + QR generation and records every call in order."""

    def __init__(self):
        self.calls = []
        self.fail_text = 0
        self.fail_image = 0

    def text(self, to, body):
        if self.fail_text:
            self.fail_text -= 1
            raise RuntimeError("Kapso API error 500")
        self.calls.append(("text", to, body))
        return {"messages": [{"id": f"wamid.{len(self.calls)}"}]}

    def image(self, to, image_path, caption):
        if self.fail_image:
            self.fail_image -= 1
            raise RuntimeError("QR send failed: 500")
        self.calls.append(("image", to, image_path, caption))
        return {"messages": [{"id": f"wamid.{len(self.calls)}"}]}

    def qr(self, amount, invoice_ref):
        self.calls.append(("qr", Decimal(str(amount)), invoice_ref))
        return {"qr_path": f"/fake/{invoice_ref}.png", "amount": f"{amount:.2f}",
                "upi_link": "upi://pay?fake"}

    def of(self, kind):
        return [c for c in self.calls if c[0] == kind]


def no_network(*args, **kwargs):
    raise AssertionError("Tests must not make real HTTP requests")


class WorkflowTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["SHOP_DB_PATH"] = os.path.join(self.tmp, "shop.db")
        self.rec = Recorder()
        self.extract = mock.Mock(side_effect=lambda body, date, history="": EXTRACTIONS.get(body, []))

        patches = [
            mock.patch("requests.post", no_network),
            mock.patch("agent.extract_customer_events", self.extract),
            mock.patch("src.notify.send_whatsapp_message", self.rec.text),
            mock.patch("src.notify.send_whatsapp_image", self.rec.image),
            mock.patch("src.notify.generate_payment_qr", self.rec.qr),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.client = TestClient(webhook.app)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- helpers ----

    def say(self, body, message_id, phone=CUSTOMER):
        return self.client.post(
            "/webhook",
            headers={"x-webhook-event": "whatsapp.message.received"},
            json={"message": {
                "id": message_id, "from": f"+{phone}", "type": "text",
                "timestamp": "1791600000", "text": {"body": body},
                "kapso": {"direction": "inbound"},
            }},
        )

    def order(self, body="Bhaiya, 2 kilo aloo dena", message_id="m-order", phone=CUSTOMER):
        r = self.say(body, message_id, phone)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(len(r.json()["orders_created"]), 1, r.json())
        return r.json()["orders_created"][0]

    def deliver(self, order_id, **extra):
        return self.client.post("/admin/confirm-delivery", headers=ADMIN,
                                json={"order_id": order_id, **extra})

    def pay(self, amount, reference, phone=CUSTOMER, **extra):
        return self.client.post("/admin/confirm-payment", headers=ADMIN, json={
            "customer": phone, "amount": amount, "reference": reference, **extra})

    def ledger(self, phone=CUSTOMER):
        r = self.client.get(f"/admin/customers/{phone}/ledger", headers=ADMIN)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def qr_amounts(self):
        return [c[1] for c in self.rec.of("qr")]

    # ---- 1 ----
    def test_order_is_stored_without_invoice_or_qr(self):
        order_id = self.order()

        details = self.client.get(f"/admin/orders/{order_id}", headers=ADMIN).json()
        self.assertEqual(details["status"], "pending")
        self.assertEqual(details["items"], [{"item": "aloo", "qty": "2", "unit": "kg"}])
        self.assertIsNone(details["invoice"])

        reply = self.rec.of("text")[-1][2]
        self.assertIn("order note kar liya hai: 2 kg aloo", reply)
        self.assertIn(order_id, reply)
        self.assertEqual(self.rec.of("qr"), [])
        self.assertEqual(self.rec.of("image"), [])
        self.assertEqual(self.ledger()["invoices"], [])

    # ---- 2 ----
    def test_customer_saying_delivered_is_not_delivery(self):
        order_id = self.order()
        r = self.say("bhaiya delivery done", "m-delivered")
        self.assertEqual(r.status_code, 200)

        details = self.client.get(f"/admin/orders/{order_id}", headers=ADMIN).json()
        self.assertEqual(details["status"], "pending")
        self.assertIsNone(details["invoice"])
        self.assertEqual(self.rec.of("image"), [])
        claims = self.ledger()["unverified_claims"]
        self.assertEqual([c["kind"] for c in claims], ["DELIVERY_CLAIM"])

    # ---- 3 ----
    def test_shop_confirmation_bills_once_and_sends_correct_qr(self):
        order_id = self.order()
        r = self.deliver(order_id, customer=f"+{CUSTOMER}")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["status"], "confirmed")
        self.assertEqual(Decimal(body["bill_amount"]), Decimal("60.00"))  # 2 kg x ₹30
        self.assertEqual(body["notifications"], {"bill_qr": "sent", "bill_text": "sent"})

        self.assertEqual(self.qr_amounts(), [Decimal("60.00")])
        self.assertEqual(self.rec.of("qr")[0][2], body["invoice_id"])

        kinds = [c[0] for c in self.rec.calls if c[0] in ("text", "image")]
        self.assertEqual(kinds[-2:], ["image", "text"])  # QR, then the bill text
        self.assertEqual(self.rec.of("image")[0][1], CUSTOMER)
        bill = self.rec.of("text")[-1][2]
        self.assertIn("Upar bheje gaye UPI QR", bill)
        self.assertIn("Bill amount: ₹60", bill)
        self.assertIn("Pehle verified payment: ₹0", bill)
        self.assertIn("Abhi baaki: ₹60", bill)

        led = self.ledger()
        self.assertEqual(len(led["invoices"]), 1)
        self.assertEqual(led["payment_status"], "unpaid")

    # ---- 4 ----
    def test_unknown_item_or_vague_quantity_is_not_guessed(self):
        for body, mid in [("2 kg dragonfruit dena", "m-unknown"), ("thoda aloo bhej do", "m-vague")]:
            r = self.say(body, mid)
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json()["orders_created"], [])
            self.assertIn("confirm kar dijiye", self.rec.of("text")[-1][2])

        led = self.ledger()
        self.assertEqual(led["orders"], [])
        self.assertEqual(led["invoices"], [])
        self.assertEqual(self.rec.of("qr"), [])

    # ---- 5 ----
    def test_confirming_twice_does_not_bill_twice(self):
        order_id = self.order()
        first = self.deliver(order_id)
        second = self.deliver(order_id)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["status"], "already_confirmed")
        self.assertEqual(first.json()["invoice_id"], second.json()["invoice_id"])
        self.assertEqual(len(self.ledger()["invoices"]), 1)
        self.assertEqual(len(self.rec.of("image")), 1)

    # ---- 6 ----
    def test_same_kapso_message_twice_is_processed_once(self):
        self.order(message_id="m-dup")
        again = self.say("Bhaiya, 2 kilo aloo dena", "m-dup")
        self.assertEqual(again.json(), {"status": "duplicate"})

        self.say("60 gpay kar diya", "m-claim")
        self.say("60 gpay kar diya", "m-claim")

        led = self.ledger()
        self.assertEqual(len(led["orders"]), 1)
        self.assertEqual(len(led["unverified_claims"]), 1)
        self.assertEqual(self.extract.call_count, 2)

    # ---- 7 ----
    def test_partial_payments_reduce_balance(self):
        order_id = self.order("4 kg cheeni bhejna", "m-sugar")
        self.assertEqual(Decimal(self.deliver(order_id).json()["bill_amount"]), Decimal("200"))

        r = self.pay(50, "UPI-1")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(Decimal(r.json()["remaining_balance"]), Decimal("150"))

        r = self.pay(100, "UPI-2")
        self.assertEqual(Decimal(r.json()["remaining_balance"]), Decimal("50"))
        self.assertEqual(Decimal(r.json()["total_verified_payments"]), Decimal("150"))
        self.assertEqual(r.json()["payment_status"], "partial")

        # Bill QR for 200, then a QR for what was still owed after each payment.
        self.assertEqual(self.qr_amounts(), [Decimal("200"), Decimal("150"), Decimal("50")])
        receipt = self.rec.of("text")[-1][2]
        self.assertIn("₹100 ka payment verify ho gaya", receipt)
        self.assertIn("Abhi baaki: ₹50", receipt)

    # ---- 8 ----
    def test_promise_is_not_payment(self):
        self.deliver(self.order())
        self.say("kal de dunga", "m-promise")

        led = self.ledger()
        self.assertEqual(Decimal(led["total_verified_payments"]), 0)
        self.assertEqual(Decimal(led["remaining_balance"]), Decimal("60"))
        self.assertEqual(led["payments"], [])
        self.assertEqual(led["unverified_claims"][0]["kind"], "PROMISE")

    # ---- 9 ----
    def test_payment_claim_is_not_verified(self):
        self.deliver(self.order())
        self.say("60 gpay kar diya", "m-paid-claim")

        led = self.ledger()
        self.assertEqual(led["payments"], [])
        self.assertEqual(Decimal(led["remaining_balance"]), Decimal("60"))
        claim = led["unverified_claims"][0]
        self.assertEqual((claim["kind"], claim["status"], claim["amount_paise"]),
                         ("PAYMENT_CLAIM", "unverified", 6000))
        self.assertIn("verify karne ke baad", self.rec.of("text")[-1][2])

    # ---- 10 ----
    def test_full_payment_clears_balance_and_sends_no_new_qr(self):
        order_id = self.order()
        self.deliver(order_id)
        r = self.pay("60.00", "UPI-FULL")

        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(Decimal(r.json()["remaining_balance"]), 0)
        self.assertEqual(r.json()["payment_status"], "paid")
        self.assertEqual(r.json()["notifications"], {"payment_text": "sent", "payment_qr": "skipped"})
        self.assertEqual(self.qr_amounts(), [Decimal("60")])  # only the original bill QR
        self.assertIn("poora hisaab clear", self.rec.of("text")[-1][2])

        # Re-confirming the paid order sends nothing new.
        self.assertEqual(self.deliver(order_id).json()["status"], "already_confirmed")
        self.assertEqual(len(self.rec.of("image")), 1)

        # Paying the same reference again is not double counted.
        again = self.pay("60.00", "UPI-FULL")
        self.assertEqual(again.json()["status"], "already_recorded")
        self.assertEqual(Decimal(self.ledger()["total_verified_payments"]), Decimal("60"))

    # ---- 11 ----
    def test_state_survives_restart(self):
        order_id = self.order(message_id="m-before-restart")
        self.deliver(order_id)
        self.pay(20, "UPI-R1")

        importlib.reload(webhook)  # fresh app object, same database file
        self.client = TestClient(webhook.app)

        led = self.ledger()
        self.assertEqual(led["orders"][0]["order_id"], order_id)
        self.assertEqual(Decimal(led["total_billed"]), Decimal("60"))
        self.assertEqual(Decimal(led["total_verified_payments"]), Decimal("20"))
        self.assertEqual(Decimal(led["remaining_balance"]), Decimal("40"))
        self.assertEqual(self.say("Bhaiya, 2 kilo aloo dena", "m-before-restart").json(),
                         {"status": "duplicate"})
        self.assertEqual(self.deliver(order_id).json()["status"], "already_confirmed")

    # ---- 12 ----
    def test_delivery_affects_only_the_named_order(self):
        first = self.order("Bhaiya, 2 kilo aloo dena", "m-a")
        second = self.order("1 kg pyaz bhi chahiye", "m-b")

        r = self.deliver(second)
        self.assertEqual(Decimal(r.json()["bill_amount"]), Decimal("35"))

        a = self.client.get(f"/admin/orders/{first}", headers=ADMIN).json()
        b = self.client.get(f"/admin/orders/{second}", headers=ADMIN).json()
        self.assertEqual((a["status"], a["invoice"]), ("pending", None))
        self.assertEqual(b["status"], "delivered")
        self.assertEqual(self.qr_amounts(), [Decimal("35")])

    # ---- 13 ----
    def test_invalid_order_id_or_wrong_customer(self):
        r = self.deliver("ORD-DOESNOTEXIST")
        self.assertEqual(r.status_code, 404)

        order_id = self.order()
        r = self.deliver(order_id, customer=OTHER)
        self.assertEqual(r.status_code, 409)

        self.assertEqual(self.ledger()["invoices"], [])
        self.assertEqual(self.rec.of("qr"), [])
        self.assertEqual(self.rec.of("image"), [])

    # ---- 14 ----
    def test_disputed_invoice_gets_no_automatic_qr(self):
        order_id = self.order()
        self.rec.fail_image = 1
        self.assertEqual(self.deliver(order_id).status_code, 502)  # QR did not go out

        self.say("aloo aaya hi nahi", "m-dispute")
        led = self.ledger()
        self.assertTrue(led["invoices"][0]["disputed"])
        self.assertEqual(led["payment_status"], "disputed")
        self.assertTrue(led["review_flags"])

        r = self.deliver(order_id)  # retry while disputed
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["notifications"]["bill_qr"], "skipped")
        self.assertEqual(self.rec.of("image"), [])

        # Shop verifies a partial payment on the disputed invoice: still no QR.
        r = self.pay(10, "UPI-D1", invoice_id=led["invoices"][0]["invoice_id"])
        self.assertEqual(r.json()["notifications"]["payment_qr"], "skipped")
        self.assertEqual(self.rec.of("image"), [])

        # Once the shop resolves it, the held-back QR goes out for the real due.
        r = self.client.post("/admin/resolve-dispute", headers=ADMIN,
                             json={"invoice_id": led["invoices"][0]["invoice_id"]})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(len(self.rec.of("image")), 1)
        self.assertEqual(self.qr_amounts()[-1], Decimal("50"))

    # ---- 15 ----
    def test_whatsapp_failure_keeps_state_and_retry_is_safe(self):
        order_id = self.order()

        self.rec.fail_text = 2
        r = self.deliver(order_id)
        self.assertEqual(r.status_code, 502)
        self.assertEqual(r.json()["notifications"], {"bill_qr": "sent", "bill_text": "failed"})
        details = self.client.get(f"/admin/orders/{order_id}", headers=ADMIN).json()
        self.assertEqual(details["status"], "delivered")  # delivery itself is saved

        r = self.deliver(order_id)
        self.assertEqual(r.status_code, 502)

        r = self.deliver(order_id)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["notifications"], {"bill_qr": "sent", "bill_text": "sent"})

        bills = [c for c in self.rec.of("text") if "Bill amount" in c[2]]
        self.assertEqual(len(bills), 1)
        self.assertEqual(len(self.rec.of("image")), 1)
        self.assertEqual(len(self.ledger()["invoices"]), 1)

    # ---- QR image regressions (WhatsApp error 131053) ----

    def test_qr_image_send_failure_is_recorded_and_retried_once(self):
        order_id = self.order()
        self.rec.fail_image = 1

        r = self.deliver(order_id)
        self.assertEqual(r.status_code, 502)
        self.assertEqual(r.json()["notifications"], {"bill_qr": "failed", "bill_text": "sent"})
        self.assertEqual(self.rec.of("image"), [])
        bill = self.rec.of("text")[-1][2]
        self.assertNotIn("Upar bheje gaye UPI QR", bill)  # never claim a QR that failed
        self.assertIn("Abhi baaki: ₹60", bill)

        r = self.deliver(order_id)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["status"], "already_confirmed")
        self.assertEqual(r.json()["notifications"], {"bill_qr": "sent", "bill_text": "sent"})
        self.assertEqual(len(self.rec.of("image")), 1)
        self.assertEqual(self.qr_amounts(), [Decimal("60"), Decimal("60")])  # regenerated, same amount
        self.assertEqual(len([c for c in self.rec.of("text") if "Bill amount" in c[2]]), 1)
        self.assertEqual(len(self.ledger()["invoices"]), 1)

        # A further retry sends nothing.
        self.deliver(order_id)
        self.assertEqual(len(self.rec.of("image")), 1)

    def test_whatsapp_rejecting_qr_later_allows_retry(self):
        order_id = self.order()
        self.assertEqual(self.deliver(order_id).status_code, 200)
        wamid = f"wamid.{self.rec.calls.index(self.rec.of('image')[0]) + 1}"

        r = self.client.post(
            "/webhook",
            headers={"x-webhook-event": "whatsapp.message.failed"},
            json={"message": {"id": wamid, "type": "image", "kapso": {"statuses": [
                {"status": "failed", "errors": [{"code": 131053, "title": "Media upload error",
                                                 "error_data": {"details": "Image is invalid."}}]}]}}},
        )
        self.assertEqual(r.json(), {"status": "recorded_failure"})
        details = self.client.get(f"/admin/orders/{order_id}", headers=ADMIN).json()
        self.assertEqual(details["invoice"]["notifications"]["bill_qr"], "failed")

        r = self.deliver(order_id)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(self.rec.of("image")), 2)
        self.assertEqual(len(self.ledger()["invoices"]), 1)

    def _bill_rows(self, invoice_id):
        conn = db.connect()
        try:
            return {r["kind"]: dict(r) for r in conn.execute(
                "SELECT kind, status, attempts, provider_message_id FROM notifications"
                " WHERE ref_id = ?", (invoice_id,))}
        finally:
            conn.close()

    def _mark_qr_failed(self, invoice_id):
        """What the shop did by hand in shop.db: only bill_qr -> failed."""
        conn = db.connect()
        try:
            conn.execute("UPDATE notifications SET status = 'failed'"
                         " WHERE ref_id = ? AND kind = 'bill_qr'", (invoice_id,))
        finally:
            conn.close()

    def test_manually_failed_qr_is_retried_alone_on_already_confirmed_order(self):
        order_id = self.order()
        invoice_id = self.deliver(order_id).json()["invoice_id"]
        before = self._bill_rows(invoice_id)
        self._mark_qr_failed(invoice_id)

        r = self.deliver(order_id)

        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["status"], "already_confirmed")
        self.assertEqual(r.json()["invoice_id"], invoice_id)
        after = self._bill_rows(invoice_id)
        self.assertEqual(r.json()["notifications"],
                         {k: v["status"] for k, v in after.items()})  # persisted, not assumed
        self.assertEqual(after["bill_qr"]["status"], "sent")
        self.assertEqual(after["bill_qr"]["attempts"], 2)
        self.assertNotEqual(after["bill_qr"]["provider_message_id"],
                            before["bill_qr"]["provider_message_id"])
        self.assertEqual(after["bill_text"], before["bill_text"])  # text not resent

        self.assertEqual(len(self.rec.of("image")), 2)
        self.assertEqual(len(self.rec.of("text")), 2)  # order reply + one bill
        self.assertEqual(self.qr_amounts(), [Decimal("60"), Decimal("60")])
        led = self.ledger()
        self.assertEqual(len(led["invoices"]), 1)
        self.assertEqual(Decimal(led["invoices"][0]["amount"]), Decimal("60"))

    def test_failed_qr_retry_returns_502_and_stays_failed(self):
        order_id = self.order()
        invoice_id = self.deliver(order_id).json()["invoice_id"]
        self._mark_qr_failed(invoice_id)
        self.rec.fail_image = 1

        with self.assertLogs("src.notify", level="WARNING") as logs:
            r = self.deliver(order_id)

        self.assertEqual(r.status_code, 502)
        self.assertEqual(r.json()["notifications"], {"bill_qr": "failed", "bill_text": "sent"})
        self.assertIn("bill_qr", logs.output[0])
        self.assertNotIn("test-admin-token", "".join(logs.output))
        self.assertEqual(self._bill_rows(invoice_id)["bill_qr"]["status"], "failed")
        self.assertEqual(len(self.ledger()["invoices"]), 1)

    def test_concurrent_qr_retries_send_once(self):
        order_id = self.order()
        invoice_id = self.deliver(order_id).json()["invoice_id"]
        self._mark_qr_failed(invoice_id)

        started, release = threading.Event(), threading.Event()
        slow_image = self.rec.image

        def blocking_image(**kw):
            started.set()
            release.wait(5)
            return slow_image(**kw)

        results = []

        def retry():
            conn = db.connect()
            try:
                results.append(notify.send_bill(conn, invoice_id)["bill_qr"])
            except notify.NotificationBusy:
                results.append("busy")
            finally:
                conn.close()

        with mock.patch("src.notify.send_whatsapp_image", blocking_image):
            first = threading.Thread(target=retry)
            first.start()
            self.assertTrue(started.wait(5))
            retry()            # second retry while the first is mid-send
            release.set()
            first.join(5)

        self.assertEqual(sorted(results), ["busy", "sent"])
        self.assertEqual(len(self.rec.of("image")), 2)  # original + exactly one retry

    # ---- extra safety checks ----

    def test_admin_endpoints_require_token(self):
        order_id = self.order()
        for headers in ({}, {"x-shop-admin-token": "wrong"}):
            r = self.client.post("/admin/confirm-delivery", headers=headers,
                                 json={"order_id": order_id})
            self.assertEqual(r.status_code, 401)
        self.assertEqual(self.ledger()["invoices"], [])

    def test_credit_is_used_once_on_next_invoice(self):
        self.deliver(self.order())
        self.pay(100, "UPI-OVER")  # 40 extra -> credit
        self.assertEqual(Decimal(self.ledger()["available_credit"]), Decimal("40"))

        r = self.deliver(self.order("1 kg pyaz bhi chahiye", "m-next"))
        self.assertEqual(Decimal(r.json()["previously_verified"]), Decimal("35"))
        self.assertEqual(Decimal(r.json()["amount_due"]), 0)
        self.assertEqual(r.json()["notifications"]["bill_qr"], "skipped")

        led = self.ledger()
        self.assertEqual(Decimal(led["total_verified_payments"]), Decimal("100"))
        self.assertEqual(Decimal(led["available_credit"]), Decimal("5"))
        self.assertEqual(Decimal(led["remaining_balance"]), 0)

    def test_ambiguous_payment_allocation_needs_invoice_id(self):
        self.deliver(self.order())
        self.deliver(self.order("1 kg pyaz bhi chahiye", "m-b"))
        r = self.pay(30, "UPI-AMBIG")
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self.ledger()["payments"], [])

    def test_invalid_payment_amounts_rejected(self):
        self.deliver(self.order())
        for amount in (0, -5, "abc", "10.555"):
            self.assertIn(self.pay(amount, f"UPI-{amount}").status_code, (400, 422))
        self.assertEqual(self.ledger()["payments"], [])


class UnitConversionTest(unittest.TestCase):

    def test_spoken_units(self):
        n = normalizer.normalize_quantity
        self.assertEqual(n(2, "kilo", "kg"), 2)
        self.assertEqual(n(500, "gram", "kg"), 0.5)
        self.assertEqual(n(500, "gram", "250g"), 2)
        self.assertEqual(n(1, "dozen", "piece"), 12)
        self.assertEqual(n(1, "litre", "packet", {"1 litre": 2}), 2)
        self.assertEqual(n(1, "pack", "packet"), 1)
        with self.assertRaises(ValueError):
            n(2, "kg", "piece")


class PaymentQrTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        for p in (mock.patch.object(payments, "UPI_ID", "shop@testbank"),
                  mock.patch.object(payments, "PAYEE_NAME", "Test Shop"),
                  mock.patch.object(payments, "BASE_DIR", Path(self.tmp))):
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_qr_uses_configured_payee_and_exact_amount(self):
        qr = payments.generate_payment_qr(amount=Decimal("60.00"), invoice_ref="INV-1234")
        self.assertIn("pa=shop%40testbank", qr["upi_link"])
        self.assertIn("pn=Test+Shop", qr["upi_link"])
        self.assertIn("am=60.00", qr["upi_link"])
        self.assertIn("tn=INV-1234", qr["upi_link"])
        self.assertTrue(Path(qr["qr_path"]).exists())

    def test_qr_png_is_8bit_rgb_for_whatsapp(self):
        from PIL import Image
        qr = payments.generate_payment_qr(amount=60, invoice_ref="INV-RGB")
        with Image.open(qr["qr_path"]) as img:
            self.assertEqual((img.format, img.mode), ("PNG", "RGB"))

    def test_qr_rejects_bad_amounts(self):
        for amount in (0, -1, None, "abc", "NaN", "1.234"):
            with self.assertRaises(ValueError):
                payments.generate_payment_qr(amount=amount, invoice_ref="INV-X")


if __name__ == "__main__":
    unittest.main()
