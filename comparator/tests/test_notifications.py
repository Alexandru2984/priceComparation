import json
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from comparator.models import MetroOffer, PriceAlert, Product, PushSubscription
from comparator.services.notifications import is_allowed_push_endpoint, send_triggered_price_alerts

PUSH_SETTINGS = {
    "WEBPUSH_VAPID_PRIVATE_KEY": "/tmp/test-vapid.pem",
    "WEBPUSH_VAPID_PUBLIC_KEY": "B" + "x" * 86,
    "WEBPUSH_VAPID_SUBJECT": "mailto:test@example.com",
}


class PushEndpointTests(TestCase):
    @override_settings(WEBPUSH_ALLOWED_HOSTS=["fcm.googleapis.com", "push.services.mozilla.com"])
    def test_only_known_https_push_hosts_are_allowed(self):
        self.assertTrue(is_allowed_push_endpoint("https://fcm.googleapis.com/wp/abc"))
        self.assertTrue(is_allowed_push_endpoint("https://updates.push.services.mozilla.com/wpush/abc"))
        self.assertFalse(is_allowed_push_endpoint("http://fcm.googleapis.com/wp/abc"))
        self.assertFalse(is_allowed_push_endpoint("https://fcm.googleapis.com.attacker.test/wp/abc"))
        self.assertFalse(is_allowed_push_endpoint("https://user:pass@fcm.googleapis.com/wp/abc"))


@override_settings(**PUSH_SETTINGS)
class PushViewTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user(
            username="push-admin", password="A-test-password-2026!", is_staff=True
        )
        self.client.force_login(self.staff)
        self.payload = {
            "endpoint": "https://fcm.googleapis.com/wp/test-endpoint",
            "keys": {"p256dh": "a" * 65, "auth": "b" * 24},
        }

    def test_staff_can_register_and_disable_own_subscription(self):
        response = self.client.post(
            "/app/notificari/aboneaza/", json.dumps(self.payload), content_type="application/json"
        )
        self.assertEqual(response.status_code, 200)
        subscription = PushSubscription.objects.get(user=self.staff)
        self.assertTrue(subscription.active)

        response = self.client.post(
            "/app/notificari/dezaboneaza/",
            json.dumps({"endpoint": self.payload["endpoint"]}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        subscription.refresh_from_db()
        self.assertFalse(subscription.active)

    def test_external_endpoint_is_rejected(self):
        self.payload["endpoint"] = "https://attacker.example/push"
        response = self.client.post(
            "/app/notificari/aboneaza/", json.dumps(self.payload), content_type="application/json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(PushSubscription.objects.exists())

    def test_subscription_cannot_be_taken_from_another_staff_account(self):
        other = get_user_model().objects.create_user(
            username="other-push-admin", password="A-test-password-2026!", is_staff=True
        )
        PushSubscription.objects.create(
            user=other,
            endpoint=self.payload["endpoint"],
            p256dh="c" * 65,
            auth="d" * 24,
        )
        response = self.client.post(
            "/app/notificari/aboneaza/", json.dumps(self.payload), content_type="application/json"
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(PushSubscription.objects.get().user, other)

    def test_service_worker_is_public_and_not_cached(self):
        self.client.logout()
        response = self.client.get("/service-worker.js")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Service-Worker-Allowed"], "/")
        self.assertIn("no-store", response["Cache-Control"])


@override_settings(**PUSH_SETTINGS)
class PriceAlertDeliveryTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user(
            username="alerts-admin", password="A-test-password-2026!", is_staff=True
        )
        self.product = Product.objects.create(name="Apă test 2 L", base_unit="L")
        MetroOffer.objects.create(
            product=self.product,
            units_per_package=1,
            unit_size=2,
            price_gross=Decimal("4.00"),
            valid_from=date(2026, 8, 9),
        )
        self.alert = PriceAlert.objects.create(product=self.product, target_price=Decimal("2.10"))
        PushSubscription.objects.create(
            user=self.staff,
            endpoint="https://fcm.googleapis.com/wp/alert-test",
            p256dh="a" * 65,
            auth="b" * 24,
        )

    @patch("comparator.services.notifications.webpush")
    def test_triggered_alert_is_sent_only_once_at_same_price(self, mocked_webpush):
        first = send_triggered_price_alerts()
        second = send_triggered_price_alerts()

        self.assertEqual(first, {"alerts": 1, "messages": 1})
        self.assertEqual(second, {"alerts": 0, "messages": 0})
        self.assertEqual(mocked_webpush.call_count, 1)
        self.alert.refresh_from_db()
        self.assertEqual(self.alert.last_notified_price, Decimal("2.00"))

    @patch("comparator.services.notifications.webpush")
    def test_alert_rearms_after_price_moves_above_target(self, mocked_webpush):
        send_triggered_price_alerts()
        offer = self.product.metro_offers.get()
        offer.price_gross = Decimal("5.00")
        offer.save()
        send_triggered_price_alerts()
        self.alert.refresh_from_db()
        self.assertIsNone(self.alert.last_notified_price)

        offer.price_gross = Decimal("3.80")
        offer.save()
        result = send_triggered_price_alerts()
        self.assertEqual(result["alerts"], 1)
        self.assertEqual(mocked_webpush.call_count, 2)
