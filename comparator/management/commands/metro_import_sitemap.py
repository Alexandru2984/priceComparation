from django.core.management.base import BaseCommand, CommandError

from comparator.services.metro_sitemap import (
    MetroSitemapError,
    fetch_metro_sitemap_products,
    import_metro_sitemap_products,
)


class Command(BaseCommand):
    help = "Importă rapid catalogul public național METRO din sitemap, fără a inventa prețuri locale."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Descarcă și validează sitemapul fără a modifica baza de date.",
        )

    def handle(self, *args, **options):
        try:
            products = fetch_metro_sitemap_products()
        except (MetroSitemapError, OSError) as exc:
            raise CommandError(str(exc)) from exc

        if options["dry_run"]:
            self.stdout.write(self.style.SUCCESS(
                f"Sitemap valid: {len(products)} coduri METRO unice. Baza de date nu a fost modificată."
            ))
            return

        stats = import_metro_sitemap_products(products)
        self.stdout.write(self.style.SUCCESS(
            "Import sitemap finalizat: "
            f"{stats['discovered']} coduri descoperite, "
            f"{stats['new_codes']} coduri noi, "
            f"{stats['new_products']} produse noi, "
            f"{stats['existing_codes']} coduri deja cunoscute."
        ))
