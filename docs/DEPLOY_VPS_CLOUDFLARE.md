# Publicare pe VPS cu Nginx și Cloudflare Tunnel

Configurația recomandată păstrează toate serviciile sensibile pe loopback:

```text
Internet → Cloudflare → cloudflared → Nginx 127.0.0.1:8080
                                      → Gunicorn 127.0.0.1:8010
                                      → PostgreSQL 127.0.0.1:5432
```

Pagina `/` rămâne demo public și folosește numai date statice. `/app/`, `/admin/` și documentele reale cer
cont staff, parolă și MFA. Aplicația nu oferă rută de înregistrare, iar utilizatorii nu sunt administrabili
din Django Admin.

În exemple, înlocuiește `preturi.example.ro` cu domeniul real. Nu copia peste configurația Nginx sau
Cloudflare existentă; adaugă configurația PriceMatch lângă celelalte servicii de pe VPS.

## 1. Utilizator Linux și aplicație

Pe Ubuntu/Debian, instalează dependențele sistem și creează un utilizator fără shell interactiv:

```bash
sudo apt update
sudo apt install -y python3-venv tesseract-ocr tesseract-ocr-ron tesseract-ocr-eng postgresql nginx
sudo adduser --system --group --home /srv/pricematch pricematch
sudo install -d -o pricematch -g pricematch -m 0750 /srv/pricematch
```

Copiază sau clonează proiectul în `/srv/pricematch`, fără `.env`, backupuri, `media/` sau profilul Chrome de
pe calculatorul local. Apoi:

```bash
sudo chown -R pricematch:pricematch /srv/pricematch
sudo -u pricematch python3 -m venv /srv/pricematch/.venv
sudo -u pricematch /srv/pricematch/.venv/bin/pip install -r /srv/pricematch/requirements.txt
sudo -u pricematch install -d -m 0700 /srv/pricematch/media /srv/pricematch/backups /srv/pricematch/data /srv/pricematch/staticfiles /srv/pricematch/reports
```

## 2. PostgreSQL local

Deschide consola PostgreSQL și folosește `\password`, astfel încât parola să nu apară în comanda shell:

```bash
sudo -u postgres psql
```

În consolă:

```sql
CREATE ROLE pricecompare LOGIN;
\password pricecompare
CREATE DATABASE pricecompare OWNER pricecompare;
\q
```

Păstrează PostgreSQL pe `127.0.0.1`; portul 5432 nu trebuie publicat în firewall sau în Cloudflare Tunnel.

## 3. Secrete și setări

Generează cheia Django local pe VPS:

```bash
/srv/pricematch/.venv/bin/python -c "import secrets; print(secrets.token_urlsafe(64))"
```

Copiază [șablonul de mediu](../deploy/pricematch.env.example) în
`/etc/pricematch/pricematch.env`, completează domeniul, cheia și parola PostgreSQL, apoi limitează accesul:

```bash
sudo install -d -o root -g pricematch -m 0750 /etc/pricematch
sudo install -o root -g pricematch -m 0640 /srv/pricematch/deploy/pricematch.env.example /etc/pricematch/pricematch.env
sudoedit /etc/pricematch/pricematch.env
```

În producție, `METRO_API_ENABLED=1` permite sincronizarea fără browser, iar
`METRO_SELENIUM_ENABLED=0` împiedică pornirea Chrome pe server. Ollama poate rămâne oprit pe un VPS mic;
OCR Tesseract și parserul local continuă să funcționeze.

## 4. Gunicorn, worker și mentenanță

Instalează unitățile pregătite pentru utilizatorul neprivilegiat `pricematch`:

```bash
sudo install -m 0644 /srv/pricematch/deploy/vps/pricematch.service /etc/systemd/system/
sudo install -m 0644 /srv/pricematch/deploy/vps/pricematch-worker.service /etc/systemd/system/
sudo install -m 0644 /srv/pricematch/deploy/vps/pricematch-maintenance.service /etc/systemd/system/
sudo install -m 0644 /srv/pricematch/deploy/vps/pricematch-maintenance.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now pricematch.service pricematch-worker.service pricematch-maintenance.timer
```

Verifică serviciile și logurile:

```bash
sudo systemctl status pricematch pricematch-worker pricematch-maintenance.timer
sudo journalctl -u pricematch -u pricematch-worker --since today
```

Timerul face zilnic backupul local și pornește sincronizarea METRO când este scadentă. Copiază backupurile
criptat și pe alt sistem; un backup rămas doar pe același VPS nu protejează de pierderea serverului.
Pentru copia automată criptată urmează [ghidul restic](BACKUP_EXTERN_RESTIC.md).

## 5. Nginx pe loopback

Editează domeniul din [configurația Nginx](../deploy/nginx-pricematch.conf), apoi instaleaz-o:

