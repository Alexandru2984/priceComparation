# PriceMatch Local

Aplicație personală pentru compararea achizițiilor unui magazin alimentar cu prețurile METRO. Rulează local, fără API-uri plătite.

Pentru operarea zilnică, comenzile scraperului, reluarea joburilor și depanare folosește
[Ghidul practic PriceMatch](docs/GHID_OPERARE.md).

Pentru publicarea pe VPS-ul existent, cu Nginx și Cloudflare Tunnel, urmează
[ghidul de deploy securizat](docs/DEPLOY_VPS_CLOUDFLARE.md).
Pentru o copie criptată în afara VPS-ului folosește [ghidul de backup restic](docs/BACKUP_EXTERN_RESTIC.md).
Înaintea unei actualizări publice, verifică același commit pe subdomeniul separat folosind
[checklistul de staging](docs/STAGING_CHECKLIST.md).

## Ce face MVP-ul

- catalog incremental de produse urmărite;
- prețuri METRO introduse manual sau importate din CSV;
- facturi și bonuri introduse manual, din text OCR, imagine sau PDF;
- bonuri lungi încărcate din maximum 12 fotografii, procesate în ordine;
- OCR local cu Tesseract (`ron+eng`);
- coadă PostgreSQL și worker local separat, astfel încât OCR-ul nu blochează cererea web;
- scor de calitate per imagine/PDF, încercări OCR automate și avertismente pentru cadre slabe;
- centru de calibrare OCR pe bonuri/facturi confirmate manual, cu recall, precizie și erori recurente;
- structurarea textului cu Ollama și JSON Schema, cu parser simplu de rezervă;
- asociere locală fuzzy și memorarea corecțiilor;
- profil de parsare și TVA implicit separat pentru fiecare furnizor;
- comparație exactă per BUC/KG/L folosind `Decimal`;
- coadă vizuală pentru potrivirile care necesită verificare.
- actualizarea automată a prețurilor de referință din documentele METRO confirmate.
- revizuirea tuturor liniilor unui document într-un singur tabel;
- cost efectiv cu reduceri, SGR, transport și reducerea generală distribuite proporțional;
- istoric separat METRO/furnizor, alerte de preț și semnale de calitate;
- notificări Web Push locale, fără API plătit, verificate automat la 15 minute;
- liste de cumpărături care recomandă cea mai ieftină sursă recentă;
- scanare EAN/GTIN din browserul telefonului, cu introducere manuală de rezervă;
- stoc auditat, reaprovizionare și optimizarea comenzilor după bax, transport, prag și buget;
- import cu previzualizare pentru listele de preț CSV/XLSX și exporturi POS idempotente;
- import inițial XLSX, cu foi separate pentru furnizori, produse și stoc și aplicare idempotentă;
- marjă netă cu TVA, pierderi estimate și recomandare de preț la raft;
- raport operațional săptămânal în interfață și Excel, generat automat lunea;
- export administrativ XLSX cu 12 foi pentru catalog, prețuri, documente, stoc, liste și alerte, filtrabil
  după perioadă și protejat împotriva formulelor injectate;
- scanări METRO automate țintite/complet și coadă pentru abateri mari de preț;
- MFA cu aplicație TOTP pentru publicare pe internet;
- backup comprimat, verificat SHA-256 și restaurare izolată de test;
- retenție automată pentru sesiuni, jurnal, staging METRO, joburi tehnice și versiuni OCR vechi;
- paginare pentru cataloagele mari și căutare locală autocomplete, fără încărcarea miilor de opțiuni în HTML.
- interogări cu preîncărcare controlată și indexuri PostgreSQL pentru dashboard, documente, potriviri și
  ofertele METRO curente, protejate prin teste care refuză reapariția query-urilor per produs.

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
un utilizator `staff`. Conturile se creează numai din terminal, cu parolă cerută interactiv:

```bash
.venv/bin/python manage.py create_staff_user PROPRIETAR --role admin
.venv/bin/python manage.py create_staff_user OPERATOR --role operator
.venv/bin/python manage.py list_staff_users
.venv/bin/python manage.py disable_staff_user OPERATOR
.venv/bin/python manage.py enable_staff_user OPERATOR
.venv/bin/python manage.py set_staff_role OPERATOR admin --confirm
.venv/bin/python manage.py revoke_staff_sessions OPERATOR --confirm
.venv/bin/python manage.py reset_staff_mfa OPERATOR --confirm
.venv/bin/python manage.py changepassword OPERATOR
```

