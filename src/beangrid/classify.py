"""Decide whether a Shopify product is roasted coffee beans, and pull out shopper-facing attributes.

Rules only look at the product's *own* labelling (title, product_type, tags, option names) because
descriptions routinely mention coffee on mugs and grinders. English and French are both covered.
"""

import re
import unicodedata
from dataclasses import dataclass, field


def fold(text: str) -> str:
    """Lowercase and strip accents so 'Café Décaféiné' matches 'cafe decafeine'."""
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


def _rx(*words: str) -> re.Pattern[str]:
    return re.compile(r"(?<![a-z])(?:" + "|".join(words) + r")(?![a-z])")


# Ordered: the first category whose pattern matches the title/product_type wins.
EXCLUDE_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("gift_card", _rx("gift ?cards?", "cartes?[- ]cadeaux?", "e-?gift", "gift certificate")),
    ("subscription", _rx("subscriptions?", "abonnements?", "coffee club", "club", "subscribe")),
    ("green_coffee", _rx("green coffee", "green beans?", "raw green", "cafe vert", "unroasted")),
    ("pods", _rx("pods?", "capsules?", "nespresso", "k-?cups?", "dosettes?")),
    ("ready_to_drink", re.compile(r"(?<![a-z])cold brew(?![a-z])")),
    ("drip_bags", _rx("drip ?bags?", "steeped", "coffee bags", "sachets?", "pour[- ]over bags?", "single serve")),
    ("instant", _rx("instant", "soluble", "concentrates?", "cold brew (?:can|bottle|concentrate)s?",
                    "ready to drink", "rtd", "cans?", "bottles?", "canettes?")),
    ("tea", _rx("tea", "teas", "the (?:noir|vert|blanc|oolong|chai|earl grey)", "chai", "matcha", "tisanes?",
                "rooibos", "earl grey", "oolong")),
    ("syrup_food", _rx("syrups?", "sirops?", "sauces?", "chocolates?", "chocolat", "hot chocolate",
                       "cocoa", "cacao", "cookies?", "biscuits?", "granola", "candy", "milk", "lait", "oat ?milk", "creamer", "sugar", "sucre", "cascara")),
    ("bundle", _rx("bundles?", "samplers?", "starter packs?", "try these", "tasting packs?", "favou?rites packs?", "gift baskets?", "paniers?",
                   "ensembles?", "decouvertes?", "sample packs?", "variety packs?", "gift (?:box|set|pack|idea)s?",
                   "coffrets?", "boites? cadeaux?", "advent", "tasting (?:box|set|flight|kit)",
                   "trio", "duo", "discovery (?:box|pack)", "tasting flight")),
    ("cleaning", _rx("cleaning", "cleaner", "cafiza", "urnex", "descal(?:er|ing)", "detartrant", "rinza",
                     "dezcal", "grindz", "puly", "tablets?")),
    ("equipment", _rx(
        "grinders?", "moulins?", "kettles?", "bouilloires?", "drippers?", "brewers?", "filters? papers?", "filters",
        "paper filters?", "filtres?(?: papier)?", "scales?", "balances?", "french press(?:es)?", "aeropress",
        "chemex", "v60", "hario", "fellow", "bodum", "baratza", "comandante", "1zpresso", "timemore",
        "espresso machines?", "machines?", "tampers?", "portafilters?", "carafes?", "servers?", "pitchers?",
        "milk jugs?", "jugs?", "third wave water", "water filters?", "brewing water", "coffee makers?",
        "moccamaster", "technivorm", "breville", "la marzocco", "mahlkonig", "mahlkoenig", "rocket espresso",
        "equipment", "equipements?", "gear", "accessor(?:y|ies)", "accessoires?", "brewing", "moka pots?",
        "cafetieres?", "percolators?", "canisters?", "airscape", "clips?", "spoons?", "cuilleres?", "knock box",
        "brushes?", "brosses?", "replacement", "burrs?", "gaskets?", "parts?", "pieces", "baskets?",
        "refractometers?", "thermometers?", "trays?")),
    ("drinkware", _rx("mugs?", "tasses?", "cups?", "tumblers?", "glass(?:es)?", "verres?", "thermos",
                      "bottles?", "drinkware", "keepcup", "travel mug", "ceramics?")),
    ("merch", _rx("merch(?:andise)?", "t-?shirts?", "shirts?", "tees?", "hoodies?", "sweatshirts?", "crewnecks?",
                  "hats?", "caps?", "tuques?", "beanies?", "toques?", "totes?", "tote bags?", "stickers?",
                  "autocollants?", "aprons?", "tabliers?", "socks?", "posters?", "prints?", "books?",
                  "livres? de", "candles?", "chandelles?", "pins?", "keychains?", "ornaments?", "patch(?:es)?",
                  "bandanas?", "puzzles?", "cards?", "magnets?", "postcards?", "apparel", "vetements?", "coasters?",
                  "journals?", "notebooks?", "sacks?", "burlap", "jute")),
    ("service", _rx("workshops?", "ateliers?", "class(?:es)?", "cours", "courses?", "lessons?", "training",
                    "formations?", "tickets?", "events?", "carbon offset", "donations?", "dons?", "tips?",
                    "shipping", "livraison", "insurance", "deposit", "rental", "location", "repair", "catering",
                    "traiteur", "test product", "talks?", "tours?", "guided")),
    ("wholesale", _rx("wholesale", "b2b", "grossiste", "cafe ?service", "office coffee")),
]

