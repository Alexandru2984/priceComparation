from django.contrib import admin
from django.urls import include, path
from two_factor.urls import urlpatterns as two_factor_urls

from .views import (
    PriceMatchLoginView,
    bad_request,
    page_not_found,
    permission_denied,
    public_demo,
    server_error,
    service_worker,
)

handler400 = bad_request
handler403 = permission_denied
handler404 = page_not_found
handler500 = server_error

urlpatterns = [
    path("service-worker.js", service_worker, name="service_worker"),
    path("", public_demo, name="public_demo"),
    # This path intentionally precedes django-two-factor-auth's identical URL.
    # The view preserves MFA when required and bypasses only the OTP step when disabled.
    path("account/login/", PriceMatchLoginView.as_view(), name="pricematch_login"),
    path("", include(two_factor_urls)),
    path("admin/", admin.site.urls),
    path("app/", include("comparator.urls")),
]
