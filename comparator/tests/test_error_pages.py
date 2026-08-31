from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase, override_settings

from pricecompare.views import bad_request, server_error


@override_settings(DEBUG=False)
class ErrorPageTests(TestCase):
    def test_unknown_route_uses_safe_custom_404(self):
        response = self.client.get("/not-a-real-page/private-value/")

        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "Pagina nu există", status_code=404)
        self.assertNotContains(response, "private-value", status_code=404)

    def test_permission_denied_uses_custom_403_without_internal_reason(self):
        operator = get_user_model().objects.create_user(
            "operator-errors", password="Strong-test-password-2026!", is_staff=True
        )
        self.client.force_login(operator)

        response = self.client.get("/app/operare/")

        self.assertEqual(response.status_code, 403)
        self.assertContains(response, "Acces restricționat", status_code=403)
        self.assertNotContains(response, "superuser", status_code=403)

    def test_direct_error_handlers_do_not_render_exception_details(self):
        request = RequestFactory().get("/")
        request.user = get_user_model()()

        response_400 = bad_request(request, ValueError("sensitive parser detail"))
        response_500 = server_error(request)

        self.assertEqual(response_400.status_code, 400)
        self.assertNotIn(b"sensitive parser detail", response_400.content)
        self.assertEqual(response_500.status_code, 500)
        self.assertIn("Eroare temporară".encode(), response_500.content)
