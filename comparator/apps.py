from django.apps import AppConfig


class ComparatorConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "comparator"

    def ready(self):
        from pillow_heif import register_heif_opener

        register_heif_opener()
        from . import checks  # noqa: F401
