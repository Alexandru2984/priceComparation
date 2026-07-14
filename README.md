# PriceMatch Local

Aplicație personală pentru compararea achizițiilor unui magazin alimentar cu prețurile METRO. Rulează local, fără API-uri plătite.

## Ce face MVP-ul

- catalog incremental de produse urmărite;
- prețuri METRO introduse manual sau importate din CSV;
- facturi și bonuri introduse manual, din text OCR, imagine sau PDF;
- bonuri lungi încărcate din maximum 12 fotografii, procesate în ordine;
- OCR local cu Tesseract (`ron+eng`);
- structurarea textului cu Ollama și JSON Schema, cu parser simplu de rezervă;
- asociere locală fuzzy și memorarea corecțiilor;
- comparație exactă per BUC/KG/L folosind `Decimal`;
- coadă vizuală pentru potrivirile care necesită verificare.
- actualizarea automată a prețurilor de referință din documentele METRO confirmate.

## Instalare rapidă pe Ubuntu/Debian

```bash
sudo apt update
sudo apt install -y python3-venv tesseract-ocr tesseract-ocr-ron tesseract-ocr-eng postgresql
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
sudo -u postgres createuser --createdb --pwprompt pricecompare
sudo -u postgres createdb --owner=pricecompare pricecompare
cp .env.example .env
.venv/bin/python manage.py migrate
.venv/bin/python manage.py runserver 127.0.0.1:8010
```

Deschide `http://127.0.0.1:8010`. Portul 8010 este folosit implicit fiindcă portul 8000 este deja ocupat pe acest calculator.

Configurația implicită din `.env.example` folosește PostgreSQL prin TCP și necesită setarea parolei pentru
rolul `pricecompare`. Pentru autentificare locală `peer`, lasă `DB_PASSWORD` și `DB_HOST` goale și setează
`DB_USER` la utilizatorul Linux care rulează aplicația. SQLite rămâne disponibil cu `DB_ENGINE=sqlite`.

După prima instalare, aplicația poate fi pornită simplu cu:

```bash
./start.sh
```

## Ollama (opțional, recomandat)

Instalează Ollama din sursa oficială, apoi descarcă modelul local:

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull gemma3:4b
```

Aplicația apelează implicit `http://localhost:11434/api/chat`. Dacă Ollama nu rulează, facturile cu format simplu sunt procesate cu parserul local, iar orice linie poate fi introdusă sau corectată manual.

Pentru a opri complet încercarea de conectare la Ollama:

```bash
export OLLAMA_ENABLED=0
```

## Import METRO

Poți începe cu [sample_data/metro_prices.csv](sample_data/metro_prices.csv). Coloanele obligatorii sunt:

- `name`
- `base_unit`: `BUC`, `KG` sau `L`
- `price_gross`

Coloanele opționale sunt `brand`, `ean`, `units_per_package`, `unit_size`, `valid_from` și `source`.

Exemplu: un bax cu 6 sticle de 2 L are `units_per_package=6`, `unit_size=2`, iar `price_gross` este prețul întregului bax.

### Sincronizare din bonurile și facturile METRO

1. Creează furnizorul METRO și bifează `este METRO`.
2. Încarcă factura sau bonul. Pentru un bon lung selectează toate fotografiile în ordinea de sus în jos.
3. Verifică fiecare linie și debifează `necesită verificare`.
4. La salvare, aplicația creează produsul lipsă și actualizează automat prețul METRO.

Acest flux folosește documentele firmei tale și nu necesită acces la site-ul METRO.

Pentru o sincronizare live complet automată, fără fereastră Chrome și fără confirmare, este necesar un
feed/API autorizat și magazinul METRO relevant, deoarece prețurile și disponibilitatea depind de locație și de cont. Un mesaj gata de trimis către METRO
se află în [docs/CERERE_INTEGRARE_METRO.md](docs/CERERE_INTEGRARE_METRO.md).

### Scanare asistată cu Selenium

Din `Prețuri METRO → Scanare Selenium` poți porni un browser Chrome separat. Profilul browserului se
păstrează local în `data/metro_chrome_profile`, astfel încât magazinul și sesiunea aleasă pot rămâne active.
Parola nu este citită și nu este salvată de aplicația Django.

Panoul verde injectat în pagina METRO oferă:

- `Capturează pagina`: colectează cardurile din categoria sau căutarea vizibilă;
- `Actualizează lista urmărită`: caută automat maximum 150 de produse existente în catalogul local;
- `Finalizează`: închide scanarea și lasă produsele în staging.

Sunt colectate numai denumirea, codul METRO din URL, ambalarea/gramajul, prețul cu TVA, magazinul și
momentul capturii. Imaginile și descrierile comerciale nu sunt descărcate. Înainte de import poți corecta
orice gramaj, preț sau asociere.

Pentru a popula automat un catalog inițial cu produse alimentare de bază folosind magazinul păstrat în
profilul Chrome:

```bash
.venv/bin/python manage.py metro_seed_catalog
```

Comanda caută controlat lactate, băuturi, alcool, fructe, legume și produse de băcănie, cu maximum 8
rezultate per căutare. Pentru o selecție proprie poți transmite termenii explicit, de exemplu:

```bash
.venv/bin/python manage.py metro_seed_catalog lapte iaurt banane --limit-per-search 12
```

## Teste

```bash
.venv/bin/python manage.py test
```

## Observație importantă

AI/OCR extrage și propune date. Calculele financiare sunt realizate determinist în Django. Verifică manual TVA-ul, ambalarea și potrivirile marcate înainte de a lua o decizie de cumpărare.
