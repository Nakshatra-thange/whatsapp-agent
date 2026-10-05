import json

from src.extractor import extract_events
from src.validator import validate_and_normalize
from ledger import compute_ledger


with open("chats/anjali.txt") as f:
    chat = f.read()

with open("rules.md") as f:
    rules = f.read()

with open("prices.json") as f:
    prices = json.load(f)


# 1. Claude extraction
result = extract_events(
    chat=chat,
    rules=rules,
    prices=prices,
)

# 2. Validate + normalize
events = validate_and_normalize(
    result,
    prices,
)

# 3. Deterministic ledger
ledger = compute_ledger(
    events,
    prices,
)

print("\nEVENTS:")
print(json.dumps(events, indent=2))

print("\nLEDGER:")
print(json.dumps(ledger, indent=2))