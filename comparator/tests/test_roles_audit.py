from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase

from comparator.models import ActivityLog, Invoice, Product, Supplier


class RoleAndAuditTests(TestCase):
    def setUp(self):
        users = get_user_model()
        self.operator = users.objects.create_user(
            "operator-role", password="A-test-password-2026!", is_staff=True
        )
        self.owner = users.objects.create_superuser(
            "owner-role", password="A-test-password-2026!"
        )

    def test_operator_can_use_document_flow_but_not_configuration(self):
        self.client.force_login(self.operator)

        self.assertEqual(self.client.get("/app/facturi/adauga/").status_code, 200)
        self.assertEqual(self.client.get("/app/furnizori/adauga/").status_code, 403)
        self.assertEqual(self.client.get("/app/jurnal/").status_code, 403)

    def test_operator_pages_hide_administrator_actions(self):
        supplier = Supplier.objects.create(name="Furnizor vizibil")
        invoice = Invoice.objects.create(supplier=supplier, issued_at=date(2026, 8, 31))
        self.client.force_login(self.operator)

        supplier_page = self.client.get("/app/furnizori/")
        self.assertNotContains(supplier_page, "/app/furnizori/adauga/")
        self.assertNotContains(supplier_page, "Importă listă de preț")
        catalog_page = self.client.get("/app/catalog/")
        self.assertNotContains(catalog_page, "/app/catalog/adauga/")
        inventory_page = self.client.get("/app/stoc/")
        self.assertNotContains(inventory_page, "Importă vânzări POS")
        self.assertNotContains(inventory_page, "Adaugă sau actualizează politica")
        metro_page = self.client.get("/app/metro/")
        self.assertNotContains(metro_page, "Scanare Selenium")
        self.assertNotContains(metro_page, "Importă CSV")
        document_page = self.client.get(f"/app/facturi/{invoice.pk}/")
        self.assertNotContains(document_page, "Șterge documentul")

    def test_operator_cannot_change_alerts_or_inventory_via_direct_post(self):
        product = Product.objects.create(name="Produs protejat", base_unit="BUC")
        self.client.force_login(self.operator)

        alert_response = self.client.post(
            "/app/alerte/",
            {"product": product.pk, "target_price": "10.00", "active": "on"},
        )
        inventory_response = self.client.post(
            "/app/stoc/",
            {
                "product": product.pk,
                "minimum_quantity": "1",
                "target_quantity": "2",
                "retail_unit_size": "1",
                "retail_vat_rate": "0",
                "purchase_vat_rate": "0",
                "target_margin_percent": "20",
                "expected_waste_percent": "0",
                "active": "on",
            },
        )

        self.assertEqual(alert_response.status_code, 403)
        self.assertEqual(inventory_response.status_code, 403)

    def test_operator_can_assign_a_scanned_ean_during_receiving(self):
        product = Product.objects.create(name="Produs scanat de operator", base_unit="BUC")
        self.client.force_login(self.operator)

        response = self.client.post(
            "/app/catalog/ean/asociaza/",
            {"code": "4006381333931", "product": product.pk},
        )

        self.assertRedirects(response, f"/app/catalog/{product.pk}/")
        product.refresh_from_db()
        self.assertEqual(product.ean, "4006381333931")

    def test_administrator_can_change_configuration(self):
        self.client.force_login(self.owner)

        response = self.client.post(
            "/app/furnizori/adauga/",
            {
                "name": "Furnizor auditat",
                "tax_id": "RO123456",
                "notes": "",
                "minimum_order_gross": "0",
                "transport_gross": "0",
                "free_transport_from": "",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(Supplier.objects.filter(name="Furnizor auditat").exists())

    def test_mutating_private_request_is_logged_without_form_contents(self):
        self.client.force_login(self.owner)

        response = self.client.post(
            "/app/furnizori/adauga/",
            {
                "name": "Secretul nu intră în jurnal",
                "tax_id": "RO999999",
                "notes": "conținut privat",
                "minimum_order_gross": "0",
                "transport_gross": "0",
                "free_transport_from": "",
            },
            REMOTE_ADDR="127.0.0.1",
        )

        self.assertEqual(response.status_code, 302)
        event = ActivityLog.objects.get()
        self.assertEqual(event.user, self.owner)
        self.assertEqual(event.view_name, "comparator:supplier_create")
        self.assertEqual(event.outcome, ActivityLog.Outcome.SUCCESS)
        self.assertEqual(event.ip_address, "127.0.0.1")
        self.assertNotIn("Secretul", str(event))
        self.assertNotIn("RO999999", str(event))

    def test_administrator_can_filter_activity_page(self):
        ActivityLog.objects.create(
            user=self.owner,
            method="POST",
            path="/app/test/",
            view_name="comparator:test",
            status_code=200,
            outcome=ActivityLog.Outcome.SUCCESS,
            ip_address="127.0.0.1",
        )
        self.client.force_login(self.owner)

        response = self.client.get("/app/jurnal/?actor=owner&outcome=SUCCESS&q=test")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "/app/test/")