Administratorul configurează furnizorii, catalogul, METRO, stocul și importurile și poate șterge
documente. Operatorul poate încărca facturi/bonuri, corecta OCR-ul, consulta prețurile și lucra cu listele
de cumpărături. Toate operațiile private care modifică date apar în `Jurnal`, fără conținutul formularelor.
Nu există rută de creare cont în site, iar utilizatorii și grupurile nu sunt editabile din Django Admin.
Schimbarea rolului, resetarea MFA și revocarea sesiunilor cer intervenție din terminal; ultimul administrator
activ nu poate fi dezactivat sau retrogradat.

Configurația implicită din `.env.example` folosește PostgreSQL prin TCP și necesită setarea parolei pentru
rolul `pricecompare`. Pentru autentificare locală `peer`, lasă `DB_PASSWORD` și `DB_HOST` goale și setează
`DB_USER` la utilizatorul Linux care rulează aplicația. SQLite rămâne disponibil cu `DB_ENGINE=sqlite`.

Pentru mutarea datelor existente din Excel, autentifică-te ca administrator și intră în `Import inițial`.
Descarcă șablonul XLSX, completează foile `Furnizori`, `Produse` și `Stoc`, apoi verifică previzualizarea.
Rândurile greșite sunt omise, iar confirmarea nu poate dubla stocul dacă același fișier este trimis din nou.
EAN-ul este recomandat în foaia de stoc pentru o asociere fără ambiguități.

Pentru acces din internet setează `MFA_REQUIRED=1`. După autentificarea cu parola, aplicația te trimite la
`/account/two_factor/setup/`: scanează codul QR cu o aplicație TOTP și salvează codurile de recuperare în
afara calculatorului. Linkul `MFA` din bara privată permite regenerarea codurilor sau administrarea
dispozitivului. Nu păstra codurile de recuperare în același director cu backupurile.

Pentru dezvoltare, aplicația poate fi pornită simplu cu:

```bash
./start.sh
```

Pe calculatorul configurat în producție se folosesc serviciile systemd descrise mai jos, nu `start.sh`.

## Flux recomandat pentru primele facturi și bonuri

1. Autentifică-te la `http://127.0.0.1:8010/account/login/`, apoi intră în `/app/`.
2. Adaugă furnizorii din `Furnizori → Furnizor nou`; marchează separat furnizorul METRO.
3. Din `Documente → Document nou`, alege factură sau bon și încarcă PDF/JPG/PNG ori lipește textul.
4. Pentru un bon lung, selectează până la 12 fotografii în ordinea de sus în jos.
5. Folosește tabelul `Revizuire rapidă` pentru a corecta toate liniile, apoi debifează `necesită
   verificare`. Confirmarea memorează automat aliasul și prețul furnizorului.
6. Urmărește jobul în `Documente → Inbox procesare`; pagina documentului se actualizează automat.
7. Dacă OCR-ul nu citește corect, verifică scorul și avertismentele fiecărei imagini, apoi refotografiază
   sau adaugă liniile manual.

După ce un document este verificat complet, apasă `Folosește la calibrare`. Pagina `Calibrare OCR`
compară din nou parserul local cu liniile confirmate și arată separat produsele omise, cantitățile,
prețurile, unitățile și imaginile slabe. Un set variat de 10–20 de documente reale este suficient pentru
prima bază de măsurare; nu folosi documente care mai au linii bifate `necesită verificare`.

După salvare, secțiunea `Fișiere și ordinea OCR` permite adăugarea altor cadre, mutarea lor sus/jos și
ștergerea unei fotografii greșite. Ordinea afișată este exact ordinea folosită de OCR. Orice modificare a
fișierelor marchează documentul pentru reverificare; reprocesarea rămâne o acțiune separată și salvează
automat versiunea veche a liniilor. Limitele sunt 12 fișiere, 10 MB per fișier și 50 MB cumulat.

