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
- revizuirea tuturor liniilor unui document într-un singur tabel;
- cost efectiv cu reduceri, SGR, transport și reducerea generală distribuite proporțional;
- istoric separat METRO/furnizor, alerte de preț și semnale de calitate;
- notificări Web Push locale, fără API plătit, verificate automat la 15 minute;
- liste de cumpărături care recomandă cea mai ieftină sursă recentă;
- scanare EAN/GTIN din browserul telefonului, cu introducere manuală de rezervă;
- backup comprimat, verificat SHA-256 și restaurare explicită.
- paginare pentru cataloagele mari și căutare locală autocomplete, fără încărcarea miilor de opțiuni în HTML.

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

Pagina `/` este un demo public cu date statice. Datele reale sunt în `/app/` și pot fi accesate numai de
un utilizator `staff`. Creează primul administrator sau setează-i parola astfel:

```bash
.venv/bin/python manage.py createsuperuser
# pentru utilizatorul local pregătit deja:
.venv/bin/python manage.py changepassword micu
```

Configurația implicită din `.env.example` folosește PostgreSQL prin TCP și necesită setarea parolei pentru
rolul `pricecompare`. Pentru autentificare locală `peer`, lasă `DB_PASSWORD` și `DB_HOST` goale și setează
`DB_USER` la utilizatorul Linux care rulează aplicația. SQLite rămâne disponibil cu `DB_ENGINE=sqlite`.

Pentru dezvoltare, aplicația poate fi pornită simplu cu:

```bash
./start.sh
```

Pe calculatorul configurat în producție se folosesc serviciile systemd descrise mai jos, nu `start.sh`.

## Flux recomandat pentru primele facturi și bonuri

1. Autentifică-te la `http://127.0.0.1:8010/admin/login/`, apoi intră în `/app/`.
2. Adaugă furnizorii din `Furnizori → Furnizor nou`; marchează separat furnizorul METRO.
3. Din `Documente → Document nou`, alege factură sau bon și încarcă PDF/JPG/PNG ori lipește textul.
4. Pentru un bon lung, selectează până la 12 fotografii în ordinea de sus în jos.
5. Folosește tabelul `Revizuire rapidă` pentru a corecta toate liniile, apoi debifează `necesită
   verificare`. Confirmarea memorează automat aliasul și prețul furnizorului.
6. Dacă OCR-ul nu citește corect, salvează documentul fără procesare și adaugă liniile manual.

Transportul și reducerea generală se completează pe document. Reducerea unei linii și garanția SGR se
completează separat; SGR nu este tratată ca preț al mărfii. Toate comparațiile rămân pe valori cu TVA.

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

Comanda caută automat peste 200 de familii de produse. Implicit încarcă toate cardurile disponibile
pentru fiecare căutare, deduplică după codul intern METRO și salvează progresul fiecărui termen. Dacă
Chrome sau rețeaua se opresc, reia exact scanarea rămasă:

```bash
.venv/bin/python manage.py metro_seed_catalog --resume ID_SCANARE
```

Din interfață, `Prețuri METRO → Scanare Selenium → Catalog complet automat` pornește aceeași operație în
fundal. Pentru o selecție proprie poți transmite termenii explicit, de exemplu:

```bash
.venv/bin/python manage.py metro_seed_catalog lapte iaurt banane --limit-per-search 12
```

Poți adăuga oricând alte familii de produse fără să modifici codul:

```bash
.venv/bin/python manage.py metro_seed_catalog conserve pate "crema de branza" mezeluri salam sunca
```

Rezultatele sunt deduplicate, iar o ofertă existentă pentru același produs, magazin și zi este actualizată.

Pentru termeni proprii poți fixa și categoria:

```bash
.venv/bin/python manage.py metro_seed_catalog "pasta de dinti" sampon deodorant --category "Igienă personală"
```

Lista implicită acoperă lactate, băuturi, fructe și legume, băcănie, conserve, mezeluri, dulciuri,
snacks, igienă, curățenie, cafea și ceai, sosuri, panificație, congelate, carne și pește, produse pentru
copii, hrană pentru animale și consumabile de menaj. Folosește o întârziere de cel puțin 0,3 secunde;
valoarea implicită de 0,8 secunde evită încărcarea agresivă a site-ului.

Magazinul folosit automat este configurat prin `METRO_STORE_QUERY`. Pentru locația curentă:

```dotenv
METRO_STORE_QUERY=Targoviste
PREFERRED_METRO_STORE=METRO PUNCT TARGOVISTE
```

Ofertele celorlalte magazine sunt păstrate, dar comparațiile folosesc cu prioritate magazinul preferat.

### Categorii și export

În `Produse` poți filtra catalogul după categorie și poți descărca:

- `Export CSV`: format UTF-8 cu separator `;`, ușor de deschis în Excel;
- `Export Excel`: foaia `Catalog curent` cu prețul preferat și foaia `Toate ofertele` cu istoricul
  locațiilor METRO pentru produsele filtrate.

Pentru reclasificarea catalogului după schimbarea regulilor:

```bash
.venv/bin/python manage.py categorize_products --overwrite
```

### EAN și scannerul telefonului

`Produse → Scanează EAN` folosește camera prin API-ul local al browserului și verifică cifra de control
GTIN. Camera este disponibilă numai pe `localhost` sau prin HTTPS; de pe un telefon din rețeaua locală
este necesar HTTPS. Chrome pe Android oferă suportul cel mai bun. Introducerea manuală rămâne disponibilă.

Codul intern METRO este păstrat separat de EAN. Astfel, schimbarea denumirii comerciale de pe site nu mai
creează automat un produs duplicat.

