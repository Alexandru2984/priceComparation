# Staging PriceMatch pe subdomeniu separat

Staging-ul permite testarea de pe telefon și prin Cloudflare fără să atingă baza, documentele sau procesele
de producție. Exemplele folosesc `staging.preturi.example.ro`; înlocuiește-l cu subdomeniul real.

Separarea obligatorie este:

```text
staging hostname → cloudflared → Nginx 127.0.0.1:8081 → Gunicorn 127.0.0.1:8020
                                                        → pricecompare_staging
                                                        → /srv/pricematch-staging/media
```

## 1. Utilizator, cod și PostgreSQL separate

```bash
sudo adduser --system --group --home /srv/pricematch-staging pricematch-staging
sudo install -d -o pricematch-staging -g pricematch-staging -m 0750 /srv/pricematch-staging
```

Copiază checkout-ul pe care vrei să-l testezi în `/srv/pricematch-staging`, apoi instalează mediul lui
Python. Nu copia `.env`, `media/`, backupurile sau baza din producție.

```bash
sudo chown -R pricematch-staging:pricematch-staging /srv/pricematch-staging
sudo -u pricematch-staging python3 -m venv /srv/pricematch-staging/.venv
sudo -u pricematch-staging /srv/pricematch-staging/.venv/bin/pip install -r /srv/pricematch-staging/requirements.txt
sudo -u pricematch-staging install -d -m 0700 /srv/pricematch-staging/media /srv/pricematch-staging/backups /srv/pricematch-staging/data /srv/pricematch-staging/staticfiles /srv/pricematch-staging/reports
```

Creează un rol și o bază PostgreSQL care nu au acces la producție:

```bash
sudo -u postgres psql
```

```sql
CREATE ROLE pricecompare_staging LOGIN;
\password pricecompare_staging
CREATE DATABASE pricecompare_staging OWNER pricecompare_staging;
\q
```

## 2. Mediu și servicii

```bash
sudo install -d -o root -g pricematch-staging -m 0750 /etc/pricematch
sudo install -o root -g pricematch-staging -m 0640 /srv/pricematch-staging/deploy/staging/pricematch-staging.env.example /etc/pricematch/staging.env
sudoedit /etc/pricematch/staging.env
sudo install -m 0644 /srv/pricematch-staging/deploy/staging/pricematch-staging.service /etc/systemd/system/
sudo install -m 0644 /srv/pricematch-staging/deploy/staging/pricematch-staging-worker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now pricematch-staging pricematch-staging-worker
```

Folosește o cheie Django și o parolă PostgreSQL diferite de producție. `PRICEMATCH_ENVIRONMENT=staging`
afișează permanent banda roșie „MEDIU DE TEST”. API-ul și Selenium METRO sunt oprite implicit ca să nu
pornești accidental scanări reale.

## 3. Nginx și Cloudflare Tunnel

```bash
sudo install -m 0644 /srv/pricematch-staging/deploy/staging/nginx-pricematch-staging.conf /etc/nginx/sites-available/pricematch-staging
sudoedit /etc/nginx/sites-available/pricematch-staging
sudo ln -s /etc/nginx/sites-available/pricematch-staging /etc/nginx/sites-enabled/pricematch-staging
sudo nginx -t
sudo systemctl reload nginx
```

În tunnel-ul existent adaugă hostname-ul staging către `http://127.0.0.1:8081`, cu HTTP Host Header egal
cu subdomeniul. Pentru un tunnel configurat local, regula este în
`deploy/staging/cloudflared-ingress.yml.example` și trebuie pusă înainte de fallbackul final.

Protejează întregul hostname staging cu Cloudflare Access și permite numai adresa ta. Django MFA rămâne
activ. Configurează `Cache Bypass` pentru întregul hostname.

```bash
cloudflared tunnel ingress validate
sudo systemctl restart cloudflared
curl -I -H 'Host: staging.preturi.example.ro' http://127.0.0.1:8081/
curl -I https://staging.preturi.example.ro/
```

## 4. Conturi de test

```bash
cd /srv/pricematch-staging
sudo -u pricematch-staging .venv/bin/python manage.py create_staff_user test-admin --role admin
sudo -u pricematch-staging .venv/bin/python manage.py create_staff_user test-operator --role operator
```

Folosește parole și dispozitive TOTP distincte de producție. Nu importa un backup real în staging decât
dacă ai nevoie de un test controlat și accepți că acel mediu va conține temporar date private.

## 5. Checklist funcțional de pe calculator și telefon

- banda roșie de staging este vizibilă pe toate paginile;
- `/` nu afișează produse sau documente reale;
- operatorul poate adăuga și corecta un bon, dar primește 403 la importul inițial, stoc și scanări METRO;
- administratorul poate deschide jurnalul și vede operațiile operatorului;
- loginul cere MFA, logoutul revocă sesiunea din browser, iar cinci parole greșite activează limitarea;
- fotografia JPG/PNG de pe telefon intră în coadă și workerul finalizează OCR-ul;
- un bon lung din mai multe fotografii păstrează ordinea și poate fi reordonat;
- PDF-ul valid este procesat, iar un fișier fals sau prea mare este refuzat;
- `/media/orice` răspunde 404, în timp ce descărcarea autentificată din document funcționează;
- camera citește un EAN prin HTTPS și asocierea greșită poate fi corectată;
- un document verificat poate fi adăugat în `Calibrare OCR`, iar metricile apar fără reprocesare externă;
- șablonul `Import inițial` produce previzualizare și același fișier nu dublează stocul;
- exporturile CSV/XLSX se deschid corect și nu includ date din demo;
- layoutul rămâne utilizabil pe ecran îngust și nu expune butoane admin operatorului;
- `Stare` nu raportează migrații lipsă, spațiu critic sau limbile Tesseract absente.

Activează temporar `METRO_API_ENABLED=1` numai când vrei să verifici explicit catalogul pe baza staging.
Nu rula simultan scanări agresive din staging și producție.

## 6. Verificări automate

```bash
cd /srv/pricematch-staging
sudo -u pricematch-staging .venv/bin/python manage.py test
sudo -u pricematch-staging .venv/bin/python manage.py check --deploy
sudo -u pricematch-staging .venv/bin/python manage.py makemigrations --check --dry-run
sudo systemctl status pricematch-staging pricematch-staging-worker
sudo journalctl -u pricematch-staging -u pricematch-staging-worker --since today
sudo ss -lntp | grep -E ':(8020|8081)\\b'
```

Porturile 8020 și 8081 trebuie să apară numai pe `127.0.0.1`.

## 7. Promovare în producție

1. Rulează checklistul complet pe commitul care urmează să fie publicat.
2. Notează hashul commitului și nu mai modifica acel checkout.
3. Creează și verifică backupul producției; confirmă și snapshotul restic extern.
4. Actualizează checkout-ul de producție la același commit.
5. Instalează dependențele, apoi repornește `pricematch` și `pricematch-worker`.
6. Verifică migrările, demo-ul, loginul, un document existent și logurile.
7. Păstrează versiunea anterioară disponibilă până după verificarea funcțională.

Nu promovezi baza sau directoarele `media/` din staging. Se promovează numai codul și migrările deja testate.
