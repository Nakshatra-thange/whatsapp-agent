import json
from pathlib import Path
from response import format_response
from src.extractor import extract_events
from src.validator import validate_and_normalize
from ledger import compute_ledger


BASE_DIR = Path(__file__).resolve().parent


def load_json(filename):
    with open(BASE_DIR / filename, "r", encoding="utf-8") as f:
        return json.load(f)


def load_rules():
    with open(BASE_DIR / "rules.md", "r", encoding="utf-8") as f:
        return f.read()


def process_chat(chat):
    """
    Process one WhatsApp conversation.

    Returns:
        events: validated/normalized events
        ledger: calculated financial state
    """

    rules = load_rules()
    prices = load_json("prices.json")

    # 1. Claude extracts events
    extraction = extract_events(
        chat=chat,
        rules=rules,
        prices=prices,
    )

    # 2. Python validates + normalizes
    events = validate_and_normalize(
        extraction,
        prices,
    )

    # 3. Python calculates the financial state
    ledger = compute_ledger(
        events,
        prices,
    )

    return {
        "events": events,
        "ledger": ledger,
    }


if __name__ == "__main__":

    chat = """
[2026-10-15 10:00] Customer: 2 kilo aloo dena
[2026-10-15 10:30] Shop: delivered
"""

    result = process_chat(chat)

    print("\n=== RESPONSE ===")
    print(format_response(result))