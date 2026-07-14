import unicodedata


CATEGORY_SEARCH_TERMS = {
    "Lactate": ["iaurt", "smantana", "lapte", "branza", "crema de branza", "unt", "cascaval"],
    "Băuturi nealcoolice": ["suc", "apa plata", "apa minerala", "energizant", "ceai rece"],
    "Băuturi alcoolice": ["bere", "vin", "vodca", "whisky", "rom", "gin", "lichior"],
    "Fructe și legume": [
        "mere", "banane", "portocale", "rosii", "cartofi", "ceapa", "ardei", "castraveti",
    ],
    "Băcănie": ["ulei", "zahar", "faina", "orez", "paste"],
    "Ouă": ["oua"],
    "Conserve și pate": ["conserve", "conserve peste", "conserve legume", "pate"],
    "Mezeluri": ["mezeluri", "salam", "sunca", "parizer", "crenvursti"],
    "Dulciuri": ["ciocolata", "bomboane", "biscuiti", "napolitane", "croissant", "guma de mestecat", "acadele"],
    "Snacks": ["chipsuri", "pufuleti", "popcorn", "covrigei", "seminte", "alune", "crackers"],
    "Igienă personală": [
        "pasta de dinti", "periuta de dinti", "sapun", "sampon", "gel de dus", "deodorant",
        "hartie igienica", "servetele umede",
    ],
    "Curățenie": [
        "detergent vase", "detergent rufe", "balsam rufe", "inalbitor", "dezinfectant", "saci menajeri",
    ],
    "Cafea și ceai": ["cafea", "cafea boabe", "cafea instant", "ceai"],
    "Sosuri și condimente": ["mustar", "ketchup", "maioneza", "bulion", "condimente", "sos tomate"],
    "Panificație": ["paine", "chifle", "lipie", "toast"],
    "Congelate": ["pizza congelata", "legume congelate", "cartofi congelati", "inghetata"],
}

CATEGORY_CHOICES = [(name, name) for name in [*CATEGORY_SEARCH_TERMS, "Altele"]]

CATEGORY_KEYWORDS = {
    "Igienă personală": [
        "pasta de dinti", "periuta", "sapun", "sampon", "gel de dus", "deodorant", "hartie igienica",
        "servetele umede",
    ],
    "Curățenie": [
        "detergent", "balsam rufe", "inalbitor", "dezinfectant", "saci menajeri", "solutie curatat",
    ],
    "Dulciuri": [
        "ciocolata", "bomboane", "biscuit", "napolitan", "croissant", "guma", "acadea", "coji dulci",
    ],
    "Snacks": ["chips", "pufuleti", "popcorn", "covrigei", "seminte", "alune", "crackers"],
    "Mezeluri": [
        "salam", "sunca", "parizer", "crenvursti", "cremwursti", "jambon", "prosciutto", "muschi file",
        "ciolan afumat",
    ],
    "Conserve și pate": [
        "pate", "conserva", "ton in", "macrou", "sprot", "hering", "fasole", "mazare", "porumb",
        "ciuperci", "masline", "castraveti in otet", "sfecla", "pasta vegetala", "compot", "gem ",
    ],
    "Lactate": ["iaurt", "smantana", "lapte", "branza", "cascaval", "unt ", "crema de branza"],
    "Ouă": ["oua", "albus de ou", "galbenus de ou"],
    "Băuturi alcoolice": [
        "bere", "vin ", "vodca", "vodka", "whisky", "rom ", "gin ", "lichior", "secco",
    ],
    "Băuturi nealcoolice": ["apa ", "suc ", "nectar", "energizant", "ceai rece"],
    "Cafea și ceai": ["cafea", "ceai"],
    "Sosuri și condimente": ["mustar", "ketchup", "maioneza", "bulion", "condiment", "sos tomate"],
    "Panificație": ["paine", "chifle", "lipie", "toast"],
    "Congelate": ["congelat", "inghetata", "pizza"],
    "Fructe și legume": [
        "mere", "banane", "portocale", "rosii", "cartofi", "ceapa", "ardei", "castraveti", "ridichi",
    ],
    "Băcănie": ["ulei", "zahar", "faina", "orez", "paste"],
}


def _normalized(value):
    return unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode("ascii").lower()


def infer_category(name):
    normalized = f" {_normalized(name)} "
    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(_normalized(keyword) in normalized for keyword in keywords):
            return category
    return "Altele"
