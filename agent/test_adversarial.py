import json
from pathlib import Path

from src.extractor import extract_events
from src.validator import validate_and_normalize
from ledger import compute_ledger


BASE_DIR = Path(__file__).resolve().parent


def load_data(filename):
    chat = (
        BASE_DIR / "tests" / "adversarial_chats" / filename
    ).read_text()

    rules = (BASE_DIR / "rules.md").read_text()

    prices = json.loads(
        (BASE_DIR / "prices.json").read_text()
    )

    return chat, rules, prices


def test_unknown_item():
    chat, rules, prices = load_data("unknown_item.txt")

    result = extract_events(
        chat=chat,
        rules=rules,
        prices=prices,
    )

    try:
        validate_and_normalize(result, prices)
    except ValueError as e:
        print("PASS: unknown item was rejected")
        print(e)
        return

    raise AssertionError(
        "Unknown item should have been rejected"
    )


def test_payment_promise():
    chat, rules, prices = load_data("payment_promise.txt")

    result = extract_events(
        chat=chat,
        rules=rules,
        prices=prices,
    )

    events = validate_and_normalize(
        result,
        prices,
    )

    ledger = compute_ledger(
        events,
        prices,
    )

    print("\nPAYMENT PROMISE TEST")
    print("Events:")
    print(json.dumps(events, indent=2))

    print("\nLedger:")
    print(json.dumps(ledger, indent=2))

    assert any(
        event["type"] == "PROMISE"
        for event in events
    )

    assert not any(
        event["type"] == "PAYMENT"
        for event in events
    )

    assert ledger["total_paid"] == 0
    assert ledger["balance"] == 60
    assert ledger["payment_status"] == "unpaid"

    print("\nPASS: payment promise was not counted as payment")

def test_partial_payment():
    chat, rules, prices = load_data("partial_payment.txt")

    result = extract_events(
        chat=chat,
        rules=rules,
        prices=prices,
    )

    events = validate_and_normalize(
        result,
        prices,
    )

    ledger = compute_ledger(
        events,
        prices,
    )

    print("\nPARTIAL PAYMENT TEST")
    print("Events:")
    print(json.dumps(events, indent=2))

    print("\nLedger:")
    print(json.dumps(ledger, indent=2))

    assert ledger["total_billed"] == 100
    assert ledger["total_paid"] == 80
    assert ledger["balance"] == 20
    assert ledger["payment_status"] == "partial"

    print("\nPASS: partial payments were added correctly")

def test_overpayment():
    chat, rules, prices = load_data("overpayment.txt")

    result = extract_events(
        chat=chat,
        rules=rules,
        prices=prices,
    )

    events = validate_and_normalize(
        result,
        prices,
    )

    ledger = compute_ledger(
        events,
        prices,
    )

    print("\nOVERPAYMENT TEST")
    print("Events:")
    print(json.dumps(events, indent=2))

    print("\nLedger:")
    print(json.dumps(ledger, indent=2))

    assert ledger["total_billed"] == 100
    assert ledger["total_paid"] == 120
    assert ledger["balance"] == 0
    assert ledger["credit"] == 20
    assert ledger["payment_status"] == "overpaid"

    print("\nPASS: overpayment created credit correctly")

def test_credit_adjustment():
    chat, rules, prices = load_data("credit_adjustment.txt")

    result = extract_events(
        chat=chat,
        rules=rules,
        prices=prices,
    )

    events = validate_and_normalize(
        result,
        prices,
    )

    ledger = compute_ledger(
        events,
        prices,
    )

    print("\nCREDIT ADJUSTMENT TEST")
    print("Events:")
    print(json.dumps(events, indent=2))

    print("\nLedger:")
    print(json.dumps(ledger, indent=2))

    assert ledger["total_billed"] == 130
    assert ledger["total_paid"] == 120
    assert ledger["balance"] == 10
    assert ledger["credit"] == 0
    assert ledger["payment_status"] == "partial"

    print("\nPASS: existing credit was applied without counting it as new cash")

def test_cancellation():
    chat, rules, prices = load_data("cancellation.txt")

    result = extract_events(
        chat=chat,
        rules=rules,
        prices=prices,
    )

    events = validate_and_normalize(
        result,
        prices,
    )

    ledger = compute_ledger(
        events,
        prices,
    )

    print("\nCANCELLATION TEST")
    print("Events:")
    print(json.dumps(events, indent=2))

    print("\nLedger:")
    print(json.dumps(ledger, indent=2))

    assert ledger["total_billed"] == 0
    assert ledger["total_paid"] == 0
    assert ledger["balance"] == 0
    assert ledger["payment_status"] == "not_applicable"

    print("\nPASS: cancelled order created no invoice")

def test_dispute():
    chat, rules, prices = load_data("dispute.txt")

    result = extract_events(
        chat=chat,
        rules=rules,
        prices=prices,
    )

    events = validate_and_normalize(
        result,
        prices,
    )

    ledger = compute_ledger(
        events,
        prices,
    )

    print("\nDISPUTE TEST")
    print("Events:")
    print(json.dumps(events, indent=2))

    print("\nLedger:")
    print(json.dumps(ledger, indent=2))

    assert ledger["total_billed"] == 90
    assert ledger["known_billed"] == 90
    assert ledger["total_paid"] == 0
    assert ledger["balance"] is None
    assert ledger["credit"] is None
    assert ledger["payment_status"] == "disputed"
    assert ledger["needs_human"] is True

    print("\nPASS: delivery dispute requires human review")


if __name__ == "__main__":
    test_unknown_item()
    test_payment_promise()
    test_partial_payment()
    test_overpayment()
    test_credit_adjustment()
    test_cancellation()
    test_dispute()