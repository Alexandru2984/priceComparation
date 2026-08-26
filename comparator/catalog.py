import unicodedata


CATEGORY_SEARCH_TERMS = {
    "Lactate": [
        "iaurt", "iaurt grecesc", "iaurt de baut", "smantana", "lapte", "lapte UHT", "lapte batut",
        "chefir", "sana", "branza", "telemea", "branza topita", "crema de branza", "unt", "cascaval",
        "mozzarella", "mascarpone", "desert lactat", "budinca", "lapte condensat", "branza cottage",
        "feta", "halloumi", "parmezan", "ricotta", "lapte fara lactoza", "iaurt fara lactoza",
        "bautura vegetala", "lapte migdale", "lapte ovaz", "lapte soia", "branza capra",
        "branza burduf", "urda", "gouda", "emmentaler",
    ],
    "Băuturi nealcoolice": [
        "suc", "suc carbogazos", "cola", "limonada", "apa plata", "apa minerala", "apa aromatizata",
        "energizant", "ceai rece", "sirop", "nectar", "apa tonica", "bautura sport", "suc copii",
        "bautura aloe vera", "bautura cocos", "ginger beer", "bautura izotonica", "apa vitamine",
        "suc bio", "smoothie", "must", "socata", "bautura instant",
    ],
    "Băuturi alcoolice": [
        "bere", "bere doza", "bere sticla", "cidru", "vin", "vin spumant", "prosecco", "vodca",
        "whisky", "rom", "gin", "lichior", "coniac", "palinca", "vermut", "tequila", "aperitiv",
        "bitter", "rachiu", "bere fara alcool", "sampanie", "vin rose", "vin alb", "vin rosu",
        "crema whisky", "alcool sanitar", "tuica", "brandy",
    ],
    "Fructe și legume": [
        "mere", "pere", "banane", "portocale", "mandarine", "lamai", "struguri", "pepene",
        "kiwi", "avocado", "rosii", "cartofi", "ceapa", "usturoi", "ardei", "castraveti",
        "varza", "morcovi", "ciuperci proaspete", "salata verde", "grapefruit", "pomelo", "ananas",
        "mango", "piersici", "nectarine", "prune", "capsuni", "afine", "zmeura", "dovlecei",
        "vinete", "broccoli", "conopida", "telina", "sfecla", "spanac", "ridichi", "patrunjel",
        "marar", "leustean", "busuioc proaspat", "ghimbir", "porumb fiert", "fasole verde",
        "mazare proaspata", "cartof dulce", "dovleac", "rucola", "salata iceberg", "mix salata",
    ],
    "Băcănie": [
        "ulei", "ulei masline", "zahar", "faina", "malai", "gris", "orez", "paste", "cereale",
        "fulgi ovaz", "sare", "drojdie", "pesmet", "quinoa", "couscous", "bulgur", "linte",
        "naut uscat", "fasole uscata", "seminte chia", "indulcitor", "taitei", "noodles",
        "piure instant", "praf de copt", "zahar vanilat", "esenta vanilie", "gelatina",
        "amidon", "budinca praf", "muesli", "granola", "faina integrala", "faina fara gluten",
    ],
    "Ouă": ["oua"],
    "Conserve și pate": [
        "conserve", "conserve peste", "ton conserva", "macrou conserva", "conserve legume", "fasole conserva",
        "porumb conserva", "mazare conserva", "rosii conserva", "pate", "pate vegetal", "zacusca", "muraturi",
        "masline", "gem", "compot", "lapte cocos", "crema cocos", "ardei copti conserva",
        "sardine conserva", "hering conserva", "sprot conserva", "naut conserva", "linte conserva",
        "castraveti murati", "capere", "ciuperci conserva", "salata icre", "hummus",
    ],
    "Mezeluri": [
        "mezeluri", "salam", "salam uscat", "sunca", "parizer", "crenvursti", "carnati", "kaizer",
        "bacon", "jambon", "muschi file", "prosciutto", "pastrama", "toba", "lebar", "caltabos",
        "mortadella", "pepperoni", "salam feliat", "sunca presata",
    ],
    "Dulciuri": [
        "ciocolata", "batoane ciocolata", "bomboane", "bomboane gumate", "biscuiti", "fursecuri",
        "napolitane", "croissant", "prajituri ambalate", "guma de mestecat", "acadele", "halva",
        "cozonac", "crema cacao", "jeleuri", "marshmallow", "batoane cereale", "praline",
        "drajeuri", "dropsuri", "eugenia", "turta dulce", "vafe", "briose", "chec ambalat",
    ],
    "Snacks": [
        "chipsuri", "tortilla chips", "pufuleti", "popcorn", "covrigei", "sticksuri", "seminte", "alune",
        "fistic", "caju", "crackers", "mix nuci", "migdale", "nuci", "arahide", "nachos",
        "salsa chips", "porumb copt", "biscuiti sarati", "grisine", "snack proteic",
    ],
    "Igienă personală": [
        "pasta de dinti", "periuta de dinti", "sapun", "sampon", "gel de dus", "deodorant",
        "hartie igienica", "servetele umede", "servetele hartie", "prosoape hartie", "apa de gura",
        "ata dentara", "aparat ras", "spuma ras", "absorbante", "tampoane", "vata", "plasturi",
        "balsam par", "crema maini", "crema corp", "dischete demachiante", "gel intim",
        "fixativ", "ceara par", "vopsea par", "demachiant", "apa micelara", "crema fata",
        "protectie solara", "parfum", "apa toaleta", "betisoare urechi", "prezervative",
    ],
    "Curățenie": [
        "detergent vase", "detergent rufe", "capsule rufe", "balsam rufe", "inalbitor", "dezinfectant",
        "solutie geamuri", "solutie pardoseli", "odorizant", "bureti vase", "lavete", "saci menajeri",
        "degresant", "anticalcar", "detergent toaleta", "manusi menaj", "mop", "solutie mobila",
        "solutie baie", "solutie bucatarie", "tablete masina vase", "sare masina vase",
        "odorizant toaleta", "insecticid", "capcane insecte", "matura", "faras", "galeata",
    ],
    "Cafea și ceai": [
        "cafea", "cafea macinata", "cafea boabe", "cafea capsule", "cafea instant", "ceai", "ceai plicuri",
        "cacao", "ciocolata calda", "filtre cafea", "cicoare", "matcha", "ceai verde",
        "ceai fructe", "ceai plante", "cafea decofeinizata",
    ],
    "Sosuri și condimente": [
        "mustar", "ketchup", "maioneza", "bulion", "pasta tomate", "condimente", "piper", "boia",
        "sos tomate", "sos paste", "sos soia", "otet", "zeama lamaie", "sos barbecue", "sos iute",
        "pesto", "hrean", "sos usturoi", "sos burger", "sos sweet chili", "sos teriyaki",
        "sos worcester", "sos tzatziki", "mix condimente", "vegeta", "scortisoara", "foi dafin",
    ],
    "Panificație": [
        "paine", "bagheta", "chifle", "lipie", "toast", "tortilla", "pesmeti", "coji pizza",
        "paine integrala", "paine fara gluten", "focaccia", "croissant proaspat", "blat pizza",
    ],
    "Congelate": [
        "pizza congelata", "legume congelate", "fructe congelate", "cartofi congelati", "peste congelat",
        "carne congelata", "aluat congelat", "inghetata", "placinta congelata", "foietaj congelat",
        "fructe mare congelate", "snitel congelat", "nuggets", "burger congelat", "prajitura congelata",
    ],
    "Carne și pește": [
        "carne pui", "piept pui", "pulpe pui", "carne porc", "ceafa porc", "cotlet porc", "carne vita",
        "carne tocata", "peste proaspat", "somon", "pastrav", "aripioare pui", "ficat pui", "mici",
        "burger", "dorada", "fructe de mare", "curcan", "rata", "iepure", "coaste porc",
        "ciolan porc", "muschi vita", "creveti", "calamar", "midii", "file peste", "ton proaspat",
    ],
    "Produse pentru copii": [
        "scutece", "servetele bebelusi", "lapte praf", "mancare bebelusi", "piure bebelusi", "sampon copii",
    ],
    "Hrană animale": [
        "hrana caini", "hrana pisici", "conserve caini", "conserve pisici", "nisip pisici",
        "recompense caini", "recompense pisici", "hrana pasari", "hrana pesti", "accesorii animale",
    ],
    "Menaj și consumabile": [
        "folie aluminiu", "folie alimentara", "hartie copt", "pungi alimentare", "pahare unica folosinta",
        "farfurii unica folosinta", "tacamuri unica folosinta", "caserole", "scobitori", "chibrituri",
        "brichete", "baterii alcaline", "bec led", "lumanari", "folie stretch", "pungi congelator",
        "pungi zip", "role casa marcat", "etichete pret", "sfoara alimentara",
    ],
    "Semipreparate": [
        "sandwich", "salata gata preparata", "mancare gatita", "supa gata", "ciorba gata",
        "pizza proaspata", "lasagna", "paste gata", "orez gata", "snitel", "chiftele", "falafel",
        "shaorma", "hot dog", "foietaj", "aluat pizza", "maioneza salata", "humus",
    ],
    "Produse dietetice": [
        "fara gluten", "fara zahar", "fara lactoza", "produs vegan", "produs bio", "produs proteic",
        "bautura proteica", "pudra proteica", "baton proteic", "cracker integral", "paste integrale",
        "paine proteica", "dulceata fara zahar", "sirop agave", "stevia", "unt arahide",
    ],
    "Papetărie și birou": [
        "pix", "creion", "marker", "caiet", "hartie copiator", "plicuri", "dosar", "biblioraft",
        "banda adeziva", "foarfeca", "capse", "capsator", "etichete adezive", "calculator birou",
    ],
    "Bucătărie și veselă": [
        "pahare sticla", "cani", "farfurii", "boluri", "tavi aluminiu", "hartie profesionala copt",
        "cutii depozitare", "ustensile bucatarie", "cutite bucatarie", "tocator", "oala", "tigaie",
        "termometru bucatarie", "manusi unica folosinta",
    ],
}

