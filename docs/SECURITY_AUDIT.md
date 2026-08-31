# Audit de securitate și publicare

Data ultimei actualizări: 31 august 2026. Domeniu analizat: aplicația Django, autentificarea, rutele, uploadurile,
fișierele private, configurarea PostgreSQL, scraperul METRO și configurația VPS/Nginx/Cloudflare Tunnel.

## Măsuri implementate

- pagina `/` folosește numai date demonstrative hardcodate și nu citește catalogul privat;
- toate rutele `/app/` cer un utilizator activ cu `is_staff=True`;
- loginul unic este furnizat de `django-two-factor-auth`, cu parolă de minimum 12 caractere și TOTP;
- `MFA_REQUIRED=1` impune înrolarea și o sesiune OTP verificată pe toate rutele `/app/` și `/admin/`;
- `django-axes` protejează inclusiv loginul principal `/account/login/`, nu doar Django Admin, și blochează
  temporar combinația utilizator/IP după 5 autentificări eșuate;
- prin reverse proxy, IP-ul clientului este acceptat numai din `X-Real-IP` rescris de un proxy aflat în
  `DJANGO_TRUSTED_PROXY_IPS`; un header trimis de altă sursă este ignorat;
- nu există înregistrare publică; conturile staff se creează, listează, dezactivează și resetează MFA numai
  prin comenzi locale, iar User/Group nu pot fi administrate din interfața Django Admin;
- rolul operator este separat de administrator; configurarea, importurile sensibile, stocul, scanările
  METRO și ștergerea documentelor cer administrator;
- cererile private care modifică date sunt jurnalizate cu utilizator, rută, rezultat și IP, fără corpul
  formularului, parole sau conținut din documente;
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
- restaurarea refuză arhive cu căi nesigure și cere confirmarea literală `RESTORE`;
- stagingul are utilizator Linux, cheie Django, bază PostgreSQL, directoare media, worker, porturi și
  hostname separate de producție; scanarea METRO este oprită implicit acolo;
- secretele și configurația bazei rămân în `.env`, exclus din Git;
- `pip-audit` nu a găsit vulnerabilități cunoscute în dependențele declarate;
- Bandit nu a găsit probleme de severitate medie sau mare.

## Verificări executate la 31 august 2026

- 192 teste Django trecute pe PostgreSQL, inclusiv rolurile, MFA, uploadurile, OCR-ul, importurile,
  inventarul, scraperul și restaurarea izolată;
- `makemigrations --check --dry-run`: nicio migrație lipsă;
- `pip check`: nicio dependență incompatibilă;
- `pip-audit -r requirements.txt`: nicio vulnerabilitate cunoscută;
- `bandit -r comparator pricecompare -ll`: nicio problemă de severitate medie sau mare;
- backup nou creat și restaurat cu succes într-o bază izolată;
- toate scripturile shell din `deploy/` trec verificarea de sintaxă.

`check --deploy` păstrează intenționat avertismentele pentru `SECURE_HSTS_INCLUDE_SUBDOMAINS` și
`SECURE_HSTS_PRELOAD`. Aceste opțiuni se activează numai după verificarea tuturor subdomeniilor prin HTTPS.
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
- `runserver` nu este acceptabil în producție;
- baza PostgreSQL trebuie să asculte numai local sau într-o rețea privată, niciodată pe internet.
- Cloudflare Access și regulile WAF pot adăuga o barieră în fața aplicației, dar nu înlocuiesc parola, MFA,
  autorizarea Django, backupurile și actualizarea regulată a sistemului.
