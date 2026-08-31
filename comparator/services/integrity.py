from django.db.models import Count, F, Q

from comparator.models import Invoice, InvoiceLine, MetroOffer, Product, ProductCode, SupplierOffer


def _check(name, severity, queryset, detail, example_field="pk"):
    count = queryset.count()
    return {
        "name": name,
        "severity": severity,
        "count": count,
        "detail": detail,
        "examples": [str(value)[:120] for value in queryset.values_list(example_field, flat=True)[:5]],
    }


def audit_data_integrity():
    """Find data states that can make price, matching, or stock results unreliable."""
    duplicate_eans = (
        Product.objects.exclude(ean="")
        .values("ean")
        .annotate(duplicates=Count("id"))
        .filter(duplicates__gt=1)
        .order_by("ean")
    )
    products_without_matching_ean_code = Product.objects.exclude(ean="").exclude(
        codes__kind=ProductCode.Kind.EAN,
        codes__code=F("ean"),
    )
    invalid_lines = InvoiceLine.objects.filter(
        Q(quantity__lte=0)
        | Q(units_per_package__lte=0)
        | Q(unit_size__lte=0)
        | Q(unit_price_gross__lt=0)
        | Q(vat_rate__lt=0)
        | Q(vat_rate__gt=100)
    )
    confirmed_unmatched_lines = InvoiceLine.objects.filter(
        needs_review=False,
        matched_product__isnull=True,
    )
    invalid_offers = MetroOffer.objects.filter(Q(units_per_package__lte=0) | Q(unit_size__lte=0) | Q(price_gross__lt=0))
    zero_active_offers = MetroOffer.objects.filter(active=True, price_gross=0)
    mismatched_supplier_units = SupplierOffer.objects.exclude(base_unit=F("product__base_unit"))
    processed_documents_under_review = Invoice.objects.filter(
        status=Invoice.Status.PROCESSED,
        lines__needs_review=True,
    ).distinct()

    checks = [
        _check(
            "EAN duplicat în catalog",
            "ERROR",
            duplicate_eans,
            "Același cod nu trebuie să identifice mai multe produse.",
            "ean",
        ),
        _check(
            "EAN fără cod de catalog sincronizat",
            "WARN",
            products_without_matching_ean_code,
            "Câmpul EAN vechi și registrul de coduri trebuie să indice aceeași valoare.",
            "name",
        ),
        _check(
            "Linii de document cu valori imposibile",
            "ERROR",
            invalid_lines,
            "Cantitățile trebuie să fie pozitive, iar prețul și TVA-ul valide.",
            "original_name",
        ),
        _check(
            "Linii confirmate fără produs asociat",
            "ERROR",
            confirmed_unmatched_lines,
            "O linie fără asociere trebuie să rămână marcată pentru revizie.",
            "original_name",
        ),
        _check(
            "Oferte METRO cu valori imposibile",
            "ERROR",
            invalid_offers,
            "Ambalarea trebuie să fie pozitivă, iar prețul nu poate fi negativ.",
            "product__name",
        ),
        _check(
            "Oferte METRO active cu preț zero",
            "WARN",
            zero_active_offers,
            "Un preț zero activ poate denatura recomandările de cumpărare.",
            "product__name",
        ),
        _check(
            "Unități furnizor incompatibile",
            "ERROR",
            mismatched_supplier_units,
            "Oferta furnizorului trebuie normalizată în unitatea produsului.",
            "product__name",
        ),
        _check(
            "Documente procesate cu linii în revizie",
            "WARN",
            processed_documents_under_review,
            "Documentul trebuie trecut în starea de revizie până la confirmarea liniilor.",
            "pk",
        ),
    ]
    return {
        "checks": checks,
        "error_count": sum(check["count"] for check in checks if check["severity"] == "ERROR"),
        "warning_count": sum(check["count"] for check in checks if check["severity"] == "WARN"),
    }
