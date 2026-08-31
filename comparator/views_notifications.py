import json
import re

from django.conf import settings
from django.http import HttpResponseNotAllowed, JsonResponse
from django.shortcuts import render

from .models import PushSubscription
from .services.notifications import (
    is_allowed_push_endpoint,
    send_to_active_staff,
    webpush_configured,
)


def notification_settings(request):
    return render(
        request,
        "comparator/notification_settings.html",
        {
            "webpush_ready": webpush_configured(),
            "vapid_public_key": settings.WEBPUSH_VAPID_PUBLIC_KEY,
            "active_subscription_count": PushSubscription.objects.filter(user=request.user, active=True).count(),
        },
    )


def push_subscribe(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    if not webpush_configured():
        return JsonResponse({"error": "Notificările nu sunt configurate pe server."}, status=503)
    if len(request.body) > 16_384:
        return JsonResponse({"error": "Datele abonamentului sunt prea mari."}, status=400)
    try:
        subscription = json.loads(request.body)
        endpoint = subscription["endpoint"].strip()
        p256dh = subscription["keys"]["p256dh"].strip()
        auth = subscription["keys"]["auth"].strip()
    except (json.JSONDecodeError, KeyError, TypeError, AttributeError):
        return JsonResponse({"error": "Abonament invalid."}, status=400)
    token_pattern = re.compile(r"^[A-Za-z0-9_-]{16,255}$")
    if (
        len(endpoint) > 1000
        or not is_allowed_push_endpoint(endpoint)
        or not token_pattern.fullmatch(p256dh)
        or not token_pattern.fullmatch(auth)
    ):
        return JsonResponse({"error": "Abonament invalid."}, status=400)
    existing = PushSubscription.objects.filter(endpoint=endpoint).first()
    if existing and existing.user_id != request.user.id:
        return JsonResponse({"error": "Abonamentul aparține altui cont."}, status=409)
    PushSubscription.objects.update_or_create(
        endpoint=endpoint,
        defaults={
            "user": request.user,
            "p256dh": p256dh,
            "auth": auth,
            "user_agent": request.headers.get("User-Agent", "")[:300],
            "active": True,
        },
    )
    return JsonResponse({"ok": True})


def push_unsubscribe(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    try:
        endpoint = json.loads(request.body).get("endpoint", "").strip()
    except (json.JSONDecodeError, AttributeError):
        return JsonResponse({"error": "Cerere invalidă."}, status=400)
    PushSubscription.objects.filter(user=request.user, endpoint=endpoint).update(active=False)
    return JsonResponse({"ok": True})


def push_test(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    delivered = send_to_active_staff(
        {
            "title": "PriceMatch · test reușit",
            "body": "Telefonul poate primi alerte de preț.",
            "url": "/app/alerte/",
            "tag": "pricematch-test",
        },
        user=request.user,
    )
    if not delivered:
        return JsonResponse({"error": "Nu s-a putut livra notificarea."}, status=502)
    return JsonResponse({"ok": True, "delivered": delivered})
