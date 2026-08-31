# Audit de securitate și publicare

Data ultimei actualizări: 31 august 2026. Domeniu analizat: întregul cod Python și configurația GitHub Actions,
autentificarea, toate rutele private, exporturile, uploadurile și arhivele, apelurile HTTP, procesele locale,
fișierele private, PostgreSQL, OCR/Ollama, scraperul METRO și istoricul Git pentru tipare de secrete.

## Măsuri implementate

- pagina `/` folosește numai date demonstrative hardcodate și nu citește catalogul privat;
- toate rutele `/app/` cer un utilizator activ cu `is_staff=True`;
- loginul unic este furnizat de `django-two-factor-auth`, cu parolă de minimum 12 caractere și TOTP;
- `MFA_REQUIRED=1` impune înrolarea și o sesiune OTP verificată pe toate rutele `/app/` și `/admin/`;
- `django-axes` protejează inclusiv loginul principal `/account/login/`, nu doar Django Admin, și blochează
  temporar combinația utilizator/IP după 5 autentificări eșuate;
- prin reverse proxy, IP-ul clientului este acceptat numai din `X-Real-IP` rescris de un proxy aflat în
  `DJANGO_TRUSTED_PROXY_IPS`; un header trimis de altă sursă este ignorat;
- nu există înregistrare publică; conturile staff se creează, listează, activează, dezactivează, schimbă ca
  rol, deloghează forțat și resetează MFA numai prin comenzi locale, iar User/Group nu pot fi administrate
  din interfața Django Admin; ultimul administrator activ este protejat;
- rolul operator este separat de administrator; configurarea, importurile sensibile, stocul, scanările
  METRO și ștergerea documentelor cer administrator;
- cererile private care modifică date sunt jurnalizate cu utilizator, rută, rezultat și IP, fără corpul
  formularului, parole sau conținut din documente;
- exportul administrativ complet este rezervat administratorului și fiecare descărcare este jurnalizată;
- textele exportate în CSV/XLSX sunt neutralizate când pot fi interpretate drept formule de spreadsheet;
- CSRF este activ pe toate formularele, iar operațiile distructive folosesc POST;
- documentele nu mai sunt servite direct din `MEDIA_URL`; descărcarea cere autentificare staff;
- PDF-urile și imaginile sunt verificate după semnătură/structură, extensie, dimensiune și rezoluție;
- limite: 10 MB/fișier, 50 MB/document, 12 fișiere, 2 MB/CSV METRO și 5 MB/listă CSV/XLSX;
- fișierele XLSX acceptate nu pot conține macrocomenzi, semnătura ZIP este verificată, iar conținutul
  decomprimat este limitat la 50 MB și 5.000 de rânduri;
- importul POS folosește chei unice în PostgreSQL, astfel încât aceeași vânzare nu poate scădea stocul de două ori;
- OCR-ul rulează într-un serviciu neprivilegiat separat, cu `NoNewPrivileges`, sistem read-only și acces
  de scriere limitat la directoarele aplicației;
- fișierele noi primesc permisiuni `0600`, directoarele `0700`, iar `start.sh` folosește `umask 077`;
- sunt active CSP, anti-framing, `nosniff`, Referrer-Policy, Permissions-Policy și `no-store` pe zona privată;
- modul de producție activează cookies `Secure`, redirect HTTPS și HSTS;
- API-ul de catalog și Selenium au comutatoare separate; Selenium este dezactivat implicit în producție;
- camera este permisă prin Permissions-Policy exclusiv pe pagina privată a scannerului EAN;
- EAN/GTIN este validat prin cifra de control, iar codurile duplicate sunt refuzate;
- backupurile locale sunt comprimate, au permisiuni restrictive și manifest SHA-256;
- backupul extern folosește un repository restic criptat, retenție pe intervale, verificare zilnică a
  metadatelor și citirea integrală lunară a datelor;
- mentenanța curăță după backup sesiunile expirate și datele tehnice care depășesc retenția, fără să
  elimine documente, produse, prețuri confirmate ori mișcări de stoc;
- `verify_backup_restore` probează restaurarea completă într-o bază temporară izolată;
- restaurarea acceptă numai fișierele declarate de formatul PriceMatch, verifică SHA-256 și gzip, limitează
  numărul și dimensiunea fișierelor, refuză căi/legături/tipuri speciale și extrage media într-un director
  temporar înainte să înlocuiască datele active; confirmarea literală `RESTORE` rămâne obligatorie;