Procesarea rulează în serviciul local `pricematch-worker.service`. Joburile abandonate după o oprire sunt
repuse automat în coadă, iar un document nu poate avea două procesări active simultan.

### Liste de preț de la alți furnizori

Din `Furnizori → Importă listă de preț` poți încărca CSV sau XLSX. Sunt recunoscute denumirea, prețul,
EAN-ul, gramajul, unitatea și numărul de bucăți din bax, inclusiv cu antete uzuale în română. Aplicația
afișează întâi erorile și potrivirile propuse. Confirmarea creează un document `Listă de preț`; ofertele
devin utilizabile numai după ce confirmi liniile în pagina documentului.

`Profil parsare` din dreptul furnizorului permite alegerea parserului local/Ollama și a unui TVA implicit.
Pagina arată rata asocierilor corectate, scorul mediu și volumul de linii procesate.

### Import de vânzări POS

Din `Stoc → Importă vânzări POS` încarci CSV/XLSX cu `cantitate` și cel puțin `EAN` sau `denumire`.
Coloanele opționale sunt data vânzării și numărul bonului. Importul are previzualizare, asociere manuală și
aplicare explicită. Liniile fără potrivire sigură sau fără politică de stoc rămân blocate. Cantitatea scăzută
este `unități vândute × cantitate de bază per unitate vândută`; reimportarea aceluiași export nu dublează
mișcările de stoc.

Completează și `total document cu TVA` exact cum apare tipărit. Pagina documentului compară acest total cu
suma liniilor, reducerilor, transportului și SGR și semnalează orice diferență mai mare de 0,05 lei. Sunt
marcate separat liniile în care `cantitate × preț` nu coincide cu totalul OCR sau cota TVA pare neobișnuită.

Lista de documente poate fi căutată după furnizor ori număr și filtrată după tip și status. Pentru același
furnizor și aceeași dată, un număr de document nu poate fi introdus de două ori, inclusiv cu alte majuscule.
Bonurile fără număr rămân permise.

Din pagina unui document poți folosi `Editează documentul` pentru total, transport, reducere și observații.
După ce există linii, furnizorul, tipul, numărul și data sunt blocate ca să nu rupă istoricul de preț;
corectează aceste câmpuri înainte de procesare. Reprocesarea OCR cere o confirmare separată deoarece
înlocuiește liniile și corecțiile existente.

Înainte de înlocuirea OCR, aplicația salvează automat o versiune locală cu textul extras, liniile,
cantitățile, prețurile și asocierile de produse. Ultimele 10 versiuni sunt vizibile în pagina documentului
și pot fi restaurate. Restaurarea salvează mai întâi versiunea curentă, astfel încât operația poate fi
anulată printr-o restaurare ulterioară. Versiunile sunt private și sunt șterse odată cu documentul.

`Șterge documentul…` cere tastarea explicită a cuvântului `STERGE`. Operația elimină liniile, fișierele
încărcate și ofertele METRO derivate direct din document, fără să șteargă produsele din catalog.

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

Coloanele opționale sunt `brand`, `ean`, `units_per_package`, `unit_size`, `volume_min_packages`, `volume_price_gross`, `valid_from` și `source`.

Exemplu: un bax cu 6 sticle de 2 L are `units_per_package=6`, `unit_size=2`, iar `price_gross` este prețul întregului bax.

Pentru prețurile METRO care scad la volum, `volume_min_packages=3` și `volume_price_gross=38.50` înseamnă că de la trei baxuri prețul fiecărui bax devine 38,50 lei. Scanarea Selenium citește automat etichetele METRO de forma „preț pentru 3+”, le păstrează separat pentru fiecare magazin și aplică pragul numai când factura sau lista de cumpărături conține cantitatea necesară.

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

Pentru popularea rapidă recomandată, cu 60 de căutări largi în toate categoriile:

```bash
.venv/bin/python manage.py metro_seed_catalog --breadth-only --store Targoviste
```

Pentru catalogul complet folosind magazinul păstrat în profilul Chrome:

```bash
.venv/bin/python manage.py metro_seed_catalog
```

