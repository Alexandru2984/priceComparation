from functools import wraps

from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required as django_staff_member_required
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.urls import reverse
from django_otp import user_has_device


def enforce_mfa(request):
    if not settings.MFA_REQUIRED or not request.user.is_authenticated or request.user.is_verified():
        return None
    destination = "two_factor:login" if user_has_device(request.user) else "two_factor:setup"
    return redirect_to_login(request.get_full_path(), reverse(destination))


def staff_member_required(view_func):
    """Require a staff account and, in online mode, a verified OTP session."""
    protected = django_staff_member_required(view_func, login_url=settings.LOGIN_URL)

    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        if request.user.is_authenticated and request.user.is_staff:
            mfa_response = enforce_mfa(request)
            if mfa_response:
                return mfa_response
        return protected(request, *args, **kwargs)

    return wrapped


def app_admin_required(view_func):
    """Require the terminal-created administrator role for configuration changes."""
    protected = staff_member_required(view_func)

    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        if request.user.is_authenticated and request.user.is_staff and not request.user.is_superuser:
            raise PermissionDenied("Această operație necesită rolul de administrator PriceMatch.")
        return protected(request, *args, **kwargs)

    return wrapped
