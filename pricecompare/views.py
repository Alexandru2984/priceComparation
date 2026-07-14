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
