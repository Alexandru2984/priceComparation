from django.core.exceptions import PermissionDenied
from django.db import DatabaseError

from comparator.auth import enforce_mfa
from comparator.models import ActivityLog
from pricecompare.security import get_client_ip_address


class AdminMFAEnforcementMiddleware:
    """Apply the same mandatory MFA policy to Django admin URLs."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith("/admin/"):
            if request.user.is_authenticated and request.user.is_staff and not request.user.is_superuser:
                raise PermissionDenied("Django Admin este rezervat administratorilor PriceMatch.")
            mfa_response = enforce_mfa(request)
            if mfa_response:
                return mfa_response
        return self.get_response(request)


class ActivityAuditMiddleware:
    """Record mutating private requests without storing form data or document contents."""

    MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
    AUDITED_READ_VIEWS = {"comparator:data_export_download"}

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        user = getattr(request, "user", None)
        resolver_match = getattr(request, "resolver_match", None)
        view_name = resolver_match.view_name if resolver_match else ""
        if (
            (request.method in self.MUTATING_METHODS or view_name in self.AUDITED_READ_VIEWS)
            and request.path.startswith(("/app/", "/admin/"))
            and user is not None
            and user.is_authenticated
            and user.is_staff
        ):
            if response.status_code in {401, 403}:
                outcome = ActivityLog.Outcome.DENIED
            elif response.status_code >= 400:
                outcome = ActivityLog.Outcome.ERROR
            else:
                outcome = ActivityLog.Outcome.SUCCESS
            try:
                ActivityLog.objects.create(
                    user=user,
                    method=request.method,
                    path=request.path[:500],
                    view_name=view_name[:180],
                    status_code=response.status_code,
                    outcome=outcome,
                    ip_address=get_client_ip_address(request),
                )
            except (DatabaseError, ValueError):
                pass
        return response


class SecurityHeadersMiddleware:
    """Add restrictive browser policies to both public and private responses."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'; "
            "object-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; font-src 'self'; connect-src 'self'",
        )
        camera = "(self)" if request.path == "/app/catalog/scaneaza-ean/" else "()"
        response.setdefault(
            "Permissions-Policy", f"camera={camera}, microphone=(), geolocation=(), payment=()"
        )
        if request.path.startswith(("/app/", "/admin/", "/account/")):
            # Keep authentication and private HTML unchanged between Django and the browser.
            # In particular, this prevents edge-side script injection on sensitive pages.
            response["Cache-Control"] = "private, no-store, no-transform"
        return response
