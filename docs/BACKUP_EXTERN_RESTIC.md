# Backup extern criptat cu restic

Backupul local nu este suficient dacă VPS-ul sau discul se pierde. Configurația aceasta creează mai întâi
un backup portabil PriceMatch, îl trimite criptat într-un repository extern și șterge copia temporară numai
după terminarea comenzii. Repository-ul poate fi SFTP, S3 compatibil sau alt backend suportat de restic.

Restic criptează repository-ul cu parola lui. Pierderea parolei face restaurarea imposibilă; păstrează o
copie offline, separată de VPS. Cheile S3/SFTP nu înlocuiesc parola restic.

## 1. Instalare și secrete

```bash
sudo apt update
sudo apt install -y restic
sudo install -d -o root -g pricematch -m 0750 /etc/pricematch
sudo install -o root -g pricematch -m 0640 /srv/pricematch/deploy/restic.env.example /etc/pricematch/restic.env
sudoedit /etc/pricematch/restic.env
```

Creează parola fără să o pui în argumentele unui proces:

```bash
sudo install -o root -g pricematch -m 0640 /dev/null /etc/pricematch/restic-password
sudoedit /etc/pricematch/restic-password
```

Fișierul trebuie să conțină o singură parolă lungă și aleatoare. Pentru SFTP, cheia SSH folosită de
utilizatorul `pricematch` trebuie să fie read-only și să permită acces numai la directorul repository-ului.
Pentru S3 completează cheile în `restic.env` și acordă-le acces doar la bucketul/prefixul de backup.

## 2. Inițializarea repository-ului

Scriptul wrapper citește configurația root-owned și poate fi folosit fără exportarea secretelor în shell:

```bash
sudo -u pricematch /srv/pricematch/deploy/vps/pricematch-restic.sh init
sudo -u pricematch /srv/pricematch/deploy/vps/pricematch-restic.sh snapshots
```

Nu rula `init` dacă repository-ul conține deja backupuri PriceMatch.

## 3. Instalarea timerelor

```bash
sudo chmod 0755 /srv/pricematch/deploy/vps/pricematch-restic.sh /srv/pricematch/deploy/vps/pricematch-offsite-backup.sh
sudo install -m 0644 /srv/pricematch/deploy/vps/pricematch-offsite-backup.service /etc/systemd/system/
sudo install -m 0644 /srv/pricematch/deploy/vps/pricematch-offsite-backup.timer /etc/systemd/system/
sudo install -m 0644 /srv/pricematch/deploy/vps/pricematch-offsite-check.service /etc/systemd/system/
sudo install -m 0644 /srv/pricematch/deploy/vps/pricematch-offsite-check.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now pricematch-offsite-backup.timer pricematch-offsite-check.timer
```

Backupul zilnic rulează după mentenanța locală și păstrează 7 copii zilnice, 5 săptămânale, 12 lunare și
3 anuale. În fiecare zi se verifică metadatele repository-ului; în prima duminică din lună se citește și se
verifică integral conținutul extern.

Prima probă trebuie pornită manual:

```bash
sudo systemctl start pricematch-offsite-backup.service
sudo systemctl status pricematch-offsite-backup.service
sudo journalctl -u pricematch-offsite-backup.service --since today
sudo -u pricematch /srv/pricematch/deploy/vps/pricematch-restic.sh snapshots
```

## 4. Probă de restaurare

Restaurează într-un director temporar, niciodată peste aplicația activă:

```bash
sudo -u pricematch install -d -m 0700 /srv/pricematch/data/restore-test
sudo -u pricematch /srv/pricematch/deploy/vps/pricematch-restic.sh restore latest --target /srv/pricematch/data/restore-test
```

În directorul restaurat găsește subdirectorul `pricematch-AAAALLZZ-HHMMSS`, apoi verifică backupul:

```bash
sudo -u pricematch /srv/pricematch/.venv/bin/python /srv/pricematch/manage.py verify_backup_restore /cale/către/pricematch-AAAALLZZ-HHMMSS
```

După verificare, șterge manual directorul de test. Fă această probă după configurare și apoi cel puțin
trimestrial. O listă de snapshoturi nu demonstrează singură că restaurarea aplicației funcționează.

## Monitorizare

```bash
systemctl list-timers 'pricematch-offsite-*'
sudo journalctl -u pricematch-offsite-backup.service -u pricematch-offsite-check.service --since '7 days ago'
```

Configurează o alertă a VPS-ului pentru unități systemd eșuate. Nu include `restic.env`, parola repository,
cheile SSH sau credentialele S3 în Git ori în backupurile necriptate.
