import csv
import io
from decimal import Decimal

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill


CATALOG_HEADERS = [
    "Produs", "Marcă", "EAN", "Categorie", "Unitate bază", "Preț pachet", "Bucăți/pachet",
    "Cantitate/bucată", "Preț/unitate bază", "Praguri volum", "Cel mai mic preț/unitate",
    "Sursă", "Valabil de la", "Activ",
]


def _safe_cell(value):
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
        return f"'{value}"
    return value


def _catalog_row(product):
    offer = product.current_metro_offer()
    tiers = list(offer.volume_tiers.all()) if offer else []
    tier_summary = " | ".join(
        f"{tier.min_packages}+ pachete: {tier.price_gross:.2f} lei"
        for tier in tiers
    )
    lowest_price = min(
        [offer.price_per_base_unit, *(tier.price_per_base_unit for tier in tiers)]
    ) if offer else None
    return [
        product.name,
        product.brand,
        product.ean,
        product.category or "Altele",
        product.base_unit,
        offer.price_gross if offer else None,
        offer.units_per_package if offer else None,
        offer.unit_size if offer else None,
        offer.price_per_base_unit if offer else None,
        tier_summary,
        lowest_price,
        offer.source if offer else "",
        offer.valid_from if offer else None,
        "Da" if product.active else "Nu",
    ]


def build_catalog_csv(products):
    output = io.StringIO()
    writer = csv.writer(output, delimiter=";", lineterminator="\n")
    writer.writerow(CATALOG_HEADERS)
    for product in products:
        writer.writerow([_safe_cell(value) for value in _catalog_row(product)])
    return ("\ufeff" + output.getvalue()).encode("utf-8")


def build_catalog_xlsx(products, offers):
    workbook = Workbook()
    catalog = workbook.active
    catalog.title = "Catalog curent"
    catalog.append(CATALOG_HEADERS)
    for product in products:
        catalog.append([_safe_cell(value) for value in _catalog_row(product)])

    history = workbook.create_sheet("Toate ofertele")
    history_headers = [
        "Produs", "Categorie", "Unitate bază", "Preț pachet", "Bucăți/pachet", "Cantitate/bucată",
        "Preț/unitate bază", "Praguri volum", "Cel mai mic preț/unitate", "Sursă", "Valabil de la", "Activă",
    ]
    history.append(history_headers)
    for offer in offers:
        tiers = list(offer.volume_tiers.all())
        history.append(
            [
                _safe_cell(offer.product.name),
                offer.product.category or "Altele",
                offer.product.base_unit,
                offer.price_gross,
                offer.units_per_package,
                offer.unit_size,
                offer.price_per_base_unit,
                " | ".join(
                    f"{tier.min_packages}+ pachete: {tier.price_gross:.2f} lei"
                    for tier in tiers
                ),
                min([offer.price_per_base_unit, *(tier.price_per_base_unit for tier in tiers)]),
                offer.source,
                offer.valid_from,
                "Da" if offer.active else "Nu",
            ]
        )

    for sheet in (catalog, history):
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        for cell in sheet[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="195C44")
        for column in sheet.columns:
            letter = column[0].column_letter
            width = min(max(len(str(cell.value or "")) for cell in column) + 2, 55)
            sheet.column_dimensions[letter].width = max(width, 12)
        for row in sheet.iter_rows(min_row=2):
            for cell in row:
                if isinstance(cell.value, Decimal):
                    cell.value = float(cell.value)
                    cell.number_format = "0.00"

    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()