- stagingul are utilizator Linux, cheie Django, bază PostgreSQL, directoare media, worker, porturi și
  hostname separate de producție; scanarea METRO este oprită implicit acolo;
- secretele și configurația bazei rămân în `.env`, exclus din Git;
- fiecare rută `/app/` declară programatic rolul `operator` sau `admin`, iar testele refuză o rută nouă
  rămasă fără politică de acces;
- CI rulează pe PostgreSQL: Ruff, verificarea migrațiilor și setărilor, toate testele cu prag de coverage,
  `pip-audit` și Bandit; acțiunile sunt fixate la SHA, cu `contents: read`, iar Dependabot verifică săptămânal;
- `pip-audit` nu a găsit vulnerabilități cunoscute în dependențele declarate;
- Bandit nu a găsit probleme de severitate medie sau mare.
- paginile 400/403/404/500 sunt controlate și nu afișează motive interne, excepții sau căi cerute;
- centrul administrativ de operare afișează numai agregate și configurații nesensibile;
- auditul de integritate și comanda `verify_pricematch` blochează lansarea pentru migrații, configurări,
  lipsa unui administrator activ ori date care ar compromite comparațiile;
- testele de regresie fixează bugetul interogărilor pentru calculele repetate, iar indexurile compuse acoperă
  filtrarea documentelor, ofertelor METRO, joburilor și evenimentelor recente;
- regulile financiare esențiale sunt duplicate intenționat în PostgreSQL prin `CHECK CONSTRAINT`, astfel
  încât scrierile care ocolesc formularele nu pot introduce prețuri negative sau cantități/procente invalide;

## Constatări remediate în auditul complet

| Severitate | Constatare | Remediere |
| --- | --- | --- |
| Ridicată | Restaurarea putea procesa un manifest cu nume arbitrare și arhive fără limite anti-bombă. | Allowlist strict, limite, validare gzip/tar și extragere temporară înainte de înlocuire. |
| Medie | Exporturile necesitau o regulă unică pentru toate prefixele de formulă cunoscute. | Neutralizare centralizată pentru CSV/XLSX, inclusiv caractere de control și variante full-width; CSV-ul destinat Excel primește prefix de text. |
| Medie | O rută privată nouă putea fi adăugată fără ca CI să demonstreze rolul cerut. | Marcaj de rol pe decoratori și inventarierea automată a tuturor rutelor `/app/`. |
| Medie | Configurații periculoase precum SQLite, host wildcard, Ollama remote sau URL METRO străin nu blocau verificarea de producție. | Verificări Django `pricematch.E001–E007` și avertisment separat pentru Selenium. |
| Medie | Nu exista nicio integrare CI sau prag măsurabil de acoperire. | Workflow PostgreSQL cu coverage minim 70%, Ruff, Bandit, pip-audit și verificarea migrărilor. |

## Verificări executate la 31 august 2026

- 226 teste Django trecute pe PostgreSQL, inclusiv rolurile, MFA, uploadurile ostile, exporturile, OCR-ul, importurile,
  inventarul, scraperul și restaurarea izolată;
- 72% branch coverage pe codul aplicației, măsurat fără teste și migrații; CI refuză scăderea sub 70%;
- `makemigrations --check --dry-run`: nicio migrație lipsă;
- `ruff check comparator pricecompare`: nicio eroare de sintaxă, import sau nume nedefinit;
- `pip check`: nicio dependență incompatibilă;
- `pip-audit -r requirements.txt`: nicio vulnerabilitate cunoscută;
- `bandit -r comparator pricecompare -ll`: nicio problemă de severitate medie sau mare;
- backup nou creat și restaurat cu succes într-o bază izolată;
- niciun tipar de cheie privată, token GitHub ori cheie AWS în fișierele urmărite sau istoricul Git; `.env`
  nu a fost urmărit în niciun commit;
- toate scripturile shell din `deploy/` trec verificarea de sintaxă.

`check --deploy` păstrează intenționat avertismentele pentru `SECURE_HSTS_INCLUDE_SUBDOMAINS` și
`SECURE_HSTS_PRELOAD`. Aceste opțiuni se activează numai după verificarea tuturor subdomeniilor prin HTTPS.
Configurația locală curentă mai raportează `pricematch.W001`, deoarece comutatorul legacy activează Selenium
într-un mediu marcat drept producție; înaintea publicării setează explicit `METRO_SELENIUM_ENABLED=0`.
Unitățile systemd trebuie validate din nou pe VPS după copiere, deoarece căile `/srv/pricematch` și
`/srv/pricematch-staging` nu există în mediul local de dezvoltare.

