def normalize_quantity(qty, unit, target_unit):
    """
    Convert a quantity into the unit used by prices.json.

    Claude does not perform these conversions.
    """

    if qty is None or unit is None:
        return None

    unit = unit.lower().strip()
    target_unit = target_unit.lower().strip()

    if unit == target_unit:
        return qty

    # weight
    if unit in ("gram", "grams", "g"):
        if target_unit == "kg":
            return qty / 1000

    if unit in ("kg", "kilogram", "kilograms"):
        if target_unit == "gram":
            return qty * 1000

    # volume
    if unit in ("ml", "millilitre", "milliliter"):
        if target_unit in ("litre", "liter", "l"):
            return qty / 1000

    if unit in ("litre", "liter", "l"):
        if target_unit == "ml":
            return qty * 1000

    raise ValueError(
        f"Cannot convert {qty} {unit} to {target_unit}"
    )