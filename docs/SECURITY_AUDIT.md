# Audit de securitate și publicare

Data auditului: 14 iulie 2026. Domeniu analizat: aplicația Django, autentificarea, rutele, uploadurile,
fișierele private, configurarea PostgreSQL, Selenium și dependențele Python.

## Măsuri implementate

- pagina `/` folosește numai date demonstrative hardcodate și nu citește catalogul privat;
- toate rutele `/app/` cer un utilizator activ cu `is_staff=True`;
- loginul este furnizat de Django Admin, cu validatori de parolă și minimum 12 caractere;
- `django-axes` blochează temporar combinația utilizator/IP după 5 autentificări eșuate;
- CSRF este activ pe toate formularele, iar operațiile distructive folosesc POST;
- documentele nu mai sunt servite direct din `MEDIA_URL`; descărcarea cere autentificare staff;
- PDF-urile și imaginile sunt verificate după semnătură/structură, extensie, dimensiune și rezoluție;
- limite: 10 MB/fișier, 50 MB/document, 12 fișiere și 2 MB/CSV;
- fișierele noi primesc permisiuni `0600`, directoarele `0700`, iar `start.sh` folosește `umask 077`;
- sunt active CSP, anti-framing, `nosniff`, Referrer-Policy, Permissions-Policy și `no-store` pe zona privată;
- modul de producție activează cookies `Secure`, redirect HTTPS și HSTS;
- Selenium este dezactivat implicit în producție și se activează numai explicit;
- secretele și configurația bazei rămân în `.env`, exclus din Git;
- `pip-audit` nu a găsit vulnerabilități cunoscute în dependențele declarate;
- Bandit nu a găsit probleme de severitate medie sau mare.

## Configurație obligatorie înainte de publicare

Folosește un domeniu dedicat și un reverse proxy cu TLS, de exemplu Caddy sau Nginx. Nu expune direct
Gunicorn, PostgreSQL, directorul `media/`, Ollama sau portul Selenium.

```dotenv
DJANGO_DEBUG=0
DJANGO_PRODUCTION=1
DJANGO_SECRET_KEY=genereaza-o-cu-secrets-token-urlsafe
DJANGO_ALLOWED_HOSTS=preturi.exemplu.ro
DJANGO_CSRF_TRUSTED_ORIGINS=https://preturi.exemplu.ro
DJANGO_TRUST_PROXY=1
DJANGO_SECURE_SSL_REDIRECT=1
METRO_SCRAPER_ENABLED=0
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

- salvează zilnic baza cu `pg_dump -Fc pricecompare > pricecompare.dump`;
- salvează separat directorul `media/`, cu criptare și acces limitat;
- testează restaurarea, nu doar crearea backupului;
- rotește `DJANGO_SECRET_KEY` și parola bazei dacă există suspiciune de compromitere;
- verifică periodic tabelele `axes_accessattempt` și `axes_accesslog`;
- rulează `clearsessions` periodic pentru eliminarea sesiunilor expirate.

## Riscuri reziduale și condiții de publicare

- autentificarea multifactor nu este încă implementată; pentru acces din internet este următoarea măsură recomandată;
- OCR procesează formate complexe cu biblioteci native; uploadul este limitat la staff, dar procesul ar trebui rulat
  cu utilizator Linux neprivilegiat și fără acces la alte directoare;
- acesta este un audit automat și de cod, nu un test de penetrare extern pe infrastructura finală;
- `runserver` nu este acceptabil în producție;
- baza PostgreSQL trebuie să asculte numai local sau într-o rețea privată, niciodată pe internet.
