from decimal import Decimal
import re

# Spoken/written unit -> canonical unit. Claude copies the customer's unit
# ("kilo", "gm", "pcs"); Python decides what it means.
UNIT_SYNONYMS = {
    "kg": ["kg", "kgs", "kilo", "kilos", "kilogram", "kilograms"],
    "g": ["g", "gm", "gms", "gram", "grams"],
    "litre": ["l", "ltr", "litre", "liter", "litres", "liters"],
    "ml": ["ml", "millilitre", "milliliter", "millilitres", "milliliters"],
    "piece": ["piece", "pieces", "pc", "pcs", "nos"],
    "dozen": ["dozen", "dozens", "darjan", "dz"],
    "packet": ["packet", "packets", "pack", "packs", "pkt", "pkts"],
    "bottle": ["bottle", "bottles"],
    "bar": ["bar", "bars"],
    "tube": ["tube", "tubes"],
}

_CANONICAL = {
    alias: canonical
    for canonical, aliases in UNIT_SYNONYMS.items()
    for alias in aliases
}

GRAMS = {"g": Decimal(1), "kg": Decimal(1000)}
MILLILITRES = {"ml": Decimal(1), "litre": Decimal(1000)}
PIECES = {"piece": Decimal(1), "dozen": Decimal(12)}


def canonical_unit(unit):
    unit = unit.lower().strip()
    # Pack sizes such as "250g" (paneer) stay as grams-per-unit.
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(g|gm|gms|gram|grams)", unit)
    if match:
        return f"{Decimal(match.group(1)).normalize():f}g"
    return _CANONICAL.get(unit, unit)


def _per_unit(unit, table):
    if unit in table:
        return table[unit]
    if table is GRAMS and re.fullmatch(r"\d+(\.\d+)?g", unit):
        return Decimal(unit[:-1])
    return None


def _convert(qty, unit, target):
    if unit == target:
        return qty
    for table in (GRAMS, MILLILITRES, PIECES):
        a, b = _per_unit(unit, table), _per_unit(target, table)
        if a is not None and b is not None:
            return qty * a / b
    return None


def _out(value):
    return int(value) if value == value.to_integral_value() else float(value)


def normalize_quantity(qty, unit, target_unit, conversions=None):
    """
    Convert a quantity into the unit used by prices.json.

    `conversions` is the optional per-item table from prices.json,
    e.g. milk: {"1 litre": 2} means 1 litre = 2 packets.

    Claude does not perform these conversions.
    """

    if qty is None or unit is None:
        return None

    q = Decimal(str(qty))
    unit_c = canonical_unit(unit)
    target_c = canonical_unit(target_unit)

    converted = _convert(q, unit_c, target_c)
    if converted is not None:
        return _out(converted)

    for key, target_qty in (conversions or {}).items():
        size, _, key_unit = key.partition(" ")
        in_key_unit = _convert(q, unit_c, canonical_unit(key_unit))
        if in_key_unit is not None:
            return _out(in_key_unit / Decimal(size) * Decimal(str(target_qty)))

    raise ValueError(
        f"Cannot convert {qty} {unit} to {target_unit}"
    )