```bash
sudo install -m 0644 /srv/pricematch/deploy/nginx-pricematch.conf /etc/nginx/sites-available/pricematch
sudoedit /etc/nginx/sites-available/pricematch
sudo ln -s /etc/nginx/sites-available/pricematch /etc/nginx/sites-enabled/pricematch
sudo nginx -t
sudo systemctl reload nginx
```

Nginx ascultă numai pe `127.0.0.1:8080`, blochează accesul direct la `/media/` și transmite către Django
doar IP-ul validat din `CF-Connecting-IP`. Gunicorn ascultă la rândul său numai pe loopback.

## 6. Cloudflare Tunnel existent

Dacă tunnel-ul este administrat din dashboard, adaugă un `Public hostname`:

- hostname: domeniul PriceMatch;
- service type: `HTTP`;
- URL: `127.0.0.1:8080`;
- HTTP Host Header: același domeniu PriceMatch.

Dacă folosești un fișier local, copiază regula din
[exemplul cloudflared](../deploy/cloudflared-config.yml.example) în lista `ingress` existentă, înaintea
regulii finale `http_status:404`. Nu înlocui UUID-ul sau credentials-file ale tunnel-ului tău.

```bash
cloudflared tunnel ingress validate
sudo systemctl restart cloudflared
sudo journalctl -u cloudflared --since today
```

Cloudflare trebuie să folosească HTTPS la margine. Creează o regulă de cache `Bypass` pentru hostname-ul
aplicației: paginile private, cookies de sesiune și tokenurile CSRF nu trebuie servite din cache. Pentru o
barieră suplimentară poți adăuga Cloudflare Access peste `/app/*`, `/admin/*` și `/account/*`; MFA Django
rămâne activ chiar și în acest caz.

Tunnel-ul inițiază conexiuni către Cloudflare, deci 8010, 8080, 5432 și 11434 nu trebuie deschise inbound.
Păstrează doar metoda ta existentă de administrare a VPS-ului; modificarea regulilor SSH se face separat,
după ce ai verificat că nu te blochezi în afara serverului.

## 7. Conturi create numai din terminal

Rulează comenzile ca utilizatorul serviciului, astfel încât să folosească exact mediul de producție:

```bash
cd /srv/pricematch
sudo -u pricematch /srv/pricematch/.venv/bin/python manage.py create_staff_user PROPRIETAR --role admin
sudo -u pricematch /srv/pricematch/.venv/bin/python manage.py create_staff_user OPERATOR --role operator
sudo -u pricematch /srv/pricematch/.venv/bin/python manage.py list_staff_users
sudo -u pricematch /srv/pricematch/.venv/bin/python manage.py disable_staff_user NUME
sudo -u pricematch /srv/pricematch/.venv/bin/python manage.py reset_staff_mfa NUME --confirm
```

Parola este cerută interactiv și nu poate fi transmisă ca argument. Rolul `operator` lucrează cu
documentele și OCR-ul, iar rolul `admin` schimbă configurația, importurile și stocul. După primul login,
utilizatorul este obligat să înroleze o aplicație TOTP. Păstrează codurile de recuperare în afara VPS-ului.

## 8. Verificare înainte de DNS public

```bash
cd /srv/pricematch
sudo -u pricematch /srv/pricematch/.venv/bin/python manage.py check --deploy
curl -I -H 'Host: preturi.example.ro' http://127.0.0.1:8080/
curl -I https://preturi.example.ro/
sudo ss -lntp | grep -E ':(8010|8080|5432|11434)\\b'
```

Primele trei porturi trebuie să apară numai pe loopback; Ollama nu trebuie să apară dacă este dezactivat.
Verifică apoi în browser:

1. `/` arată doar mock data;
2. `/app/` redirecționează un vizitator anonim la login;
3. un cont fără MFA este trimis la înrolare;
4. `/media/orice` răspunde 404;
5. încărcarea și procesarea unui bon de test funcționează;
6. un backup se creează și trece testul de restaurare.

După câteva zile stabile exclusiv pe HTTPS poți crește `DJANGO_HSTS_SECONDS`. Activează
`DJANGO_HSTS_INCLUDE_SUBDOMAINS` și `DJANGO_HSTS_PRELOAD` numai dacă toate subdomeniile domeniului sunt
pregătite permanent pentru HTTPS.

## Actualizări ulterioare

După copierea unei versiuni noi:

```bash
sudo -u pricematch /srv/pricematch/.venv/bin/pip install -r /srv/pricematch/requirements.txt
sudo systemctl restart pricematch pricematch-worker
sudo systemctl status pricematch pricematch-worker
```

Serviciul web rulează automat migrările și `collectstatic` înainte de Gunicorn. Pentru o actualizare cu
schimbări majore, creează și verifică backupul înainte de restart.
