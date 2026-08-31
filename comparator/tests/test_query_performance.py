from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from comparator.models import (
    BaseUnit,
    DocumentProcessingJob,
    Invoice,
    InvoiceLine,
    MetroOffer,
    Product,
    Supplier,
    SupplierOffer,
)
from comparator.services.insights import (
    current_metro_offers_prefetch,
    current_source_options,
    matching_quality_summary,
    source_option_prefetches,
)
from comparator.services.invoices import prime_invoice_merchandise_totals


class QueryPerformanceTests(TestCase):
    def setUp(self):
        self.supplier = Supplier.objects.create(name="Furnizor performanță")
        self.product = Product.objects.create(
            name="Produs performanță",
            base_unit=BaseUnit.PIECE,
        )
        self.invoice = Invoice.objects.create(
            supplier=self.supplier,
            issued_at=date(2026, 8, 31),
        )
        self.line = InvoiceLine.objects.create(
            invoice=self.invoice,
            original_name=self.product.name,
            quantity=2,
            unit_price_gross=Decimal("8.00"),
            matched_product=self.product,
            needs_review=False,
        )
        MetroOffer.objects.create(
            product=self.product,
            units_per_package=1,
            unit_size=1,
            price_gross=Decimal("7.00"),
            valid_from=date(2026, 8, 31),
        )
        MetroOffer.objects.create(
            product=self.product,
            units_per_package=1,
            unit_size=1,
            price_gross=Decimal("1.00"),
            valid_from=date(2026, 8, 30),
            active=False,
        )
        SupplierOffer.objects.create(
            supplier=self.supplier,
            product=self.product,
            invoice_line=self.line,
            price_per_base_unit=Decimal("8.00"),
            base_unit=BaseUnit.PIECE,
            valid_from=date(2026, 8, 31),
        )

    def test_matching_quality_uses_one_aggregate_query(self):
        with self.assertNumQueries(1):
            summary = matching_quality_summary()

        self.assertEqual(summary["confirmed"], 1)

    def test_prefetched_source_options_do_not_query_per_product(self):
        product = Product.objects.prefetch_related(*source_option_prefetches()).get(pk=self.product.pk)

        with self.assertNumQueries(0):
            options = current_source_options(product, Decimal("2"))

        self.assertEqual([option["kind"] for option in options], ["METRO", "SUPPLIER"])
        self.assertEqual(len(product._prefetched_objects_cache["metro_offers"]), 1)

    def test_prefetched_comparisons_do_not_query_per_line(self):
        lines = prime_invoice_merchandise_totals(
            InvoiceLine.objects.select_related("invoice", "matched_product").prefetch_related(
                current_metro_offers_prefetch("matched_product__metro_offers")
            )
        )

        with self.assertNumQueries(0):
            comparisons = [line.comparison() for line in lines]

        self.assertEqual(comparisons[0]["status"], "MAI_SCUMP")

    def test_invoice_list_prefetches_only_active_processing_jobs(self):
        DocumentProcessingJob.objects.create(
            invoice=self.invoice,
            status=DocumentProcessingJob.Status.COMPLETED,
        )
        active_job = DocumentProcessingJob.objects.create(invoice=self.invoice)
        admin = get_user_model().objects.create_superuser("query-admin", password="Strong-test-password-2026!")
        self.client.force_login(admin)

        response = self.client.get("/app/facturi/")

        listed_invoice = response.context["page_obj"].object_list[0]
        self.assertEqual(listed_invoice.line_count, 1)
        self.assertEqual(listed_invoice.active_processing_jobs, [active_job])