Comanda completă caută automat peste 500 de familii de produse. Implicit apasă toate controalele
„Arată încă 24 rezultate”, deduplică după codul intern METRO și salvează progresul fiecărui termen. Dacă
Chrome sau rețeaua se opresc, reia exact scanarea rămasă:

```bash
.venv/bin/python manage.py metro_seed_catalog --resume ID_SCANARE
```

Din interfață, `Prețuri METRO → Scanare Selenium`, butonul `Extindere rapidă` pornește varianta recomandată,
iar `Catalog complet` pornește toate căutările în fundal. Pentru o selecție proprie poți transmite termenii explicit, de exemplu:

```bash
.venv/bin/python manage.py metro_seed_catalog lapte iaurt banane --limit-per-search 12
```

Poți adăuga oricând alte familii de produse fără să modifici codul:

```bash
.venv/bin/python manage.py metro_seed_catalog conserve pate "crema de branza" mezeluri salam sunca
```

Rezultatele sunt deduplicate, iar o ofertă existentă pentru același produs, magazin și zi este actualizată.

Pentru actualizarea completă și rapidă a magazinului configurat, folosește API-ul
public consumat de pagina METRO. Comanda preia prețul produsului fără garanția
SGR, unitatea comercială și pragurile de volum; Selenium rămâne fallbackul de
verificare:

```bash
.venv/bin/python manage.py metro_seed_catalog --api-crawl --store Targoviste
```

Pentru termeni proprii poți fixa și categoria:

```bash
.venv/bin/python manage.py metro_seed_catalog "pasta de dinti" sampon deodorant --category "Igienă personală"
```

Lista implicită acoperă lactate, băuturi, fructe și legume, băcănie, conserve, mezeluri, dulciuri,
snacks, igienă, curățenie, cafea și ceai, sosuri, panificație, congelate, carne și pește, produse pentru
copii, hrană pentru animale și consumabile de menaj. Folosește o întârziere de cel puțin 0,3 secunde;
valoarea implicită de 0,8 secunde evită încărcarea agresivă a site-ului.

### Prospețime și disponibilitate METRO

Fiecare cod METRO este urmărit separat pentru fiecare magazin: prima observare, ultima observare, ultimul
preț, ambalarea și numărul de scanări consecutive în care a lipsit. Un produs este marcat indisponibil și
ofertele lui nu mai intră în comparații numai după două scanări complete consecutive în care nu apare.
Scanările manuale, parțiale sau cu termeni eșuați nu marchează produse ca lipsă.

Dacă produsul reapare, oferta este reactivată automat. Pagina `Prețuri METRO` arată ultima observare,
produsele indisponibile și catalogul mai vechi de 14 zile, iar detaliul fiecărei scanări complete raportează
produsele noi, reapărute, prețurile și ambalările schimbate. Istoricul ofertelor rămâne în PostgreSQL.

Magazinul folosit automat este configurat prin `METRO_STORE_QUERY`. Pentru locația curentă:

```dotenv
METRO_STORE_QUERY=Targoviste
PREFERRED_METRO_STORE=METRO PUNCT TARGOVISTE
METRO_AUTOMATION_ENABLED=0
METRO_FULL_SCAN_INTERVAL_DAYS=7
METRO_TARGETED_SCAN_INTERVAL_HOURS=24
METRO_TARGETED_SCAN_MAX_PRODUCTS=150
METRO_PRICE_ANOMALY_PERCENT=40
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
- `pricematch-worker.service`: coada locală PostgreSQL pentru OCR și extragerea documentelor;
- `pricematch-alerts.timer`: verifică alertele la fiecare 15 minute;
- `pricematch-backup.timer`: face mentenanța zilnică în jurul orei 03:30 (backup, scanarea METRO scadentă și verificarea alertelor).
- `pricematch-weekly-report.timer`: generează lunea raportul Excel privat în `reports/`.

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

Verificarea sigură restaurează backupul într-o bază SQLite temporară, rulează verificările Django și șterge
baza temporară, fără să atingă PostgreSQL sau documentele curente:

```bash
.venv/bin/python manage.py verify_backup_restore backups/pricematch-AAAALLZZ-HHMMSS
```

Pentru cron sau timerul systemd inclus, comanda periodică face backupul, verifică alertele și pornește cel mult o scanare METRO:

```bash
# mentenanță zilnică + scanare țintită/completă numai când este scadentă
.venv/bin/python manage.py pricematch_maintenance --scheduled-metro