## Configurație obligatorie înainte de publicare

Pentru VPS-ul cu Nginx și Cloudflare Tunnel folosește configurațiile din `deploy/` și urmează
`docs/DEPLOY_VPS_CLOUDFLARE.md`. Nginx și Gunicorn trebuie să asculte numai pe loopback. Nu expune direct
Gunicorn, PostgreSQL, directorul `media/`, Ollama sau portul Selenium.

```dotenv
DJANGO_DEBUG=0
DJANGO_PRODUCTION=1
PRICEMATCH_ENVIRONMENT=production
DJANGO_SECRET_KEY=genereaza-o-cu-secrets-token-urlsafe
DJANGO_ALLOWED_HOSTS=preturi.exemplu.ro
DJANGO_CSRF_TRUSTED_ORIGINS=https://preturi.exemplu.ro
DJANGO_TRUST_PROXY=1
DJANGO_TRUSTED_PROXY_IPS=127.0.0.1,::1
DJANGO_SECURE_SSL_REDIRECT=1
MFA_REQUIRED=1
METRO_API_ENABLED=1
METRO_SELENIUM_ENABLED=0
OLLAMA_ENABLED=0
```

Generează cheia fără să o salvezi în istoricul shell-ului și copiaz-o direct în `.env`:

```bash
.venv/bin/python -c "import secrets; print(secrets.token_urlsafe(64))"
```

Rulează înainte de fiecare publicare:

```bash
.venv/bin/python manage.py check --deploy
.venv/bin/python manage.py migrate --noinput
.venv/bin/python manage.py collectstatic --noinput
.venv/bin/gunicorn pricecompare.wsgi:application --bind 127.0.0.1:8010 --workers 2
```

Începe HSTS cu valoarea implicită de o oră. Numai după ce domeniul și toate subdomeniile funcționează
exclusiv prin HTTPS, setează `DJANGO_HSTS_SECONDS=31536000`, `DJANGO_HSTS_INCLUDE_SUBDOMAINS=1` și
`DJANGO_HSTS_PRELOAD=1`. Activarea prematură poate face domeniul inaccesibil prin HTTP pentru mult timp.

## Backup și operare

- rulează zilnic `.venv/bin/python manage.py pricematch_maintenance --scheduled-metro` și copiază backupul
  criptat pe alt sistem, nu doar în alt director de pe același VPS;
- pentru redundanță PostgreSQL poți păstra și `pg_dump -Fc pricecompare > pricecompare.dump`;
- backupul aplicației include `media/`, dar discul extern trebuie criptat și accesul limitat;
- testează periodic restaurarea cu `.venv/bin/python manage.py verify_backup_restore <director-backup>`;
- backupul conține și secretele dispozitivelor TOTP; protejează-l și criptează discul extern;
- rotește `DJANGO_SECRET_KEY` și parola bazei dacă există suspiciune de compromitere;
- verifică periodic tabelele `axes_accessattempt` și `axes_accesslog`;
- rulează `clearsessions` periodic pentru eliminarea sesiunilor expirate.

## Riscuri reziduale și condiții de publicare

- MFA protejează aplicația, dar securitatea contului depinde de păstrarea separată a parolei, telefonului și codurilor de recuperare;
- OCR procesează formate complexe cu biblioteci native; uploadul este limitat la staff, dar procesul ar trebui rulat
  cu utilizator Linux neprivilegiat și fără acces la alte directoare;
- acesta este un audit automat și de cod, nu un test de penetrare extern pe infrastructura finală;
- coverage-ul total nu dovedește corectitudinea tuturor ramurilor; zonele cele mai puțin acoperite rămân
  browserul Selenium real, erorile API METRO și verificările hardware/OCR dependente de sistem;
- `runserver` nu este acceptabil în producție;
- baza PostgreSQL trebuie să asculte numai local sau într-o rețea privată, niciodată pe internet.
- Cloudflare Access și regulile WAF pot adăuga o barieră în fața aplicației, dar nu înlocuiesc parola, MFA,
  autorizarea Django, backupurile și actualizarea regulată a sistemului.
