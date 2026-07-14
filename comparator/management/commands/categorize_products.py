from django.core.management.base import BaseCommand

from comparator.catalog import infer_category
from comparator.models import MetroScrapedProduct, Product


class Command(BaseCommand):
    help = "Completează automat categoriile produselor din catalog pe baza denumirii."

    def add_arguments(self, parser):
        parser.add_argument("--overwrite", action="store_true", help="Recalculează inclusiv categoriile existente.")

    def handle(self, *args, **options):
        staged_categories = {}
        staged_rows = MetroScrapedProduct.objects.filter(
            imported=True, matched_product__isnull=False
        ).exclude(category="").order_by("captured_at")
        for row in staged_rows.only("matched_product_id", "category", "captured_at"):
            staged_categories[row.matched_product_id] = row.category

        products = Product.objects.all()
        if not options["overwrite"]:
            products = products.filter(category="")
        changed = 0
        for product in products.iterator():
            inferred = infer_category(product.name)
            category = inferred if inferred != "Altele" else staged_categories.get(product.pk, "Altele")
            if product.category != category:
                product.category = category
                product.save(update_fields=["category"])
                changed += 1
        self.stdout.write(self.style.SUCCESS(f"Categorii actualizate: {changed}."))
