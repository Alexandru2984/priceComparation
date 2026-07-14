from django.contrib.admin.views.decorators import staff_member_required
from django.urls import path

from . import views


app_name = "comparator"

urlpatterns = [
    path("", staff_member_required(views.dashboard), name="dashboard"),
    path("furnizori/", staff_member_required(views.supplier_list), name="supplier_list"),
    path("furnizori/adauga/", staff_member_required(views.supplier_create), name="supplier_create"),
    path("catalog/", staff_member_required(views.product_list), name="product_list"),
    path("catalog/adauga/", staff_member_required(views.product_create), name="product_create"),
    path("metro/", staff_member_required(views.metro_list), name="metro_list"),
    path("metro/adauga/", staff_member_required(views.metro_offer_create), name="metro_offer_create"),
    path("metro/importa/", staff_member_required(views.metro_import), name="metro_import"),
    path("metro/sincronizeaza-documente/", staff_member_required(views.metro_sync_documents), name="metro_sync_documents"),
    path("metro/scanari/", staff_member_required(views.metro_scrape_list), name="metro_scrape_list"),
    path("metro/scanari/porneste/", staff_member_required(views.metro_scrape_start), name="metro_scrape_start"),
    path("metro/scanari/<int:pk>/", staff_member_required(views.metro_scrape_detail), name="metro_scrape_detail"),
    path("metro/scanari/<int:pk>/importa/", staff_member_required(views.metro_scrape_import), name="metro_scrape_import"),
    path("facturi/", staff_member_required(views.invoice_list), name="invoice_list"),
    path("facturi/adauga/", staff_member_required(views.invoice_create), name="invoice_create"),
    path("facturi/<int:pk>/", staff_member_required(views.invoice_detail), name="invoice_detail"),
    path("facturi/<int:pk>/fisier/", staff_member_required(views.invoice_file_download), name="invoice_file_download"),
    path("documente/pagini/<int:pk>/fisier/", staff_member_required(views.document_page_download), name="document_page_download"),
    path("facturi/<int:pk>/proceseaza/", staff_member_required(views.invoice_process), name="invoice_process"),
    path("facturi/<int:pk>/linie/adauga/", staff_member_required(views.line_create), name="line_create"),
    path("linii/<int:pk>/editeaza/", staff_member_required(views.line_edit), name="line_edit"),
    path("linii/<int:pk>/sterge/", staff_member_required(views.line_delete), name="line_delete"),
]