# Words that also appear in genuine coffee names ("Espresso Bar Blend", "Swiss Water Decaf", "Filter
# Roast", "Chocolate Bomb"), so they only exclude when the roaster hasn't typed the product as coffee.
SOFT_CATEGORIES = {"syrup_food", "merch", "equipment"}
LIQUID_RX = re.compile(r"\d\s*(?:ml|l|litres?|liters?|cl)(?![a-z])|bouteilles?|bottles?|cans?(?![a-z])|canettes?")

# Strong evidence the product is coffee beans.
COFFEE_TYPE_RX = _rx(
    "coffee", "coffees", "cafe", "cafes", "espresso", "espressos", "blend", "blends", "melanges?",
    "single origins?", "origine unique", "beans?", "whole bean", "grains?", "roast", "roasts", "torrefaction",
    "decaf", "decafeine", "filter", "filtre", "sac de cafe", "seasonal", "micro ?lots?", "omni",
)
# Words that only make sense on a coffee: varietals, farm terms, processing.
COFFEE_NAME_RX = _rx(
    "gesha", "geisha", "sidra", "caturra", "catuai", "typica", "pacamara", "maragogype", "sl-?28",
    "sl-?34", "heirloom", "(?:pink|red|yellow|striped) bourbon", "bourbon pointu", "castillo", "wush ?wush", "pink bourbon", "java", "mokka", "laurina", "eugenioides",
    "finca", "hacienda", "fazenda", "estate", "cooperative", "cooperativa", "washing station", "station",
    "peaberry", "supremo", "excelso", "aa", "ab", "lot", "micro ?lot", "anaerobic", "washed", "natural",
    "honey process", "decaf", "decaffeinated", "espresso", "roast", "blend",
)
GRIND_OPTION_RX = _rx(
    "whole ?bean", "en grains?", "grains? entiers?", "ground", "moulu", "grind", "mouture", "espresso",
    "french press", "filter", "filtre", "drip", "pour ?over", "piston", "chemex", "aeropress", "moka",
    "cone", "percolator",
)

ORIGINS: dict[str, str] = {
    "ethiopia": "Ethiopia", "ethiopie": "Ethiopia", "ethiopian": "Ethiopia", "yirgacheffe": "Ethiopia",
    "sidamo": "Ethiopia", "guji": "Ethiopia",
    "kenya": "Kenya", "colombia": "Colombia", "colombie": "Colombia", "colombian": "Colombia",
    "brazil": "Brazil", "bresil": "Brazil", "brasil": "Brazil", "brazilian": "Brazil",
    "guatemala": "Guatemala", "honduras": "Honduras", "costa rica": "Costa Rica",
    "el salvador": "El Salvador", "salvador": "El Salvador", "nicaragua": "Nicaragua", "panama": "Panama",
    "peru": "Peru", "perou": "Peru", "bolivia": "Bolivia", "bolivie": "Bolivia", "ecuador": "Ecuador",
    "equateur": "Ecuador", "mexico": "Mexico", "mexique": "Mexico", "chiapas": "Mexico", "oaxaca": "Mexico",
    "rwanda": "Rwanda", "burundi": "Burundi", "uganda": "Uganda", "ouganda": "Uganda",
    "tanzania": "Tanzania", "tanzanie": "Tanzania", "congo": "DR Congo", "drc": "DR Congo",
    "yemen": "Yemen", "india": "India", "inde": "India", "indonesia": "Indonesia", "indonesie": "Indonesia",
    "sumatra": "Indonesia", "java": "Indonesia", "sulawesi": "Indonesia", "bali": "Indonesia",
    "flores": "Indonesia", "papua new guinea": "Papua New Guinea", "png": "Papua New Guinea",
    "papouasie": "Papua New Guinea", "timor": "Timor-Leste", "vietnam": "Vietnam", "yunnan": "China",
    "china": "China", "chine": "China", "myanmar": "Myanmar", "thailand": "Thailand", "laos": "Laos",
    "hawaii": "Hawaii", "kona": "Hawaii", "jamaica": "Jamaica", "jamaique": "Jamaica",
    "blue mountain": "Jamaica", "dominican": "Dominican Republic", "haiti": "Haiti", "cuba": "Cuba",
    "malawi": "Malawi", "zambia": "Zambia", "zimbabwe": "Zimbabwe", "philippines": "Philippines",
    "taiwan": "Taiwan", "nepal": "Nepal", "cameroon": "Cameroon", "cameroun": "Cameroon",
}
_ORIGIN_RX = _rx(*sorted(ORIGINS, key=len, reverse=True))

