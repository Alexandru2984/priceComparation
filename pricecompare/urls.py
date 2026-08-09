from django.contrib import admin
from django.urls import include, path

from .views import public_demo, service_worker


urlpatterns = [
    path("service-worker.js", service_worker, name="service_worker"),
    path("", public_demo, name="public_demo"),
    path("admin/", admin.site.urls),
    path("app/", include("comparator.urls")),
]
