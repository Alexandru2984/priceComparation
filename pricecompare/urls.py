from django.contrib import admin
from django.urls import include, path

from .views import public_demo


urlpatterns = [
    path("", public_demo, name="public_demo"),
    path("admin/", admin.site.urls),
    path("app/", include("comparator.urls")),
]