PROCESSES: list[tuple[str, re.Pattern[str]]] = [
    ("Anaerobic", _rx("anaerobic", "anaerobie", "anaerobique")),
    ("Carbonic", _rx("carbonic maceration", "carbonic", "maceration carbonique")),
    ("Co-ferment / infused", _rx("co-?ferment(?:ed|ation)?", "infused", "infuse", "thermal shock")),
    ("Honey", _rx("honey(?: process(?:ed)?)?", "miel", "pulped natural", "black honey", "red honey",
                  "yellow honey")),
    ("Natural", _rx("natural", "naturals", "nature", "naturel", "dry process(?:ed)?", "sun[- ]dried")),
    ("Washed", _rx("washed", "lave", "fully washed", "wet process(?:ed)?")),
    ("Wet-hulled", _rx("wet[- ]hulled", "giling basah")),
]

_ROAST_RX = [
    ("Medium-dark", re.compile(r"medium[\s_-]*dark|mi[\s-]*fonce")),
    ("Medium-light", re.compile(r"medium[\s_-]*light|mi[\s-]*(?:clair|pale)")),
    ("Light", re.compile(r"(?<![a-z])(?:light|pale|claire?|blonde?)(?![a-z])")),
    ("Medium", re.compile(r"(?<![a-z])(?:medium|moyenne?|medi?um)(?![a-z])")),
    ("Dark", re.compile(r"(?<![a-z])(?:dark|foncee?|french roast|italian roast)(?![a-z])")),
]
# Only trust a roast word when it is attached to "roast"/"torréfaction" or sits in a roast tag;
# otherwise "dark chocolate" tasting notes would mark everything dark.
_ROAST_CONTEXT = re.compile(
    r"(?:roast|torrefaction|torref|profile|degree)[\s_:-]*(?:level|profile)?[\s_:-]*"
    r"([a-z][a-z\s_-]{2,15})"
    r"|([a-z][a-z\s_-]{2,15}?)[\s_-]*(?:roast|blend|torrefaction|torrefie)"
)

DECAF_RX = _rx("decaf", "decafs", "decafeine", "decaffeinated", "decaffeine", "swiss water", "sans cafeine",
               "mountain water", "e\\.?a\\.? decaf", "sugar ?cane decaf")
HALF_CAF_RX = _rx("half ?caf", "demi-?cafeine", "half decaf")
BLEND_RX = _rx("blend", "blends", "melange", "assemblage", "house")
ESPRESSO_RX = _rx("espresso", "espressos")
FILTER_RX = _rx("filter", "filtre", "pour ?over", "drip")
SAMPLE_RX = _rx("samples?", "echantillons?", "tasters?", "mini")


@dataclass
class Classification:
    category: str  # coffee | <exclude category> | unknown
    reason: str
    origins: list[str] = field(default_factory=list)
    process: list[str] = field(default_factory=list)
    roast: str | None = None
    decaf: bool = False
    half_caf: bool = False
    blend: bool = False
    style: str | None = None  # espresso | filter | omni


def _first_match(rx: re.Pattern[str], *texts: str) -> str | None:
    for t in texts:
        if m := rx.search(t):
            return m.group(0)
    return None


def detect_roast(*texts: str) -> str | None:
    for text in texts:
        for m in _ROAST_CONTEXT.finditer(text):
            fragment = m.group(1) or m.group(2) or ""
            for name, rx in _ROAST_RX:
                if rx.search(fragment):
                    return name
    return None


