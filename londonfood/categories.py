"""Map OpenStreetMap tags onto the venue categories we care about."""

import re

# category -> cuisine tag values (OSM `cuisine=*`, semicolon separated)
CUISINE_MAP = {
    "Deli": {"deli", "delicatessen", "sandwich"},
    "Chinese": {"chinese", "cantonese", "sichuan", "szechuan", "dim_sum", "hong_kong", "taiwanese", "dumpling"},
    "Japanese": {"japanese", "ramen", "udon", "izakaya", "katsu"},
    "Sushi": {"sushi"},
    "Korean": {"korean"},
    "Asian": {"asian", "pan_asian", "thai", "vietnamese", "malaysian", "indonesian", "filipino",
              "singaporean", "burmese", "chinese", "japanese", "korean", "sushi", "ramen", "noodle"},
    "Indian": {"indian", "pakistani", "bangladeshi", "nepalese", "sri_lankan", "curry", "balti", "punjabi"},
    "Halal": {"halal"},
    "Pizza": {"pizza"},
    "Italian": {"italian", "pasta", "italian_pizza"},
    "Tacos": {"tacos", "taco"},
    "Mexican": {"mexican", "tex-mex", "tex_mex", "tacos", "taco", "burrito"},
    "Spanish": {"spanish", "tapas", "catalan", "basque"},
    "Burger": {"burger"},
    "American": {"american", "diner", "southern", "hot_dog"},
    "Greek": {"greek", "cypriot", "gyros", "souvlaki"},
    "French": {"french", "crepe", "bistro", "brasserie"},
    "BBQ": {"bbq", "barbecue", "smokehouse"},
    "Steak": {"steak_house", "steak", "steakhouse", "grill"},
    "Bakery": {"bakery", "pastry", "cake"},
    "Breakfast": {"breakfast", "brunch", "coffee_shop"},
    "Donuts": {"donut", "doughnut", "donuts", "doughnuts"},
    "Bagels": {"bagel", "bagels"},
    "Ice cream": {"ice_cream", "gelato", "frozen_yogurt", "frozen_yoghurt", "dessert"},
    "Seafood": {"seafood", "fish_and_chips", "fish", "oyster", "fish_chips"},
    "Thai": {"thai", "isan", "northern_thai", "southern_thai", "thai_street_food"},
    "Western": {"western", "international", "european", "fusion", "british", "australian", "german"},
}

# category -> regex matched against the venue name when tags are missing
NAME_HINTS = {
    "Deli": r"\bdeli\b|delicatessen",
    "Chinese": r"chinese|dim sum|wok\b|szechuan|sichuan",
    "Japanese": r"japanese|ramen|izakaya|yakitori",
    "Sushi": r"sushi",
    "Korean": r"korean",
    "Indian": r"indian|tandoor|curry|masala|balti|biryani",
    "Halal": r"halal",
    "Pizza": r"pizz",
    "Italian": r"trattoria|osteria|ristorante|italian|pasta",
    "Tacos": r"taco|taquer",
    "Mexican": r"mexican|burrito|cantina",
    "Spanish": r"tapas|spanish",
    "Burger": r"burger",
    "American": r"diner|american",
    "Greek": r"greek|souvlaki|gyro",
    "Food truck": r"food truck|street food",
    "French": r"bistro|brasserie|french|creperie",
    "BBQ": r"bbq|barbecue|smokehouse",
    "Steak": r"steak",
    "Bakery": r"bakery|bakehouse|patisserie|boulangerie",
    "Breakfast": r"breakfast|brunch",
    "Donuts": r"donut|doughnut",
    "Bagels": r"bagel|beigel",
    "Ice cream": r"ice cream|gelat|creamery",
    "Seafood": r"seafood|fish|oyster|chippy|lobster",
}
NAME_HINTS = {k: re.compile(v, re.I) for k, v in NAME_HINTS.items()}

CATEGORIES = list(CUISINE_MAP) + ["Food truck", "Pub"]


def categorize(tags):
    """Return a sorted list of category labels for an OSM element's tags."""
    cats = set()
    cuisines = {c.strip().lower() for c in re.split(r"[;,]", tags.get("cuisine", "")) if c.strip()}
    for cat, values in CUISINE_MAP.items():
        if cuisines & values:
            cats.add(cat)

    amenity = tags.get("amenity", "")
    shop = tags.get("shop", "")
    if amenity == "pub":
        cats.add("Pub")
    if amenity == "ice_cream" or shop == "ice_cream":
        cats.add("Ice cream")
    if shop in ("bakery", "pastry"):
        cats.add("Bakery")
    if shop == "deli":
        cats.add("Deli")
    if (tags.get("street_vendor") == "yes" or tags.get("mobile") == "yes"
            or amenity == "food_truck" or tags.get("vending") == "food"):
        cats.add("Food truck")
    if tags.get("diet:halal") in ("yes", "only"):
        cats.add("Halal")

    name = tags.get("name", "")
    for cat, rx in NAME_HINTS.items():
        if rx.search(name):
            cats.add(cat)

    if not cats:
        cats.add({"cafe": "Cafe", "bar": "Bar", "fast_food": "Takeaway",
                  "food_court": "Food court", "biergarten": "Pub"}.get(amenity, "Restaurant"))
    return sorted(cats)
