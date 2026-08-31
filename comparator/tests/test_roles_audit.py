from django.contrib.auth import get_user_model
from django.test import TestCase

from comparator.models import ActivityLog, Supplier


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