def classify(
    title: str,
    product_type: str = "",
    tags: list[str] | None = None,
    option_names: list[str] | None = None,
    option_values: list[str] | None = None,
    has_weight: bool = False,
) -> Classification:
    t_title = fold(title)
    t_type = fold(product_type or "")
    t_tags = fold(" | ".join(tags or []))
    t_tags_spaced = t_tags.replace("_", " ")
    t_opts = fold(" | ".join((option_names or []) + (option_values or [])))

    # Exclusion categories win when they appear in the title or product type. The coffee words in
    # a title like "Coffee Mug" or "Café Subscription" don't rescue it.
    type_is_coffee = bool(COFFEE_TYPE_RX.search(t_type))
    for category, rx in EXCLUDE_RULES:
        hit = _first_match(rx, t_title, t_type)
        if hit is None:
            continue
        hit_only_in_title = rx.search(t_type) is None
        if category in SOFT_CATEGORIES and hit_only_in_title:
            # e.g. "Chocolate Bomb Espresso" typed as Coffee, "Swiss Water Decaf" in 340g bags,
            # "Apricot Cookie - Peru Yellow Caturra" (named after its tasting notes).
            title_is_coffee = bool(_ORIGIN_RX.search(t_title) or COFFEE_NAME_RX.search(t_title))
            if category == "syrup_food":
                if type_is_coffee or (title_is_coffee and not re.search(r"covered|enrobe", t_title)):
                    continue
            elif has_weight:
                continue  # sold in labelled bag sizes, e.g. "Espresso Bar Blend 340g"
        if category == "ready_to_drink" and not LIQUID_RX.search(t_title + " | " + t_opts):
            continue  # "Cold Brew Blend" sold as beans
        if category == "instant" and hit in {"can", "cans", "bottle", "bottles"} and (type_is_coffee or has_weight):
            continue
        if category == "drinkware" and hit in {"cup", "cups"} and re.search(r"cup of excellence|cupping", t_title):
            continue
        if category == "wholesale" and has_weight:
            break  # Bulk beans are still beans.
        return Classification(category=category, reason=f"'{hit}' in title/type")

    evidence = []
    if COFFEE_TYPE_RX.search(t_type):
        evidence.append("type")
    if COFFEE_TYPE_RX.search(t_tags_spaced) or "origin" in t_tags or "roast" in t_tags:
        evidence.append("tags")
    if GRIND_OPTION_RX.search(t_opts):
        evidence.append("grind options")
    if _ORIGIN_RX.search(t_title) or COFFEE_TYPE_RX.search(t_title) or COFFEE_NAME_RX.search(t_title):
        evidence.append("title")

    # Weight alone isn't enough (a bag of chocolate has a weight), but weight + any coffee signal is.
    if not evidence:
        return Classification(category="unknown", reason="no coffee signals")
    if not has_weight and evidence == ["tags"]:
        return Classification(category="unknown", reason="only tag evidence and no size")

    attr_text = [t_title, t_type, t_tags_spaced]
    origins = sorted({ORIGINS[m.group(0)] for t in attr_text for m in _ORIGIN_RX.finditer(t)})
    process = [name for name, rx in PROCESSES if any(rx.search(t) for t in attr_text)]
    # "natural" alone in tags is often a marketing word; require it in title or a process tag.
    if "Natural" in process and not (
        PROCESSES[4][1].search(t_title) or re.search(r"process[^|]*natur", t_tags_spaced)
    ):
        process.remove("Natural")
    decaf = bool(_first_match(DECAF_RX, t_title, t_type, t_tags_spaced))
    half_caf = bool(_first_match(HALF_CAF_RX, t_title, t_type))
    blend = bool(_first_match(BLEND_RX, t_title, t_type)) or len(origins) > 1
    is_esp = bool(_first_match(ESPRESSO_RX, t_title, t_type, t_tags_spaced))
    is_fil = bool(_first_match(FILTER_RX, t_title, t_type, t_tags_spaced))
    style = "omni" if (is_esp and is_fil) or "omni" in t_title + t_tags else "espresso" if is_esp else "filter" if is_fil else None

    return Classification(
        category="coffee",
        reason="+".join(evidence),
        origins=origins,
        process=process,
        roast=detect_roast(t_tags_spaced, t_title, t_type),
        decaf=decaf and not half_caf,
        half_caf=half_caf,
        blend=blend,
        style=style,
    )


def is_sample(variant_title: str, grams: float | None) -> bool:
    return bool(SAMPLE_RX.search(fold(variant_title or ""))) or (grams is not None and grams < 150)
