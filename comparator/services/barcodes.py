from django.core.exceptions import ValidationError
from django.db import transaction

from comparator.models import Product, ProductCode


GTIN_LENGTHS = {8, 12, 13, 14}


def normalize_barcode(value):
    return "".join(character for character in str(value or "") if character.isdigit())


def is_valid_gtin(value):
    code = normalize_barcode(value)
    if len(code) not in GTIN_LENGTHS:
        return False
    digits = [int(character) for character in code]
    check = digits.pop()
    weighted = sum(digit * (3 if index % 2 == 0 else 1) for index, digit in enumerate(reversed(digits)))
    return (10 - weighted % 10) % 10 == check


@transaction.atomic
def assign_ean(product, value):
    code = normalize_barcode(value)
    if not is_valid_gtin(code):
        raise ValidationError("Codul nu este un EAN/GTIN valid (8, 12, 13 sau 14 cifre).")
    conflict = ProductCode.objects.filter(
        kind=ProductCode.Kind.EAN, code=code, supplier__isnull=True
    ).exclude(product=product).first()
    if conflict:
        raise ValidationError(f"Codul este deja asociat produsului «{conflict.product.name}».")
    ProductCode.objects.update_or_create(
        kind=ProductCode.Kind.EAN,
        code=code,
        supplier=None,
        defaults={"product": product},
    )
    if product.ean != code:
        product.ean = code
        product.save(update_fields=["ean"])
    return code
