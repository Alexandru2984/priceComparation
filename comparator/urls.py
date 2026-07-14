from django.urls import path

from . import views


app_name = "comparator"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("furnizori/", views.supplier_list, name="supplier_list"),
    path("furnizori/adauga/", views.supplier_create, name="supplier_create"),
    path("catalog/", views.product_list, name="product_list"),
    path("catalog/adauga/", views.product_create, name="product_create"),
    path("metro/", views.metro_list, name="metro_list"),
    path("metro/adauga/", views.metro_offer_create, name="metro_offer_create"),
    path("metro/importa/", views.metro_import, name="metro_import"),
    path("metro/sincronizeaza-documente/", views.metro_sync_documents, name="metro_sync_documents"),
    path("metro/scanari/", views.metro_scrape_list, name="metro_scrape_list"),
    path("metro/scanari/porneste/", views.metro_scrape_start, name="metro_scrape_start"),
    path("metro/scanari/<int:pk>/", views.metro_scrape_detail, name="metro_scrape_detail"),
    path("metro/scanari/<int:pk>/importa/", views.metro_scrape_import, name="metro_scrape_import"),
    path("facturi/", views.invoice_list, name="invoice_list"),
    path("facturi/adauga/", views.invoice_create, name="invoice_create"),
    path("facturi/<int:pk>/", views.invoice_detail, name="invoice_detail"),
    path("facturi/<int:pk>/proceseaza/", views.invoice_process, name="invoice_process"),
    path("facturi/<int:pk>/linie/adauga/", views.line_create, name="line_create"),
    path("linii/<int:pk>/editeaza/", views.line_edit, name="line_edit"),
    path("linii/<int:pk>/sterge/", views.line_delete, name="line_delete"),
]
