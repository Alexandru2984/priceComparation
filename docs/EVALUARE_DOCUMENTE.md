# Evaluarea locală a facturilor și bonurilor

Scopul evaluării este să măsoare separat dacă aplicația găsește produsele și dacă extrage corect
cantitatea, prețul, unitatea și EAN-ul. Documentele rămân pe calculator și nu sunt trimise către un API.

## Pregătire

Creează un director privat, exclus din Git, de exemplu `data/evaluare-reala/`. Copiază în el imagini sau
PDF-uri reprezentative: facturi digitale, fotografii bune/slabe, bonuri scurte și bonuri lungi. Nu folosi
doar documentele care deja funcționează bine.

Lângă fișiere creează `manifest.json`:

```json
{
  "cases": [
    {
      "name": "bon furnizor X",
      "file": "bon-001.jpg",
      "parser_mode": "HEURISTIC",
      "expected": [
        {
          "original_name": "Lapte integral 1L",
          "ean": "5941234567890",
          "quantity": 2,
          "unit_price_gross": 6.5,
          "base_unit": "L"
        }
      ]
    }
  ]
}
```

`file` este relativ la directorul manifestului; căile care ies din acel director sunt refuzate. Pentru a
testa numai parserul, înlocuiește `file` cu `ocr_text`. Valorile valide pentru `base_unit` sunt `BUC`, `KG`
și `L`. Include `ean` numai când codul este vizibil în document.

Rulează evaluarea:

```bash
.venv/bin/python manage.py evaluate_documents data/evaluare-reala/manifest.json \
  --output-json data/evaluare-reala/rezultat.json
```

Pentru o verificare automată înainte de actualizare:

```bash
.venv/bin/python manage.py evaluate_documents data/evaluare-reala/manifest.json \
  --min-recall 90 --min-price-accuracy 95
```

Comanda eșuează dacă pragurile nu sunt atinse. Raportul conține precision/recall pentru produse,
acuratețea câmpurilor, strategia OCR, scorul imaginii și fiecare nepotrivire. Corectează mai întâi
etichetele din manifest, apoi profilul furnizorului sau parserul. Nu ajusta regulile doar pentru un singur
bon: păstrează un set separat de documente pentru verificarea finală.
