"""Work out how much coffee a variant actually contains.

Shopify's ``grams`` field is the *shipping* weight and is frequently wrong for our purpose:
it includes packaging (340g bag -> 360g), is left at a default (5lb bag -> 454g), or is 0.
The size printed in the variant/product title is what the roaster is selling, so it wins.
"""

import re
from dataclasses import dataclass, field

UNIT_GRAMS = {
    "kg": 1000.0, "kgs": 1000.0, "kilo": 1000.0, "kilos": 1000.0, "kilogram": 1000.0, "kilograms": 1000.0,
    "kilogramme": 1000.0, "kilogrammes": 1000.0,
    "g": 1.0, "gr": 1.0, "grs": 1.0, "gram": 1.0, "grams": 1.0, "gramme": 1.0, "grammes": 1.0,
    "oz": 28.3495, "ounce": 28.3495, "ounces": 28.3495,
    "lb": 453.592, "lbs": 453.592, "pound": 453.592, "pounds": 453.592, "livre": 453.592, "livres": 453.592,
}
_UNIT = r"(kilogrammes?|kilograms?|kilos?|kgs?|grammes?|grams?|grs?|g|ounces?|oz|pounds?|lbs?|livres?)"
_NUM = r"(\d+(?:[.,]\d+)?)"

# "7x 250g", "2 x 340 g"
_MULTI_PREFIX = re.compile(rf"(?<![\d.])(\d{{1,2}})\s*[x×]\s*{_NUM}\s*{_UNIT}\b", re.I)
# "250g x 4"
_MULTI_SUFFIX = re.compile(rf"{_NUM}\s*{_UNIT}\s*[x×]\s*(\d{{1,2}})(?![\d.])", re.I)
# "1/2 lb", "½ lb"
_FRACTION = re.compile(rf"(?<![\d.])(?:(\d)\s*/\s*(\d)|(½|¼|¾))\s*{_UNIT}\b", re.I)
_SIMPLE = re.compile(rf"(?<![\d.,]){_NUM}\s*-?\s*{_UNIT}\b", re.I)
_UNICODE_FRACTIONS = {"½": 0.5, "¼": 0.25, "¾": 0.75}

# Bag sizes roasters actually sell. Used to strip packaging weight from shipping weights.
COMMON_SIZES_G = [
    100, 113, 150, 200, 225, 227, 250, 283, 300, 340, 350, 355, 375, 400, 454, 500, 680, 750,
    907, 1000, 1134, 1361, 1500, 2000, 2268, 2500, 3000, 4536, 5000,
]

MIN_PLAUSIBLE_G = 20
MAX_PLAUSIBLE_G = 25_000


def _num(s: str) -> float:
    return float(s.replace(",", "."))


def parse_weight(text: str | None) -> float | None:
    """Return grams for the first size mentioned in ``text``, or None."""
    if not text:
        return None
    if m := _MULTI_PREFIX.search(text):
        grams = int(m.group(1)) * _num(m.group(2)) * UNIT_GRAMS[m.group(3).lower()]
    elif m := _MULTI_SUFFIX.search(text):
        grams = int(m.group(3)) * _num(m.group(1)) * UNIT_GRAMS[m.group(2).lower()]
    elif m := _FRACTION.search(text):
        frac = int(m.group(1)) / int(m.group(2)) if m.group(1) else _UNICODE_FRACTIONS[m.group(3)]
        grams = frac * UNIT_GRAMS[m.group(4).lower()]
    elif m := _SIMPLE.search(text):
        grams = _num(m.group(1)) * UNIT_GRAMS[m.group(2).lower()]
    else:
        return None
    if not MIN_PLAUSIBLE_G <= grams <= MAX_PLAUSIBLE_G:
        return None
    return round(grams, 1)


def snap_shipping_weight(grams: float) -> float:
    """Shipping weights include the bag; snap to the common size that best fits ~6% packaging.

    An exact common size is assumed to already be the net weight.
    """
    if grams in COMMON_SIZES_G:
        return float(grams)
    candidates = [s for s in COMMON_SIZES_G if s <= grams <= s * 1.15 + 10]
    if not candidates:
        return float(grams)
    return float(min(candidates, key=lambda s: abs(grams / s - 1.06)))


@dataclass
class WeightResult:
    grams: float | None
    source: str  # variant_title | product_title | shipping_weight | none
    flags: list[str] = field(default_factory=list)


def resolve_weight(
    variant_title: str | None,
    product_title: str | None,
    shipping_grams: float | None,
    product_has_size_variants: bool,
) -> WeightResult:
    flags: list[str] = []
    grams: float | None = None
    source = "none"

    if (w := parse_weight(variant_title)) is not None:
        grams, source = w, "variant_title"
    elif not product_has_size_variants and (w := parse_weight(product_title)) is not None:
        grams, source = w, "product_title"
    elif shipping_grams and 50 <= shipping_grams <= MAX_PLAUSIBLE_G:
        grams, source = snap_shipping_weight(shipping_grams), "shipping_weight"
        flags.append("weight_from_shipping")

    if grams is not None and source != "shipping_weight" and shipping_grams:
        ratio = shipping_grams / grams
        if ratio < 0.65 or ratio > 1.6:
            # Informational: the listed size disagrees with Shopify's shipping weight.
            flags.append("weight_conflict")

    if grams is None:
        flags.append("no_weight")
    return WeightResult(grams=grams, source=source, flags=flags)
