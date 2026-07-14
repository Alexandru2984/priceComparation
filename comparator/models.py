from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models


class BaseUnit(models.TextChoices):
    PIECE = "BUC", "Bucată"
    KILOGRAM = "KG", "Kilogram"
    LITER = "L", "Litru"


class Supplier(models.Model):
    name = models.CharField("denumire", max_length=180, unique=True)
    tax_id = models.CharField("CUI", max_length=30, blank=True)
    is_metro = models.BooleanField("este METRO", default=False, help_text="Documentele confirmate de la acest furnizor actualizează prețurile METRO.")
    notes = models.TextField("observații", blank=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "furnizor"
        verbose_name_plural = "furnizori"

    def __str__(self):
        return self.name


class Product(models.Model):
    name = models.CharField("produs", max_length=220)
    brand = models.CharField("marcă", max_length=100, blank=True)
    ean = models.CharField("EAN", max_length=20, blank=True, db_index=True)
    base_unit = models.CharField("unitate de bază", max_length=3, choices=BaseUnit.choices)
    active = models.BooleanField("activ", default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name", "brand"]
        constraints = [
            models.UniqueConstraint(fields=["name", "brand", "base_unit"], name="unique_catalog_product")
        ]
        verbose_name = "produs urmărit"
        verbose_name_plural = "produse urmărite"

    def __str__(self):
        suffix = f" · {self.brand}" if self.brand else ""
        return f"{self.name}{suffix} ({self.base_unit})"

    def current_metro_offer(self):
        offers = self.metro_offers.filter(active=True).order_by("-valid_from")
        latest = offers.first()
        if not latest:
            return None
        return min(
            offers.filter(valid_from=latest.valid_from),
            key=lambda offer: offer.price_per_base_unit,
        )


class MetroOffer(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="metro_offers")
    units_per_package = models.DecimalField(
        "bucăți în pachet", max_digits=10, decimal_places=3, default=1,
        validators=[MinValueValidator(Decimal("0.001"))],
    )
    unit_size = models.DecimalField(
        "cantitate per bucată", max_digits=10, decimal_places=3, default=1,
        validators=[MinValueValidator(Decimal("0.001"))],
    )
    price_gross = models.DecimalField(
        "preț pachet cu TVA", max_digits=12, decimal_places=2,
        validators=[MinValueValidator(Decimal("0"))],
    )
    valid_from = models.DateField("valabil de la")
    source = models.CharField("sursă", max_length=120, default="METRO")
    active = models.BooleanField("ofertă activă", default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-valid_from", "product__name"]
        verbose_name = "preț METRO"
        verbose_name_plural = "prețuri METRO"

    @property
    def total_base_quantity(self):
        return self.units_per_package * self.unit_size

    @property
    def price_per_base_unit(self):
        total = self.total_base_quantity
        return self.price_gross / total if total else Decimal("0")

    def __str__(self):
        return f"{self.product.name}: {self.price_gross} lei / pachet"


class MetroScrapeJob(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "În așteptare"
        RUNNING = "RUNNING", "Browser deschis"
        COMPLETED = "COMPLETED", "Finalizat"
        ERROR = "ERROR", "Eroare"

    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    start_url = models.URLField(max_length=500)
    captured_count = models.PositiveIntegerField(default=0)
    imported_count = models.PositiveIntegerField(default=0)
    current_url = models.URLField(max_length=1000, blank=True)
    error = models.TextField(blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Scanare METRO #{self.pk} · {self.get_status_display()}"


class MetroScrapedProduct(models.Model):
    job = models.ForeignKey(MetroScrapeJob, on_delete=models.CASCADE, related_name="products")
    external_id = models.CharField("cod METRO", max_length=80)
    name = models.CharField("denumire", max_length=240)
    product_url = models.URLField(max_length=1000)
    store_name = models.CharField("magazin", max_length=120, blank=True)
    package_text = models.CharField("ambalare afișată", max_length=120, blank=True)
    units_per_package = models.DecimalField(max_digits=10, decimal_places=3, default=1)
    unit_size = models.DecimalField(max_digits=10, decimal_places=3, default=1)
    base_unit = models.CharField(max_length=3, choices=BaseUnit.choices, default=BaseUnit.PIECE)
    price_gross = models.DecimalField("preț cu TVA", max_digits=12, decimal_places=2)
    matched_product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True, blank=True, related_name="metro_scrape_rows")
    match_score = models.PositiveSmallIntegerField(default=0)
    imported = models.BooleanField(default=False)
    captured_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["job", "external_id"], name="unique_scraped_product_per_job")]

    @property
    def total_base_quantity(self):
        return self.units_per_package * self.unit_size

    @property
    def price_per_base_unit(self):
        total = self.total_base_quantity
        return self.price_gross / total if total else Decimal("0")

    def __str__(self):
        return f"{self.name} · {self.price_gross} lei"


class ProductAlias(models.Model):
    supplier = models.ForeignKey(Supplier, on_delete=models.CASCADE, null=True, blank=True, related_name="aliases")
    alias = models.CharField(max_length=220, db_index=True)
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="aliases")

    class Meta:
        ordering = ["alias"]
        constraints = [
            models.UniqueConstraint(fields=["supplier", "alias"], name="unique_supplier_product_alias")
        ]

    def __str__(self):
        return f"{self.alias} → {self.product.name}"


class Invoice(models.Model):
    class DocumentType(models.TextChoices):
        INVOICE = "INVOICE", "Factură"
        RECEIPT = "RECEIPT", "Bon fiscal"

    class Status(models.TextChoices):
        NEW = "NEW", "Nouă"
        PROCESSED = "PROCESSED", "Procesată"
        REVIEW = "REVIEW", "Necesită verificare"
        ERROR = "ERROR", "Eroare OCR"

    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="invoices")
    document_type = models.CharField("tip document", max_length=10, choices=DocumentType.choices, default=DocumentType.INVOICE)
    number = models.CharField("număr factură/bon", max_length=80, blank=True)
    issued_at = models.DateField("data documentului")
    document = models.FileField("imagine sau PDF", upload_to="invoices/%Y/%m/", blank=True)
    ocr_text = models.TextField("text extras / introdus manual", blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.NEW)
    processing_error = models.TextField(blank=True)
    notes = models.TextField("observații", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-issued_at", "-created_at"]
        verbose_name = "document de achiziție"
        verbose_name_plural = "documente de achiziție"

    def __str__(self):
        return f"{self.supplier} · {self.number or self.issued_at}"


class DocumentPage(models.Model):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="pages")
    file = models.FileField("imagine/PDF", upload_to="documents/%Y/%m/")
    page_order = models.PositiveSmallIntegerField("ordine", default=1)
    ocr_text = models.TextField(blank=True)

    class Meta:
        ordering = ["page_order", "id"]
        constraints = [models.UniqueConstraint(fields=["invoice", "page_order"], name="unique_document_page_order")]

    def __str__(self):
        return f"{self.invoice} · pagina {self.page_order}"


