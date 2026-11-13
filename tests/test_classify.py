import pytest

from beangrid.classify import classify, detect_roast, fold


@pytest.mark.parametrize(
    "title, ptype, opts, has_weight",
    [
        ("Ethiopia Guji Natural", "Coffee", ["340g"], True),
        ("Décaf - Guarapo", "Sac de café", ["300g / En grain"], True),
        ("Apricot Cookie - Peru Yellow Caturra", "", [], False),
        ("Chocolate Bomb Espresso", "Coffee", ["Whole Bean"], False),
        ("Espresso Bar Blend", "", ["340g"], True),
        ("Swiss Water Decaf Colombia", "Coffee", ["Whole Bean"], False),
        ("Cold Brew Blend", "Coffee", ["1 lb"], True),
        ("Ponderosa Anaerobic Honey Sidra 100g", "", [], True),
        ("Colombia - La Soledad | SL-28 Natural", "Coffee", ["40 g", "250 g"], True),
        ("Rampage Coffee | 5 Pound Bags", "Coffee", ["Whole Bean"], True),
        ("Wholesale Espresso 5lb", "Coffee", ["5lb"], True),
    ],
)
def test_coffee(title, ptype, opts, has_weight):
    c = classify(title, ptype, option_values=opts, has_weight=has_weight)
    assert c.category == "coffee", c.reason


@pytest.mark.parametrize(
    "title, ptype, category",
    [
        ("Vanilla Syrup", "Syrup", "syrup_food"),
        ("Sirop d'érable", "", "syrup_food"),
        ("Cōchu Chocolate Bar - Tutto Bene", "Cochu Chocolate", "syrup_food"),
        ("Monogram x Loveramics Diner Mug", "Mugs", "drinkware"),
        ("FELLOW Stagg EKG Pro Electric Pour Over Kettle", "Equipment", "equipment"),
        ("Osmo Cone 01 Filters (100 count)", "", "equipment"),
        ("SIBARIST B3 HYBRID SPECIALTY COFFEE FILTERS", "Filters", "equipment"),
        ("Cafiza Equipment Cleaning Powder 20oz", "Supplies", "cleaning"),
        ("Coffee Subscription · Espresso", "Subscription", "subscription"),
        ("Roaster's Club", "Coffee", "subscription"),
        ("Abonnement café", "Abonnement", "subscription"),
        ("Gift Card", "Gift Card", "gift_card"),
        ("Carte-cadeau", "", "gift_card"),
        ("Copan - Raw Green Coffee - Honduras", "Green Coffee", "green_coffee"),
        ("Mile Zero Capsules", "Coffee", "pods"),
        ("Rosso Instant! Coffee / Homebody", "Retail Web", "instant"),
        ("Indian Spiced Chai", "Tea", "tea"),
        ("Thé vert Sencha", "", "tea"),
        ("Fernwood Coffee Long Sleeve Shirt", "Merchandise", "merch"),
        ("Sampler Bundle - Try all four blends", "Coffee", "bundle"),
        ("49th Introductory Tasting Set", "Bundle", "bundle"),
        ("Teacher Gift Idea", "Coffee", "bundle"),
        ("Ethiopia Origin Class — November 29th", "Classes and experiences", "service"),
        ("Coffee Catering", "Coffee", "service"),
        ("Coffee sack", "", "merch"),
    ],
)
def test_not_coffee(title, ptype, category):
    assert classify(title, ptype, has_weight=False).category == category


def test_ready_to_drink_needs_liquid_sizes():
    assert classify("FARO COLD BREW - LEMONADE", "Coffee", option_values=["4 x 250ml"]).category == "ready_to_drink"
    assert classify("Cold Brew en bouteille", "Café", option_values=["6 x 1L"]).category == "ready_to_drink"


def test_unknown_when_no_signals():
    assert classify("Rye Bread", "").category == "unknown"


def test_attributes():
    c = classify(
        "Colombia El Paraiso - Washed Pink Bourbon",
        "Coffee",
        tags=["Roast Profile_Light", "Notes__Dark Chocolate", "Type_Filter"],
        has_weight=True,
    )
    assert c.origins == ["Colombia"]
    assert c.process == ["Washed"]
    assert c.roast == "Light"  # not "Dark" from the tasting note
    assert c.style == "filter"
    assert not c.decaf and not c.blend


def test_decaf_blend_and_half_caf():
    assert classify("Sugar Cane Decaf", "Coffee", has_weight=True).decaf
    hc = classify("No Punch Backs Half Caf", "Coffee", has_weight=True)
    assert hc.half_caf and not hc.decaf
    assert classify("Saison - Brésil / Guatemala", "Café", has_weight=True).blend


def test_roast_requires_context():
    assert detect_roast(fold("notes: dark chocolate, cherry")) is None
    assert detect_roast(fold("Torréfaction foncée")) == "Dark"
    assert detect_roast("medium-dark roast") == "Medium-dark"
