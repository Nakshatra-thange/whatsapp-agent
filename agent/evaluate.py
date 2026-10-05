import json
from pathlib import Path
from collections import Counter

from src.extractor import extract_events
from src.validator import validate_and_normalize
from src.scorer import compare_result
from ledger import compute_ledger


BASE_DIR = Path(__file__).resolve().parent


def load_json(path):
    with open(path) as f:
        return json.load(f)


def evaluate_customer(customer):

    chat_path = BASE_DIR / "chats" / f"{customer}.txt"
    label_path = BASE_DIR / "labels" / f"{customer}.json"

    chat = chat_path.read_text()
    rules = (BASE_DIR / "rules.md").read_text()
    prices = load_json(BASE_DIR / "prices.json")
    label = load_json(label_path)

    # -------------------------
    # 1. Extract
    # -------------------------

    result = extract_events(
        chat=chat,
        rules=rules,
        prices=prices,
    )

    # -------------------------
    # 2. Validate + normalize
    # -------------------------

    events = validate_and_normalize(
        result,
        prices,
    )

    # -------------------------
    # 3. Ledger
    # -------------------------

    predicted_ledger = compute_ledger(
        events,
        prices,
    )

    # -------------------------
    # 4. Compare with label
    # -------------------------

    comparison = compare_result(
        predicted_events=events,
        expected_events=label["expected_events"],
        predicted_ledger=predicted_ledger,
        expected_ledger=label["expected_ledger"],
    )

    return {
        "customer": customer,
        "passed": comparison["passed"],
        "events": events,
        "predicted_ledger": predicted_ledger,
        "expected_ledger": label["expected_ledger"],
        "comparison": comparison,
    }


def main():

    label_files = sorted(
        (BASE_DIR / "labels").glob("*.json")
    )

    results = []

    for label_file in label_files:

        customer = label_file.stem

        print(
            f"Evaluating {customer}...",
            flush=True,
        )

        try:
            result = evaluate_customer(
                customer
            )

            results.append(result)

        except Exception as e:

            print(
                f"ERROR: {customer}: {e}"
            )

            results.append({
                "customer": customer,
                "passed": False,
                "comparison": {
                    "errors": [{
                        "category": "pipeline_error",
                        "error": str(e),
                    }]
                },
            })

    # -------------------------
    # Summary
    # -------------------------

    total = len(results)

    passed = sum(
        1 for r in results
        if r["passed"]
    )

    failed = total - passed

    print()
    print("=" * 50)
    print("       GROCERY AGENT EVALUATION")
    print("=" * 50)

    print(f"Customers: {total}")
    print(f"Passed:    {passed}")
    print(f"Failed:    {failed}")

    # -------------------------
    # Failure breakdown
    # -------------------------

    failures = Counter()

    for result in results:

        for error in result["comparison"]["errors"]:

            failures[
                error["category"]
            ] += 1

    print()
    print("FAILURE BREAKDOWN")
    print("-" * 30)

    if failures:

        for category, count in failures.most_common():

            print(
                f"{category:<25} {count}"
            )

    else:

        print("No failures!")

    # -------------------------
    # Per-customer failures
    # -------------------------

    print()
    print("CUSTOMER RESULTS")
    print("-" * 30)

    for result in results:

        status = (
            "PASS"
            if result["passed"]
            else "FAIL"
        )

        print(
            f"{result['customer']:<15} {status}"
        )

        if not result["passed"]:

            for error in result["comparison"]["errors"]:

                print(
                    f"  → {error['category']}"
                )


if __name__ == "__main__":
    main()