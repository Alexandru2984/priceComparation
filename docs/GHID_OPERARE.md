# Ghid practic PriceMatch

Acest ghid acoperă folosirea zilnică a aplicației și administrarea scraperului METRO. Toate datele reale
sunt private, în PostgreSQL; pagina publică `/` afișează doar date demonstrative.

## 1. Pornire și verificare

Serviciile pornesc automat odată cu sistemul. Verifică-le cu:

```bash
systemctl is-active pricematch pricematch-worker caddy
systemctl list-timers 'pricematch-*'
```

Răspunsul normal este `active`. Aplicația locală este la `http://127.0.0.1:8010`, iar din rețeaua locală
la adresa HTTPS configurată în Caddy. După login, pagina `Stare` verifică PostgreSQL, Tesseract, Ollama,
backupul, spațiul pe disc, METRO și MFA.

Dacă ai schimbat codul sau `.env`:

```bash
.venv/bin/python manage.py migrate
sudo systemctl restart pricematch pricematch-worker
```

## 2. Fluxul zilnic recomandat

1. Intră în `Documente → + Document`.
2. Alege `Factură`, `Bon fiscal` sau lipește textul extras manual.
3. Încarcă PDF-ul ori fotografiile în ordinea de sus în jos și lasă activă procesarea automată.
4. Urmărește `Documente → Inbox procesare`.
5. În document, verifică scorul OCR, cantitatea, baxul, gramajul, TVA, reducerea și SGR.
6. Confirmă potrivirea produselor și debifează `necesită verificare` numai după control.
7. Folosește `Cumpărături` pentru alegerea sursei și `Raport` pentru controlul săptămânal.

Corecțiile confirmate devin aliasuri locale pentru furnizor. Dacă un anumit model de factură se citește
slab, intră în `Furnizori → Profil parsare` și alege reguli locale, mod automat sau Ollama local.

## 3. Scraperul METRO din interfață

Intră în `Prețuri METRO → Scanare Selenium`. Nu porni două scanări simultan și închide orice fereastră
Chrome care folosește profilul PriceMatch înainte de o scanare automată.

Ai trei variante:

- `Extindere rapidă` — recomandată pentru populare; parcurge complet 60 de căutări largi din 24 de
  categorii și apasă automat toate controalele „Arată încă 24 rezultate”;
- `Catalog complet` — peste 500 de căutări specifice; durează mult și este potrivit pentru o scanare rară;
- `Scanare manuală` — deschide Chrome; navighezi la categoria dorită, apoi folosești panoul verde.

Scanările automate selectează `Târgoviște Punct`, importă incremental după fiecare căutare și păstrează
checkpoint separat pentru fiecare termen. Întreruperea calculatorului nu pierde produsele deja importate.

Pagina jobului afișează:

- `căutări finalizate / total`;
- produse unice capturate în acel job;
- produse importate;
- termenii cu eroare și numărul încercărilor;
- la scanările complete: produse noi, reapărute, schimbări de preț și indisponibilitate.

O scanare rapidă/țintită nu marchează alte produse ca indisponibile. Numai două scanări complete,
finalizate integral pentru același magazin, pot dezactiva o ofertă absentă.

## 4. Scraperul din terminal

Rulează comenzile din directorul proiectului:

```bash
cd /home/micu/Desktop/priceComparision
```

Extinderea rapidă recomandată:

```bash
.venv/bin/python manage.py metro_seed_catalog --breadth-only --store Targoviste
```

Catalogul complet:

```bash
.venv/bin/python manage.py metro_seed_catalog --store Targoviste
```

Produse alese și categorie fixă:

```bash
.venv/bin/python manage.py metro_seed_catalog \
  "crema de branza" pate conserve mezeluri \
  --category "Conserve și pate" --store Targoviste
```

Ultimul exemplu folosește aceeași categorie pentru toți termenii; pentru categorii diferite rulează comenzi
separate. `--limit-per-search 20` limitează un test la primele 20 de rezultate. Valoarea `0`, implicită,
încarcă toate paginile. Nu coborî `--delay` sub `0.3`; valoarea implicită `0.8` este mai prudentă.

Pentru a vedea browserul și a reselecta magazinul/sesiunea:

```bash
.venv/bin/python manage.py metro_seed_catalog lapte --category Lactate \
  --store Targoviste --headed --no-import
```

`--no-import` păstrează rezultatele în staging pentru control manual. Fără el, importul este incremental.

### Reluarea unei scanări

ID-ul jobului apare în interfață și la finalul comenzii. Pentru o extindere rapidă întreruptă:

```bash
.venv/bin/python manage.py metro_seed_catalog --breadth-only --resume ID --store Targoviste
```

Pentru catalogul complet:

```bash
.venv/bin/python manage.py metro_seed_catalog --category-crawl --store Targoviste
```

Acest mod descoperă automat taxonomia live METRO și scanează toate categoriile terminale. Este preferat
listei de cuvinte atunci când vrei acoperirea întregului catalog. Pentru reluare:

```bash
.venv/bin/python manage.py metro_seed_catalog --category-crawl --resume ID --store Targoviste
```

Pentru produse care nu apar în taxonomie, rulează ocazional plasa de siguranță alfabetică. Aceasta
execută toate cele 676 de combinații de două litere, limitează fiecare rezultat la 500 și nu marchează
niciodată produsele absente ca indisponibile:

```bash
.venv/bin/python manage.py metro_seed_catalog --alphabet-crawl --store Targoviste
```

Reluare după întrerupere:

