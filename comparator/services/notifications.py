import json
import logging
from decimal import Decimal
from urllib.parse import urlparse

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from pywebpush import WebPushException, webpush

from comparator.models import PriceAlert, PushSubscription

logger = logging.getLogger(__name__)


def is_allowed_push_endpoint(endpoint):
    parsed = urlparse(endpoint or "")
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not hostname or parsed.username or parsed.password:
        return False
    return any(hostname == allowed or hostname.endswith(f".{allowed}") for allowed in settings.WEBPUSH_ALLOWED_HOSTS)


def webpush_configured():
    return bool(settings.WEBPUSH_VAPID_PRIVATE_KEY and settings.WEBPUSH_VAPID_PUBLIC_KEY)


def send_push(subscription, payload):
    if not subscription.active or not webpush_configured() or not is_allowed_push_endpoint(subscription.endpoint):
        return False
    try:
        webpush(
            subscription_info={
                "endpoint": subscription.endpoint,
                "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth},
            },
            data=json.dumps(payload, ensure_ascii=False),
            vapid_private_key=settings.WEBPUSH_VAPID_PRIVATE_KEY,
            vapid_claims={"sub": settings.WEBPUSH_VAPID_SUBJECT},
            ttl=3600,
            timeout=12,
        )
    except WebPushException as exc:
        status_code = getattr(exc.response, "status_code", None)
        if status_code in {404, 410}:
            subscription.active = False
            subscription.save(update_fields=["active", "updated_at"])
        logger.warning("Web Push failed for subscription %s: status=%s", subscription.pk, status_code)
        return False
    except Exception:
        logger.exception("Unexpected Web Push failure for subscription %s", subscription.pk)
        return False
    return True


def send_to_active_staff(payload, user=None):
    subscriptions = PushSubscription.objects.filter(active=True, user__is_active=True, user__is_staff=True)
    if user:
        subscriptions = subscriptions.filter(user=user)
    return sum(1 for subscription in subscriptions if send_push(subscription, payload))


@transaction.atomic
def send_triggered_price_alerts():
    sent_alerts = 0
    sent_messages = 0
    alerts = PriceAlert.objects.select_related("product").prefetch_related("product__metro_offers").filter(active=True)
    for alert in alerts:
        offer = alert.current_offer
        if not offer or offer.price_per_base_unit > alert.target_price:
            if alert.last_notified_price is not None:
                alert.last_notified_price = None
                alert.save(update_fields=["last_notified_price"])
            continue
        current_price = offer.price_per_base_unit.quantize(Decimal("0.01"))
        if alert.last_notified_price == current_price:
            continue
        payload = {
            "title": "PriceMatch · preț bun",
            "body": f"{alert.product.name}: {current_price:.2f} lei/{alert.product.base_unit}",
            "url": f"/app/catalog/{alert.product_id}/",
            "tag": f"price-alert-{alert.pk}",
        }
        delivered = send_to_active_staff(payload)
        if delivered:
            alert.last_notified_at = timezone.now()
            alert.last_notified_price = current_price
            alert.save(update_fields=["last_notified_at", "last_notified_price"])
            sent_alerts += 1
            sent_messages += delivered
    return {"alerts": sent_alerts, "messages": sent_messages}