# backup + actualizare completă METRO
.venv/bin/python manage.py pricematch_maintenance --scan-metro --store Targoviste
```

Mentenanța rulează curățarea după backup și elimină numai date tehnice expirate. Poți vedea întâi exact
ce ar fi eliminat, fără nicio modificare:

```bash
.venv/bin/python manage.py cleanup_pricematch
.venv/bin/python manage.py cleanup_pricematch --confirm
```

Facturile, bonurile, produsele, prețurile confirmate, stocul și fișierele documentelor nu sunt șterse.
Retenția este configurabilă prin `ACTIVITY_LOG_RETENTION_DAYS`, `TECHNICAL_DATA_RETENTION_DAYS` și
`INVOICE_REVISION_LIMIT`.

Scanarea țintită caută produsele active din stoc și listele de cumpărături; cea completă rulează implicit la 7 zile. Aplicația refuză suprapunerea scanărilor și trimite schimbările mai mari decât `METRO_PRICE_ANOMALY_PERCENT` în pagina „Abateri de preț”. Păstrează directorul `backups/` în afara Git, pe un disc separat.

## Teste

```bash
.venv/bin/python manage.py test
.venv/bin/python manage.py audit_data_integrity
.venv/bin/python manage.py verify_pricematch
```

Pentru aceeași suită de calitate folosită în CI:

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/ruff check comparator pricecompare
.venv/bin/coverage run manage.py test
.venv/bin/coverage report --fail-under=70
.venv/bin/pip-audit -r requirements.txt
.venv/bin/bandit -q -r comparator pricecompare -x comparator/tests -ll
```

GitHub Actions rulează automat aceste verificări pe PostgreSQL la fiecare push și pull request. Dependabot
verifică săptămânal pachetele Python și versiunile acțiunilor din workflow.
Suita include bugete explicite de interogări pentru potriviri, comparații și sursele de aprovizionare;
adăugarea mai multor produse nu trebuie să crească numărul de query-uri executate de fiecare rând.

Administratorul are în meniul `Operare` un rezumat fără secrete al conturilor, MFA, cozilor OCR,
scanărilor METRO, jurnalului și integrității datelor. Înainte de publicare rulează
`verify_pricematch --deploy --fail-on-warnings` cu exact variabilele mediului de producție.

Pentru a măsura parserul pe documentele tale reale fără a le trimite nicăieri, copiază manifestul exemplu,
adaugă lângă el fotografiile/PDF-urile și rulează:

```bash
.venv/bin/python manage.py evaluate_documents manifestul-meu.json \
  --output-json rezultat-evaluare.json --min-recall 90 --min-price-accuracy 95
```

Formatul și metoda de etichetare sunt descrise în [docs/EVALUARE_DOCUMENTE.md](docs/EVALUARE_DOCUMENTE.md).
Manifestul demonstrativ [sample_data/evaluation_manifest.json](sample_data/evaluation_manifest.json) are
o bază deterministă de 100%; aceasta nu reprezintă performanța pe fotografii reale.

Raportul operațional curent este disponibil în `Raport` și poate fi exportat imediat. Generatorul manual:

```bash
.venv/bin/python manage.py generate_weekly_report
```

## Securitate și publicare

Zona privată necesită cont staff, documentele sunt descărcate numai autentificat, iar încercările repetate
de login sunt limitate. Configurația completă, rezultatele auditului și pașii obligatorii pentru HTTPS,
Gunicorn, backup și HSTS sunt în [docs/SECURITY_AUDIT.md](docs/SECURITY_AUDIT.md). Pentru serverul public
folosește separat [ghidul VPS cu Nginx și Cloudflare Tunnel](docs/DEPLOY_VPS_CLOUDFLARE.md); secțiunea Caddy
de mai sus rămâne configurația accesului din rețeaua locală.

## Observație importantă

AI/OCR extrage și propune date. Calculele financiare sunt realizate determinist în Django. Verifică manual TVA-ul, ambalarea și potrivirile marcate înainte de a lua o decizie de cumpărare.
