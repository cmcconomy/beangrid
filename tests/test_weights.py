import pytest

from beangrid.weights import parse_weight, resolve_weight, snap_shipping_weight


@pytest.mark.parametrize(
    "text, grams",
    [
        ("340g", 340),
        ("300gr / En grain", 300),
        ("2.27Kg / Whole Bean", 2270),
        ("1 lb / Cone filter", 453.6),
        ("1/2 lb / Espresso", 226.8),
        ("½ lb", 226.8),
        ("10.6oz", 300.5),
        ("12 oz", 340.2),
        ("5lb (SAVE 5%)", 2268),
        ("Tombstone Espresso / French Press / 2270g / 5lb", 2270),
        ("7x 250g Bags", 1750),
        ("250g x 4", 1000),
        ("2,5 kg", 2500),
        ("1 livre", 453.6),
        ("40g SAMPLE", 40),
        ("Beans / 3/4-pound (340g)", 340.2),  # Virgin Hill: was read as "4-pound" (1814 g)
        ("1/2-lb bag", 226.8),
        ("3/4 lb", 340.2),
        ("¾-pound", 340.2),
        ("Whole Bean/12oz", 340.2),  # a slash after a word is just a separator
        ("HARIO V60-02 Coffee Server (700ml - 24oz) / 250g", 250),  # fluid oz skipped, real weight kept
    ],
)
def test_parse_weight(text, grams):
    assert parse_weight(text) == pytest.approx(grams, abs=0.2)


@pytest.mark.parametrize("text", ["Default Title", "Whole Bean", "4 x 250ml", "Lot #10", "", None, "V60 02", "3g",
                                  "HARIO V60-02 Coffee Server (700ml - 24oz)",  # Mikko: fluid ounces, not a bag
                                  "FELLOW Carter Move Mug (355ml/12oz)"])
def test_parse_weight_none(text):
    assert parse_weight(text) is None


def test_label_beats_shipping_weight_and_flags_conflict():
    # Real case: 49th Parallel listed a 10.6oz bag with a 170g shipping weight.
    r = resolve_weight("10.6oz", "Honduras", 170, product_has_size_variants=True)
    assert r.grams == pytest.approx(300.5, abs=0.2)
    assert r.source == "variant_title"
    assert "weight_conflict" in r.flags


def test_product_title_used_when_variants_are_grinds():
    r = resolve_weight("Whole Bean", "RIOT | Medium Roast Blend - 340g", 360, product_has_size_variants=False)
    assert (r.grams, r.source, r.flags) == (340, "product_title", [])


def test_product_title_ignored_when_variants_carry_sizes():
    # "5lb Coffee Bag" product whose variants are grind options of other sizes shouldn't use title.
    r = resolve_weight("Whole Bean", "Sampler 5lb", 300, product_has_size_variants=True)
    assert r.source == "shipping_weight"


def test_shipping_weight_fallback_strips_packaging():
    r = resolve_weight("Whole Bean", "Mystery Blend", 360, product_has_size_variants=False)
    assert r.grams == 340
    assert "weight_from_shipping" in r.flags


def test_no_weight():
    r = resolve_weight("", "Gift of Coffee", 0, False)
    assert r.grams is None and r.flags == ["no_weight"]


@pytest.mark.parametrize("ship, snapped", [(250, 250), (360, 340), (216, 200), (2313, 2268), (777, 750), (1300, 1134)])
def test_snap_shipping_weight(ship, snapped):
    assert snap_shipping_weight(ship) == snapped
