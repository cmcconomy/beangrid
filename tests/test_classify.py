import pytest

from beangrid.classify import classify, detect_roast, fold, is_wholesale


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


@pytest.mark.parametrize("title, ptype, tags", [
    ("Louisa", "b2b coffee", []),  # Traffic: private-label cafe blends
    ("Café Zuzu", "Sac de café", ["whitelabel", "wholesale-only"]),  # Café Pista
    ("El Camino - LNV", "Sac de café", ["wholesale-only"]),
    ("EARLY BIRD | Medium Roast Blend", "Bag", ["standard wholesale"]),  # Birdy
    ("FIGHT OR FLIGHT | Dark Roast Blend (WS)", "", []),
    ("Polaris Espresso - W", "Coffee", []),  # Equator's wholesale line
    ("Outlier – W", "Coffee", []),
    ("Test Wholesale Product", "", []),
    ("House Blend", "Café / Restaurant Service", []),
    ("Espresso Blend", "Café grossiste", []),
    ("Lost & Found - Locals Brew Dark", "wholesale", ["FYEO"]),  # Smile Tiger
    ("Butter & Blue - Iced Blend", "", ["wholesale_butterblue"]),  # Coffee Wallas: per-customer blends
    ("Berlingot", "", ["wholesale:buckets"]),  # Melk
    ("Tima - Éthiopie", "Sac de café", ["wholesale only"]),
    ("Pie Face", "coffee", ["b2b-only"]),
    ("Office Coffee - Medium", "Coffee", []),
    ("Bistro Blend", "Foodservice", []),
    ("Private Label Espresso", "Coffee", []),
])
def test_wholesale_listings(title, ptype, tags):
    assert is_wholesale(title, ptype, tags)


@pytest.mark.parametrize("title, ptype, tags", [
    ("Kenya Nyeri - Washed", "Coffee", ["washed"]),
    ("Willow Blend", "Coffee", []),
    ("Brazil - Walter Ferreira", "Coffee", []),
    ("Ethiopia Guji W1", "Coffee", []),
    ("Espresso Blend 5lb", "Coffee", ["5lb", "bulk"]),  # big bags alone are retail too
    ("Deep Groove Espresso", "Dark Espresso", ["Espresso"]),
    ("Café du Monde Blend", "Coffee", ["cafe"]),
    # Retail coffees that a store's wholesale app or catalog also lists (Phil & Sebastian, September...)
    ("Snow Day", "Filter", ["wholesale-app-category-coffee-2-sub-filter-1", "wholesale-app-xero-account-5033"]),
    ("Marmalade Cola - Guatemala Espresso", "", ["wholesale"]),
    ("Le Club Expé", "Café", ["Filtre", "no-wholesale"]),  # 94Celcius: explicitly *not* wholesale
    ("Las Moras", "Café", ["commandes succursales", "commercial"]),  # Café Saint-Henri
    ("Swiss Water Decaf", "Coffee", []),
])
def test_retail_listings_are_not_wholesale(title, ptype, tags):
    assert not is_wholesale(title, ptype, tags)
