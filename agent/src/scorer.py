def compare_events(predicted, expected):
    errors = []

    if len(predicted) != len(expected):
        errors.append({
            "category": "wrong_event_count",
            "expected": len(expected),
            "predicted": len(predicted),
        })

    for index, expected_event in enumerate(expected):

        if index >= len(predicted):
            break

        predicted_event = predicted[index]

        # Event type
        if predicted_event.get("type") != expected_event.get("type"):
            errors.append({
                "category": "wrong_event_type",
                "index": index,
                "expected": expected_event.get("type"),
                "predicted": predicted_event.get("type"),
            })
            continue

        # Date
        if predicted_event.get("date") != expected_event.get("date"):
            errors.append({
                "category": "wrong_date",
                "index": index,
                "expected": expected_event.get("date"),
                "predicted": predicted_event.get("date"),
            })

        # ORDER
        if expected_event.get("type") == "ORDER":

            expected_items = expected_event.get("items", [])
            predicted_items = predicted_event.get("items", [])

            if len(predicted_items) != len(expected_items):
                errors.append({
                    "category": "wrong_item_count",
                    "event_index": index,
                    "expected": len(expected_items),
                    "predicted": len(predicted_items),
                })

            for item_index, expected_item in enumerate(expected_items):

                if item_index >= len(predicted_items):
                    break

                predicted_item = predicted_items[item_index]

                # Item name
                if predicted_item.get("item") != expected_item.get("item"):
                    errors.append({
                        "category": "wrong_item_name",
                        "event_index": index,
                        "item_index": item_index,
                        "expected": expected_item.get("item"),
                        "predicted": predicted_item.get("item"),
                    })

                # Quantity
                if predicted_item.get("qty") != expected_item.get("qty"):
                    errors.append({
                        "category": "wrong_quantity",
                        "event_index": index,
                        "item_index": item_index,
                        "expected": expected_item.get("qty"),
                        "predicted": predicted_item.get("qty"),
                    })

                # Unit
                if predicted_item.get("unit") != expected_item.get("unit"):
                    errors.append({
                        "category": "wrong_unit",
                        "event_index": index,
                        "item_index": item_index,
                        "expected": expected_item.get("unit"),
                        "predicted": predicted_item.get("unit"),
                    })

                # Clarification flag
                if (
                    predicted_item.get("needs_clarification", False)
                    != expected_item.get("needs_clarification", False)
                ):
                    errors.append({
                        "category": "wrong_item_flag",
                        "event_index": index,
                        "item_index": item_index,
                        "expected": expected_item.get(
                            "needs_clarification", False
                        ),
                        "predicted": predicted_item.get(
                            "needs_clarification", False
                        ),
                    })

        # PAYMENT
        if expected_event.get("type") == "PAYMENT":

            if predicted_event.get("amount") != expected_event.get("amount"):
                errors.append({
                    "category": "wrong_payment_amount",
                    "index": index,
                    "expected": expected_event.get("amount"),
                    "predicted": predicted_event.get("amount"),
                })

    return errors


def compare_ledger(predicted, expected):

    errors = []
    number_errors = []
    flag_errors = []

    # Financial values
    number_fields = [
        "total_billed",
        "known_billed",
        "total_paid",
        "balance",
        "credit",
    ]

    for field in number_fields:

        predicted_value = predicted.get(field)
        expected_value = expected.get(field)

        if predicted_value != expected_value:

            error = {
                "category": "wrong_number",
                "field": field,
                "expected": expected_value,
                "predicted": predicted_value,
            }

            errors.append(error)
            number_errors.append(error)

    # Payment status
    if (
        predicted.get("payment_status")
        != expected.get("payment_status")
    ):
        errors.append({
            "category": "wrong_payment_status",
            "expected": expected.get("payment_status"),
            "predicted": predicted.get("payment_status"),
        })

    # IMPORTANT FLAGS
    for field in [
        "needs_clarification",
        "needs_human",
    ]:

        predicted_value = predicted.get(field, False)
        expected_value = expected.get(field, False)

        if predicted_value != expected_value:

            # Missing human-review flag is especially important
            if field == "needs_human" and expected_value is True:
                category = "missed_flag"
            else:
                category = "wrong_flag"

            error = {
                "category": category,
                "field": field,
                "expected": expected_value,
                "predicted": predicted_value,
            }

            errors.append(error)
            flag_errors.append(error)

    return {
        "errors": errors,
        "number_errors": number_errors,
        "flag_errors": flag_errors,
        "flags_match": len(flag_errors) == 0,
        "numbers_match": len(number_errors) == 0,
    }


def compare_result(
    predicted_events,
    expected_events,
    predicted_ledger,
    expected_ledger,
):

    event_errors = compare_events(
        predicted_events,
        expected_events,
    )

    ledger_result = compare_ledger(
        predicted_ledger,
        expected_ledger,
    )

    all_errors = (
        event_errors
        + ledger_result["errors"]
    )

    return {
        "passed": len(all_errors) == 0,
        "events_match": len(event_errors) == 0,
        "numbers_match": ledger_result["numbers_match"],
        "flags_match": ledger_result["flags_match"],
        "errors": all_errors,
    }