CATEGORY_CHOICES = [(name, name) for name in [*CATEGORY_SEARCH_TERMS, "Altele"]]

# Broad queries are intentionally few: this mode clicks through every METRO
# result page and is the quickest safe way to grow an initial local catalog.
CATEGORY_BREADTH_TERMS = {
    "Lactate": ["lapte", "iaurt", "branza"],
    "Băuturi nealcoolice": ["suc", "apa", "energizant"],
    "Băuturi alcoolice": ["bere", "vin", "whisky"],
    "Fructe și legume": ["fructe", "legume"],
    "Băcănie": ["paste", "orez", "faina", "ulei"],
    "Ouă": ["oua"],
    "Conserve și pate": ["conserve", "pate"],
    "Mezeluri": ["salam", "sunca", "carnati"],
    "Dulciuri": ["ciocolata", "biscuiti", "bomboane"],
    "Snacks": ["chipsuri", "snacks", "alune"],
    "Igienă personală": ["pasta de dinti", "sampon", "sapun"],
    "Curățenie": ["detergent", "solutie curatare"],
    "Cafea și ceai": ["cafea", "ceai"],
    "Sosuri și condimente": ["sos", "condimente"],
    "Panificație": ["paine", "chifle"],
    "Congelate": ["congelat", "inghetata"],
    "Carne și pește": ["carne", "peste"],
    "Produse pentru copii": ["bebelusi", "scutece"],
    "Hrană animale": ["hrana caini", "hrana pisici"],
    "Menaj și consumabile": ["folie", "pungi", "servetele"],
    "Semipreparate": ["mancare gata", "sandwich"],
    "Produse dietetice": ["fara zahar", "fara gluten", "proteic"],
    "Papetărie și birou": ["papetarie", "caiet", "pix"],
    "Bucătărie și veselă": ["pahare", "farfurii", "tigaie"],
}

CATEGORY_KEYWORDS = {
    "Papetărie și birou": [
        "pix ", "creion", "marker", "caiet", "hartie copiator", "plicuri", "biblioraft", "capsator",
    ],
    "Bucătărie și veselă": [
        "pahare sticla", "cana ", "cani ", "farfurii", "boluri", "tava aluminiu", "tavi aluminiu",
        "cutit bucatarie", "tocator", "tigaie", "oala ",
    ],
    "Produse dietetice": [
        "fara gluten", "fara zahar", "vegan", "proteic", "proteina", "sirop agave", "stevia",
    ],
    "Semipreparate": [
        "sandwich", "gata preparat", "mancare gatita", "lasagna", "falafel", "shaorma", "hot dog",
    ],
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