class InvoiceLine(models.Model):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="lines")
    original_name = models.CharField("denumire de pe factură", max_length=240)
    quantity = models.DecimalField(
        "număr pachete/bucăți", max_digits=10, decimal_places=3, default=1,
        validators=[MinValueValidator(Decimal("0.001"))],
    )
    units_per_package = models.DecimalField(
        "bucăți în pachet", max_digits=10, decimal_places=3, default=1,
        validators=[MinValueValidator(Decimal("0.001"))],
    )
    unit_size = models.DecimalField(
        "cantitate per bucată", max_digits=10, decimal_places=3, default=1,
        validators=[MinValueValidator(Decimal("0.001"))],
    )
    base_unit = models.CharField("unitate de bază", max_length=3, choices=BaseUnit.choices, default=BaseUnit.PIECE)
    unit_price_gross = models.DecimalField(
        "preț pachet/bucată cu TVA", max_digits=12, decimal_places=2,
        validators=[MinValueValidator(Decimal("0"))],
    )
    vat_rate = models.DecimalField("TVA %", max_digits=5, decimal_places=2, default=0)
    line_total_gross = models.DecimalField("total linie cu TVA", max_digits=12, decimal_places=2, null=True, blank=True)
    matched_product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True, blank=True, related_name="invoice_lines")
    match_score = models.PositiveSmallIntegerField(default=0)
    needs_review = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
        verbose_name = "linie factură"
        verbose_name_plural = "linii factură"

    @property
    def total_base_quantity(self):
        return self.quantity * self.units_per_package * self.unit_size

    @property
    def price_per_base_unit(self):
        package_base_quantity = self.units_per_package * self.unit_size
        return self.unit_price_gross / package_base_quantity if package_base_quantity else Decimal("0")

    @property
    def calculated_line_total(self):
        return self.line_total_gross if self.line_total_gross is not None else self.quantity * self.unit_price_gross

    def best_metro_offer(self):
        if not self.matched_product_id:
            return None
        return self.matched_product.current_metro_offer()

    def comparison(self):
        offer = self.best_metro_offer()
        if not offer or self.matched_product.base_unit != self.base_unit:
            return None
        invoice_price = self.price_per_base_unit
        metro_price = offer.price_per_base_unit
        difference = invoice_price - metro_price
        percent = (difference / metro_price * 100) if metro_price else Decimal("0")
        total_impact = difference * self.total_base_quantity
        if abs(difference) < Decimal("0.005"):
            status = "EGAL"
        elif difference < 0:
            status = "MAI_IEFTIN"
        else:
            status = "MAI_SCUMP"
        return {
            "offer": offer,
            "invoice_price": invoice_price,
            "metro_price": metro_price,
            "difference": difference,
            "percent": percent,
            "total_impact": total_impact,
            "status": status,
        }

    def __str__(self):
        return self.original_name
