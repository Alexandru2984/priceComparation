from decimal import Decimal

from django.shortcuts import redirect, render


def public_demo(request):
    if request.user.is_staff:
        return redirect("comparator:dashboard")
    return render(
        request,
        "public_demo.html",
        {
            "public_mode": True,
            "mock_product_count": 126,
            "mock_offer_count": 118,
            "mock_review_count": 4,
            "mock_comparisons": [
                {
                    "product": "Iaurt natur 400 g",
                    "supplier": "Furnizor demonstrativ",
                    "invoice_price": Decimal("9.48"),
                    "metro_price": Decimal("8.75"),
                    "difference": Decimal("8.3"),
                    "status": "MAI_SCUMP",
                },
                {
                    "product": "Apă plată 2 L",
                    "supplier": "Distribuitor demonstrativ",
                    "invoice_price": Decimal("2.10"),
                    "metro_price": Decimal("2.32"),
                    "difference": Decimal("-9.5"),
                    "status": "MAI_IEFTIN",
                },
                {
                    "product": "Ulei floarea-soarelui 1 L",
                    "supplier": "Furnizor demonstrativ",
                    "invoice_price": Decimal("7.90"),
                    "metro_price": Decimal("7.90"),
                    "difference": Decimal("0"),
                    "status": "EGAL",
                },
            ],
        },
    )


def service_worker(request):
    response = render(request, "service_worker.js", content_type="application/javascript")
    response["Service-Worker-Allowed"] = "/"
    response["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


def _error_response(request, status, title, message):
    return render(
        request,
        "errors/error.html",
        {"status": status, "error_title": title, "error_message": message},
        status=status,
    )


def bad_request(request, exception=None):
    return _error_response(
        request,
        400,
        "Cerere invalidă",
        "Datele trimise nu au putut fi procesate. Verifică formularul și încearcă din nou.",
    )


def permission_denied(request, exception=None):
    return _error_response(
        request,
        403,
        "Acces restricționat",
        "Contul autentificat nu are permisiunea necesară pentru această operație.",
    )


def page_not_found(request, exception=None):
    return _error_response(
        request,
        404,
        "Pagina nu există",
        "Adresa cerută nu corespunde niciunei pagini disponibile.",
    )


def server_error(request):
    return _error_response(
        request,
        500,
        "Eroare temporară",
        "Operația nu a putut fi finalizată. Încearcă din nou și verifică jurnalul dacă problema persistă.",
    )
