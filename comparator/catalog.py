import unicodedata


CATEGORY_SEARCH_TERMS = {
    "Lactate": [
        "iaurt", "iaurt grecesc", "iaurt de baut", "smantana", "lapte", "lapte UHT", "lapte batut",
        "chefir", "sana", "branza", "telemea", "branza topita", "crema de branza", "unt", "cascaval",
        "mozzarella", "mascarpone",
    ],
    "Băuturi nealcoolice": [
        "suc", "suc carbogazos", "cola", "limonada", "apa plata", "apa minerala", "apa aromatizata",
        "energizant", "ceai rece", "sirop", "nectar",
    ],
    "Băuturi alcoolice": [
        "bere", "bere doza", "bere sticla", "cidru", "vin", "vin spumant", "prosecco", "vodca",
        "whisky", "rom", "gin", "lichior", "coniac", "palinca", "vermut",
    ],
    "Fructe și legume": [
        "mere", "pere", "banane", "portocale", "mandarine", "lamai", "struguri", "pepene",
        "kiwi", "avocado", "rosii", "cartofi", "ceapa", "usturoi", "ardei", "castraveti",
        "varza", "morcovi", "ciuperci proaspete", "salata verde",
    ],
    "Băcănie": [
        "ulei", "ulei masline", "zahar", "faina", "malai", "gris", "orez", "paste", "cereale",
        "fulgi ovaz", "sare", "drojdie", "pesmet",
    ],
    "Ouă": ["oua"],
    "Conserve și pate": [
        "conserve", "conserve peste", "ton conserva", "macrou conserva", "conserve legume", "fasole conserva",
        "porumb conserva", "mazare conserva", "rosii conserva", "pate", "pate vegetal", "zacusca", "muraturi",
        "masline", "gem", "compot",
    ],
    "Mezeluri": [
        "mezeluri", "salam", "salam uscat", "sunca", "parizer", "crenvursti", "carnati", "kaizer",
        "bacon", "jambon", "muschi file", "prosciutto",
    ],
    "Dulciuri": [
        "ciocolata", "batoane ciocolata", "bomboane", "bomboane gumate", "biscuiti", "fursecuri",
        "napolitane", "croissant", "prajituri ambalate", "guma de mestecat", "acadele", "halva",
        "cozonac", "crema cacao",
    ],
    "Snacks": [
        "chipsuri", "tortilla chips", "pufuleti", "popcorn", "covrigei", "sticksuri", "seminte", "alune",
        "fistic", "caju", "crackers", "mix nuci",
    ],
    "Igienă personală": [
        "pasta de dinti", "periuta de dinti", "sapun", "sampon", "gel de dus", "deodorant",
        "hartie igienica", "servetele umede", "servetele hartie", "prosoape hartie", "apa de gura",
        "ata dentara", "aparat ras", "spuma ras", "absorbante", "tampoane", "vata", "plasturi",
    ],
    "Curățenie": [
        "detergent vase", "detergent rufe", "capsule rufe", "balsam rufe", "inalbitor", "dezinfectant",
        "solutie geamuri", "solutie pardoseli", "odorizant", "bureti vase", "lavete", "saci menajeri",
    ],
    "Cafea și ceai": [
        "cafea", "cafea macinata", "cafea boabe", "cafea capsule", "cafea instant", "ceai", "ceai plicuri",
        "cacao", "ciocolata calda",
    ],
    "Sosuri și condimente": [
        "mustar", "ketchup", "maioneza", "bulion", "pasta tomate", "condimente", "piper", "boia",
        "sos tomate", "sos paste", "sos soia", "otet", "zeama lamaie",
    ],
    "Panificație": ["paine", "bagheta", "chifle", "lipie", "toast", "tortilla", "pesmeti", "coji pizza"],
    "Congelate": [
        "pizza congelata", "legume congelate", "fructe congelate", "cartofi congelati", "peste congelat",
        "carne congelata", "aluat congelat", "inghetata",
    ],
    "Carne și pește": [
        "carne pui", "piept pui", "pulpe pui", "carne porc", "ceafa porc", "cotlet porc", "carne vita",
        "carne tocata", "peste proaspat", "somon", "pastrav",
    ],
    "Produse pentru copii": [
        "scutece", "servetele bebelusi", "lapte praf", "mancare bebelusi", "piure bebelusi", "sampon copii",
    ],
    "Hrană animale": [
        "hrana caini", "hrana pisici", "conserve caini", "conserve pisici", "nisip pisici",
    ],
    "Menaj și consumabile": [
        "folie aluminiu", "folie alimentara", "hartie copt", "pungi alimentare", "pahare unica folosinta",
        "farfurii unica folosinta", "tacamuri unica folosinta", "caserole", "scobitori", "chibrituri",
    ],
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
    "Carne și pește": ["carne", "piept pui", "pulpe", "ceafa", "cotlet", "somon", "pastrav", "peste proaspat"],
    "Produse pentru copii": ["scutece", "bebelus", "bebelusi", "lapte praf"],
    "Hrană animale": ["hrana caini", "hrana pisici", "nisip pisici"],
    "Menaj și consumabile": [
        "folie aluminiu", "folie alimentara", "hartie copt", "pungi alimentare", "unica folosinta",
        "caserole", "scobitori", "chibrituri",
    ],
}


def _normalized(value):
    return unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode("ascii").lower()


def infer_category(name):
    normalized = f" {_normalized(name)} "
    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(_normalized(keyword) in normalized for keyword in keywords):
            return category
    return "Altele"