Selectarea produselor din facturi, alerte, liste și prețuri manuale folosește căutarea locală. Scrie cel
puțin două caractere din denumire, marcă, EAN sau codul furnizorului; sunt returnate maximum 20 de rezultate.
Catalogul și istoricul METRO afișează câte 100 de rânduri pe pagină, iar exporturile continuă să includă
toate produsele care corespund filtrelor.

## HTTPS local și acces de pe telefon

Configurația din `deploy/` rulează Django prin Gunicorn numai pe `127.0.0.1:8010`; Caddy este singurul
serviciu expus în rețeaua locală și termină conexiunea HTTPS. Pentru calculatorul curent, adresa este:

```text
https://10.0.0.7
```

Caddy folosește o autoritate de certificare strict locală. Înainte ca telefonul să considere conexiunea
sigură, conectează-l la aceeași rețea Wi-Fi și:

1. descarcă certificatul public de la `http://10.0.0.7/ca.crt`;
2. instalează-l ca certificat CA de încredere pe telefon;
3. pe iPhone/iPad activează și încrederea completă în `Configurări → General → Informații → Configurări
   de încredere certificat`;
4. redeschide `https://10.0.0.7` și verifică să nu mai apară avertismentul de certificat.

Fișierul oferit la `/ca.crt` conține doar certificatul public; cheia privată rămâne în spațiul Caddy de pe
calculator. Camera și Web Push trebuie testate numai după ce browserul afișează conexiunea ca sigură.
Pentru iOS, adaugă aplicația pe ecranul principal înainte de activarea notificărilor. Pe Android, Chrome
poate activa notificările direct din pagina `Notificări`.

Adresa `10.0.0.7` ar trebui rezervată în router pentru acest calculator. Dacă DHCP o schimbă, actualizează
IP-ul în `.env` și [deploy/Caddyfile](deploy/Caddyfile), reinstalează configurația și repornește serviciile.

Comenzi utile:

```bash
sudo systemctl status pricematch caddy
sudo systemctl restart pricematch caddy
sudo journalctl -u pricematch -u caddy --since today
systemctl list-timers 'pricematch-*'
```

Serviciile instalate sunt:

- `pricematch.service`: Gunicorn, pornit automat la boot;
- `pricematch-alerts.timer`: verifică alertele la fiecare 15 minute;
- `pricematch-backup.timer`: creează backupul zilnic în jurul orei 03:30.

PostgreSQL și Ollama ascultă în continuare numai pe localhost. Dacă activezi UFW ulterior, permite porturile
TCP 80 și 443 numai din subrețeaua locală, nu expune portul 8010 și nu configura port-forwarding în router.

## Notificări automate

Cheile VAPID se generează o singură dată și rămân locale:

```bash
.venv/bin/python manage.py generate_vapid_keys
```

Valorile afișate se adaugă în `.env` la `WEBPUSH_VAPID_PRIVATE_KEY` și `WEBPUSH_VAPID_PUBLIC_KEY`. Nu
șterge și nu regenera cheia privată după abonarea telefonului, altfel abonamentele existente trebuie create
din nou. Din aplicație intră în `Notificări`, activează dispozitivul și trimite un test. Fiecare browser se
abonează separat, iar dezabonarea afectează doar dispozitivul curent.

Web Push nu are cost de API pentru această aplicație. Mesajul este criptat pentru abonamentul browserului;
livrarea trece totuși prin infrastructura push a browserului, deci notificarea nu este un mecanism complet
offline. Aplicația trimite doar denumirea produsului și prețul, fără facturi, conturi sau alte date sensibile.

### Istoric, alerte și liste de cumpărături

- apasă denumirea produsului din catalog pentru istoricul normalizat METRO și furnizori;
- în `Alerte` setează pragul în lei per BUC/KG/L;
- în `Cumpărături` introdu cantitatea necesară; aplicația alege cea mai ieftină sursă și arată economia;
- ofertele furnizorilor mai vechi de `SUPPLIER_PRICE_MAX_AGE_DAYS` (implicit 90 zile) sunt ignorate.

## Backup, restaurare și automatizare

Backupul include baza de date și documentele din `media/`, cu manifest și verificare SHA-256:

```bash
.venv/bin/python manage.py backup_pricematch
```

Restaurarea șterge datele curente și cere confirmarea explicită:

```bash
.venv/bin/python manage.py restore_pricematch backups/pricematch-AAAALLZZ-HHMMSS --confirm RESTORE
```

Pentru cron sau un timer systemd, comanda periodică face întâi backupul și poate porni apoi scanarea:

```bash
# backup zilnic
.venv/bin/python manage.py pricematch_maintenance

# backup + actualizare completă METRO
.venv/bin/python manage.py pricematch_maintenance --scan-metro --store Targoviste
```

Nu suprapune două scanări Selenium și păstrează directorul `backups/` în afara Git, pe un disc separat.

## Teste

```bash
.venv/bin/python manage.py test
```

## Securitate și publicare

Zona privată necesită cont staff, documentele sunt descărcate numai autentificat, iar încercările repetate
de login sunt limitate. Configurația completă, rezultatele auditului și pașii obligatorii pentru HTTPS,
Gunicorn, backup și HSTS sunt în [docs/SECURITY_AUDIT.md](docs/SECURITY_AUDIT.md).

## Observație importantă

AI/OCR extrage și propune date. Calculele financiare sunt realizate determinist în Django. Verifică manual TVA-ul, ambalarea și potrivirile marcate înainte de a lua o decizie de cumpărare.