```bash
.venv/bin/python manage.py metro_seed_catalog --alphabet-crawl --resume ID --store Targoviste
```

### Import rapid al întregului catalog public METRO

Sitemapul oficial conține și produse care nu sunt momentan disponibile în
Târgoviște. Comanda de mai jos importă denumirea, codul METRO, gramajul/unitatea
și categoria, fără să inventeze un preț. Produsele deja capturate cu Selenium nu
sunt suprascrise. Când un produs devine disponibil local, scanarea obișnuită îi
completează automat prețul și pragurile de volum.

```bash
.venv/bin/python manage.py metro_import_sitemap
```

Poți verifica mai întâi sursa fără să modifici baza de date:

```bash
.venv/bin/python manage.py metro_import_sitemap --dry-run
```

### Actualizare rapidă a prețurilor magazinului

Aceasta este scanarea periodică recomandată. Folosește API-ul public consumat de
pagina METRO, parcurge taxonomia live a magazinului și importă prețul produsului
fără garanția SGR, ambalarea și toate pragurile de volum:

```bash
.venv/bin/python manage.py metro_seed_catalog --api-crawl --store Targoviste
```

Dacă procesul este întrerupt, identifică jobul în pagina „Scanări METRO” și reia-l:

```bash
.venv/bin/python manage.py metro_seed_catalog --api-crawl --resume ID --store Targoviste
```

Din interfață, aceeași operație este butonul **Actualizare rapidă cu prețuri**.

Termenii finalizați sunt săriți. Folosește `--refresh-completed` numai când vrei intenționat să refaci
termenii; pentru un job deja finalizat pornește un job nou.

Logurile scanărilor lansate din interfață sunt în `media/metro_scraper/`:

```bash
tail -f media/metro_scraper/breadth-job-ID.log
tail -f media/metro_scraper/mass-job-ID.log
```

## 5. Ce salvează scraperul

Pentru fiecare produs se păstrează doar:

- denumirea;
- codul intern METRO din URL;
- prețul cu TVA și pragurile de volum;
- numărul de bucăți și gramajul;
- magazinul și momentul observării;
- categoria locală și disponibilitatea.

Nu sunt descărcate imagini sau descrieri comerciale. Codul intern METRO nu este EAN; EAN-ul se poate
adăuga ulterior prin scannerul telefonului sau din documentele furnizorilor.

După scanare verifică în `Prețuri METRO`:

- filtrul magazinului preferat; implicit vezi doar ultimul preț din Târgoviște, iar `Tot istoricul` și
  `Toate locațiile` rămân disponibile pentru audit;
- produse cu gramaj suspect;
- pragurile `3+`, `6+` etc.; selectează `Cu preț de volum` și `Cea mai mare economie` pentru a vedea
  economia per pachet, economia totală și valoarea minimă a coșului la prag;
- pagina `Abateri de preț` pentru schimbări mari;
- categoria `Altele`, care indică produse ce merită reclasificate.

Reclasificare automată:

```bash
.venv/bin/python manage.py categorize_products
```

Adaugă `--overwrite` numai dacă vrei să înlocuiești inclusiv categoriile deja confirmate.

## 6. Liste de preț și stoc

`Furnizori → Importă listă de preț` acceptă CSV/XLSX cu denumire și preț; EAN, gramaj, unitate și bax sunt
opționale. Mai întâi vezi previzualizarea, apoi confirmarea creează un document de revizuit.

`Stoc → Importă vânzări POS` acceptă cantitate plus EAN sau denumire. Aplicarea este explicită și
idempotentă: același export nu scade stocul de două ori. Un produs fără politică activă de stoc rămâne
blocat până îl configurezi.

## 7. Backup, raport și verificări

Backup manual și verificare fără atingerea bazei curente:

```bash
.venv/bin/python manage.py backup_pricematch
.venv/bin/python manage.py verify_backup_restore backups/pricematch-AAAALLZZ-HHMMSS
```

Raportul Excel poate fi descărcat din `Raport` sau generat manual:

```bash
.venv/bin/python manage.py generate_weekly_report
```

Înainte de o actualizare importantă:

```bash
.venv/bin/python manage.py test
.venv/bin/python manage.py check --deploy
.venv/bin/python manage.py makemigrations --check --dry-run
```

## 8. Depanare rapidă

### Scraperul spune că există deja o scanare activă

Deschide jobul indicat. Dacă procesul chiar rulează, așteaptă. Dacă browserul s-a închis, reia jobul din
terminal. Nu schimba manual statusul în baza de date cât timp există un proces Chrome/Chromedriver.

```bash
pgrep -af 'metro_seed_catalog|chromedriver|chrome.*metro_chrome'
```

### Nu găsește produse sau magazinul

Rulează un test vizibil cu `--headed --no-import`, selectează Târgoviște Punct și închide Chrome normal.
Profilul persistent este în `data/metro_chrome_profile/`; nu îl copia în Git și nu porni două instanțe cu
același profil.

### METRO și-a schimbat pagina

Dacă toate căutările devin zero sau Selenium raportează selectori inexistenți, oprește scanarea și verifică
manual pagina. Rulează testele scraperului înainte de modificări:

```bash
.venv/bin/python manage.py test comparator.tests.test_metro_scraper
```

### OCR-ul rămâne în așteptare

```bash
systemctl status pricematch-worker
journalctl -u pricematch-worker --since today
```

### Aplicația nu răspunde

```bash
systemctl status pricematch caddy
journalctl -u pricematch -u caddy --since today
```

Nu expune direct PostgreSQL, Ollama, Gunicorn sau profilul Selenium pe internet.
