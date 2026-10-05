import json
from pathlib import Path

from src.extractor import extract_events
from src.validator import validate_and_normalize


BASE_DIR = Path(__file__).resolve().parent.parent


def main():

    chat = (
        BASE_DIR / "chats" / "anjali.txt"
    ).read_text()

    rules = (
        BASE_DIR / "rules.md"
    ).read_text()

    prices = json.loads(
        (BASE_DIR / "prices.json").read_text()
    )

    result = extract_events(
        chat=chat,
        rules=rules,
        prices=prices,
    )

    events = validate_and_normalize(
        result,
        prices,
    )

    print(json.dumps(
        events,
        indent=2,
    ))


if __name__ == "__main__":
    main()