from django.contrib import admin
from django.urls import include, path
from two_factor.urls import urlpatterns as two_factor_urls

from .views import public_demo, service_worker


urlpatterns = [
    path("service-worker.js", service_worker, name="service_worker"),
    path("", public_demo, name="public_demo"),
    path("", include(two_factor_urls)),
    path("admin/", admin.site.urls),
    path("app/", include("comparator.urls")),
]
