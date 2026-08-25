from decimal import Decimal, ROUND_CEILING

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower

from .catalog import CATEGORY_CHOICES
from .validators import validate_document_upload


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
    category = models.CharField("categorie", max_length=80, choices=CATEGORY_CHOICES, blank=True, db_index=True)
    active = models.BooleanField("activ", default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

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

    def current_metro_offer(self, base_quantity=None):
        prefetched = getattr(self, "_prefetched_objects_cache", {}).get("metro_offers")
        if prefetched is None:
            offers = list(self.metro_offers.filter(active=True))
        else:
            offers = [offer for offer in prefetched if offer.active]
        if not offers:
            return None
        preferred_store = settings.PREFERRED_METRO_STORE.strip()
        if preferred_store:
            preferred_offers = [offer for offer in offers if preferred_store.lower() in offer.source.lower()]
            if preferred_offers:
                offers = preferred_offers
        latest_date = max(offer.valid_from for offer in offers)
        return min(
            (offer for offer in offers if offer.valid_from == latest_date),
            key=lambda offer: (
                offer.price_per_base_unit_for_quantity(base_quantity)
                if base_quantity is not None
                else offer.price_per_base_unit
            ),
        )


class ProductCode(models.Model):
    class Kind(models.TextChoices):
        EAN = "EAN", "EAN / cod de bare"
        METRO = "METRO", "Cod METRO"
        SUPPLIER = "SUPPLIER", "Cod furnizor"

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="codes")
    kind = models.CharField(max_length=12, choices=Kind.choices)
    code = models.CharField(max_length=80, db_index=True)
    supplier = models.ForeignKey(
        Supplier, on_delete=models.CASCADE, null=True, blank=True, related_name="product_codes"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["kind", "code"]
        constraints = [
            models.UniqueConstraint(
                fields=["kind", "code"],
                condition=Q(supplier__isnull=True),
                name="unique_global_product_code",
            ),
            models.UniqueConstraint(
                fields=["supplier", "kind", "code"],
                condition=Q(supplier__isnull=False),
                name="unique_supplier_product_code",
            ),
        ]

    def __str__(self):
        return f"{self.get_kind_display()}: {self.code} → {self.product.name}"


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
    updated_at = models.DateTimeField(auto_now=True)

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

    def package_count_for_quantity(self, base_quantity):
        total = self.total_base_quantity
        if not total or base_quantity is None:
            return 1
        requested = max(Decimal(str(base_quantity)), Decimal("0"))
        return max(1, int((requested / total).to_integral_value(rounding=ROUND_CEILING)))

    def price_for_packages(self, package_count):
        eligible = [tier for tier in self.volume_tiers.all() if tier.min_packages <= package_count]
        return max(eligible, key=lambda tier: tier.min_packages).price_gross if eligible else self.price_gross

    def price_per_base_unit_for_quantity(self, base_quantity):
        total = self.total_base_quantity
        if not total:
            return Decimal("0")
        return self.price_for_packages(self.package_count_for_quantity(base_quantity)) / total

    def __str__(self):
        return f"{self.product.name}: {self.price_gross} lei / pachet"


class MetroOfferTier(models.Model):
    offer = models.ForeignKey(MetroOffer, on_delete=models.CASCADE, related_name="volume_tiers")
    min_packages = models.PositiveIntegerField(
        "de la pachete",
        validators=[MinValueValidator(2)],
    )
    price_gross = models.DecimalField(
        "preț pachet cu TVA",
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0"))],
    )
    label = models.CharField("text METRO", max_length=120, blank=True)

    class Meta:
        ordering = ["min_packages"]
        constraints = [
            models.UniqueConstraint(fields=["offer", "min_packages"], name="unique_metro_volume_tier")
        ]
        verbose_name = "prag de volum METRO"
        verbose_name_plural = "praguri de volum METRO"

    @property
    def price_per_base_unit(self):
        total = self.offer.total_base_quantity
        return self.price_gross / total if total else Decimal("0")

    def __str__(self):
        return f"{self.min_packages}+ pachete: {self.price_gross} lei / pachet"


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
    total_queries = models.PositiveIntegerField(default=0)
    completed_queries = models.PositiveIntegerField(default=0)
    store_name = models.CharField(max_length=120, blank=True)
    current_url = models.URLField(max_length=1000, blank=True)
    error = models.TextField(blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    lifecycle_finalized_at = models.DateTimeField(null=True, blank=True)
    new_products_count = models.PositiveIntegerField(default=0)
    reactivated_products_count = models.PositiveIntegerField(default=0)
    missing_products_count = models.PositiveIntegerField(default=0)
    unavailable_products_count = models.PositiveIntegerField(default=0)
    price_changes_count = models.PositiveIntegerField(default=0)
    package_changes_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Scanare METRO #{self.pk} · {self.get_status_display()}"


class MetroScrapeTerm(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "În așteptare"
        RUNNING = "RUNNING", "În curs"
        COMPLETED = "COMPLETED", "Finalizat"
        ERROR = "ERROR", "Eroare"

    job = models.ForeignKey(MetroScrapeJob, on_delete=models.CASCADE, related_name="terms")
    term = models.CharField(max_length=160)
    category = models.CharField(max_length=80, choices=CATEGORY_CHOICES, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    attempts = models.PositiveSmallIntegerField(default=0)
    found_count = models.PositiveIntegerField(default=0)
    error = models.TextField(blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["id"]
        constraints = [models.UniqueConstraint(fields=["job", "term"], name="unique_term_per_metro_job")]

    def __str__(self):
        return f"{self.term} · {self.get_status_display()}"


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
    category = models.CharField(max_length=80, choices=CATEGORY_CHOICES, blank=True, db_index=True)
    price_gross = models.DecimalField("preț cu TVA", max_digits=12, decimal_places=2)
    volume_prices = models.JSONField("praguri de volum", default=list, blank=True)
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


class MetroProductState(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="metro_states")
    external_id = models.CharField("cod METRO", max_length=80)
    store_name = models.CharField("magazin", max_length=120, db_index=True)
    first_seen_at = models.DateTimeField()
    last_seen_at = models.DateTimeField()
    first_seen_job = models.ForeignKey(
        MetroScrapeJob,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="first_seen_states",
    )
    last_seen_job = models.ForeignKey(
        MetroScrapeJob,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="last_seen_states",
    )
    reactivated_in_job = models.ForeignKey(
        MetroScrapeJob,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reactivated_states",
    )
    consecutive_misses = models.PositiveSmallIntegerField(default=0)
    available = models.BooleanField(default=True, db_index=True)
    last_price_gross = models.DecimalField(max_digits=12, decimal_places=2)
    last_volume_prices = models.JSONField(default=list, blank=True)
    last_units_per_package = models.DecimalField(max_digits=10, decimal_places=3, default=1)
    last_unit_size = models.DecimalField(max_digits=10, decimal_places=3, default=1)
    last_base_unit = models.CharField(max_length=3, choices=BaseUnit.choices, default=BaseUnit.PIECE)
    last_package_text = models.CharField(max_length=120, blank=True)

    class Meta:
        ordering = ["store_name", "product__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["store_name", "external_id"],
                name="unique_metro_product_state_per_store",
            )
        ]
        indexes = [models.Index(fields=["store_name", "available", "last_seen_at"])]

    def __str__(self):
        return f"{self.product.name} · {self.store_name}"


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
    document = models.FileField(
        "imagine sau PDF", upload_to="invoices/%Y/%m/", blank=True, validators=[validate_document_upload]
    )
    ocr_text = models.TextField("text extras / introdus manual", blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.NEW)
    processing_error = models.TextField(blank=True)
    notes = models.TextField("observații", blank=True)
    transport_gross = models.DecimalField(
        "transport cu TVA", max_digits=12, decimal_places=2, default=0,
        validators=[MinValueValidator(Decimal("0"))],
    )
    document_discount_gross = models.DecimalField(
        "reducere document", max_digits=12, decimal_places=2, default=0,
        validators=[MinValueValidator(Decimal("0"))],
    )
    document_total_gross = models.DecimalField(
        "total document cu TVA",
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0"))],
        help_text="Totalul final tipărit pe factură sau bon, folosit pentru verificare.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-issued_at", "-created_at"]
        verbose_name = "document de achiziție"
        verbose_name_plural = "documente de achiziție"
        constraints = [
            models.UniqueConstraint(
                Lower("number"),
                "supplier",
                "issued_at",
                condition=~Q(number=""),
                name="unique_supplier_document_number_date",
            )
        ]

    def __str__(self):
        return f"{self.supplier} · {self.number or self.issued_at}"

    @property
    def merchandise_total_gross(self):
        return sum((line.merchandise_total_gross for line in self.lines.all()), Decimal("0"))

    def allocated_transport(self, line):
        total = self.merchandise_total_gross
        if total <= 0 or self.transport_gross <= 0:
            return Decimal("0")
        return self.transport_gross * line.merchandise_total_gross / total

    def allocated_document_discount(self, line):
        total = self.merchandise_total_gross
        if total <= 0 or self.document_discount_gross <= 0:
            return Decimal("0")
        return self.document_discount_gross * line.merchandise_total_gross / total

    @property
    def deposit_total_gross(self):
        return sum((line.deposit_gross for line in self.lines.all()), Decimal("0"))

    @property
    def calculated_document_total_gross(self):
        total = (
            self.merchandise_total_gross
            + self.deposit_total_gross
            + self.transport_gross
            - self.document_discount_gross
        )
        return max(total, Decimal("0"))

    @property
    def reconciliation_difference(self):
        if self.document_total_gross is None:
            return None
        return self.calculated_document_total_gross - self.document_total_gross

    @property
    def is_reconciled(self):
        difference = self.reconciliation_difference
        return difference is not None and abs(difference) <= Decimal("0.05")


class DocumentPage(models.Model):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="pages")
    file = models.FileField("imagine/PDF", upload_to="documents/%Y/%m/", validators=[validate_document_upload])
    page_order = models.PositiveSmallIntegerField("ordine", default=1)
    ocr_text = models.TextField(blank=True)

    class Meta:
        ordering = ["page_order", "id"]
        constraints = [models.UniqueConstraint(fields=["invoice", "page_order"], name="unique_document_page_order")]

    def __str__(self):
        return f"{self.invoice} · pagina {self.page_order}"


class InvoiceRevision(models.Model):
    class Reason(models.TextChoices):
        OCR_REPROCESS = "OCR_REPROCESS", "Înainte de reprocesare OCR"
        RESTORE = "RESTORE", "Înainte de restaurarea altei versiuni"

    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="revisions")
    reason = models.CharField(max_length=20, choices=Reason.choices)
    snapshot = models.JSONField()
    line_count = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invoice_revisions",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.invoice} · {self.get_reason_display()} · {self.created_at:%d.%m.%Y %H:%M}"


class InvoiceLine(models.Model):
    class MatchMethod(models.TextChoices):
        NONE = "NONE", "Fără potrivire"
        CODE = "CODE", "Cod exact"
        ALIAS = "ALIAS", "Alias învățat"
        FUZZY = "FUZZY", "Denumire și gramaj"
        MANUAL = "MANUAL", "Confirmare manuală"

    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="lines")
    original_name = models.CharField("denumire de pe factură", max_length=240)
    ean = models.CharField("EAN / cod produs", max_length=80, blank=True, db_index=True)
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
    vat_rate = models.DecimalField(
        "TVA %",
        max_digits=5,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("100"))],
    )
    line_total_gross = models.DecimalField(
        "total linie cu TVA",
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0"))],
    )
    discount_gross = models.DecimalField(
        "reducere linie", max_digits=12, decimal_places=2, default=0,
        validators=[MinValueValidator(Decimal("0"))],
    )
    deposit_gross = models.DecimalField(
        "SGR/garanție", max_digits=12, decimal_places=2, default=0,
        validators=[MinValueValidator(Decimal("0"))],
    )
    matched_product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True, blank=True, related_name="invoice_lines")
    match_score = models.PositiveSmallIntegerField(default=0)
    match_gap = models.PositiveSmallIntegerField(default=0)
    match_method = models.CharField(max_length=10, choices=MatchMethod.choices, default=MatchMethod.NONE)
    match_candidates = models.JSONField(default=list, blank=True)
    match_corrected = models.BooleanField(default=False)
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
        return self.landed_total_gross / self.total_base_quantity if self.total_base_quantity else Decimal("0")

    @property
    def calculated_line_total(self):
        return self.line_total_gross if self.line_total_gross is not None else self.quantity * self.unit_price_gross

    @property
    def merchandise_total_gross(self):
        return max(self.calculated_line_total - self.discount_gross - self.deposit_gross, Decimal("0"))

    @property
    def landed_total_gross(self):
        return max(
            self.merchandise_total_gross
            + self.invoice.allocated_transport(self)
            - self.invoice.allocated_document_discount(self),
            Decimal("0"),
        )

    @property
    def merchandise_total_net(self):
        divisor = Decimal("1") + self.vat_rate / Decimal("100")
        return self.merchandise_total_gross / divisor if divisor else self.merchandise_total_gross

    def best_metro_offer(self):
        if not self.matched_product_id:
            return None
        return self.matched_product.current_metro_offer(self.total_base_quantity)

    def comparison(self):
        offer = self.best_metro_offer()
        if not offer or self.matched_product.base_unit != self.base_unit:
            return None
        invoice_price = self.price_per_base_unit
        metro_packages = offer.package_count_for_quantity(self.total_base_quantity)
        metro_price = offer.price_per_base_unit_for_quantity(self.total_base_quantity)
        metro_volume_applied = offer.price_for_packages(metro_packages) != offer.price_gross
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
            "metro_packages": metro_packages,
            "metro_volume_applied": metro_volume_applied,
            "difference": difference,
            "percent": percent,
            "total_impact": total_impact,
            "status": status,
        }

    @property
    def data_warnings(self):
        warnings = []
        expected_total = self.quantity * self.unit_price_gross
        if self.line_total_gross is not None and abs(self.line_total_gross - expected_total) > Decimal("0.05"):
            warnings.append(
                f"Totalul liniei ({self.line_total_gross:.2f}) diferă de cantitate × preț ({expected_total:.2f})."
            )
        if self.discount_gross + self.deposit_gross > self.calculated_line_total:
            warnings.append("Reducerea și SGR depășesc totalul brut al liniei.")
        if self.vat_rate not in {Decimal(value) for value in (0, 5, 9, 11, 19, 20, 21, 24)}:
            warnings.append(f"Cota TVA de {self.vat_rate}% este neobișnuită și trebuie verificată.")
        return warnings

    def __str__(self):
        return self.original_name


class SupplierOffer(models.Model):
    supplier = models.ForeignKey(Supplier, on_delete=models.CASCADE, related_name="offers")
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="supplier_offers")
    invoice_line = models.OneToOneField(
        InvoiceLine, on_delete=models.CASCADE, related_name="supplier_offer"
    )
    price_per_base_unit = models.DecimalField(max_digits=14, decimal_places=4)
    base_unit = models.CharField(max_length=3, choices=BaseUnit.choices)
    valid_from = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-valid_from", "price_per_base_unit"]
        indexes = [models.Index(fields=["product", "supplier", "-valid_from"])]

    def __str__(self):
        return f"{self.product} · {self.supplier}: {self.price_per_base_unit}"


class PriceAlert(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="price_alerts")
    target_price = models.DecimalField(
        "prag lei/unitate", max_digits=12, decimal_places=2,
        validators=[MinValueValidator(Decimal("0"))],
    )
    active = models.BooleanField(default=True)
    note = models.CharField(max_length=180, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_notified_at = models.DateTimeField(null=True, blank=True)
    last_notified_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    class Meta:
        ordering = ["product__name"]

    @property
    def current_offer(self):
        return self.product.current_metro_offer()

    @property
    def is_triggered(self):
        offer = self.current_offer
        return bool(offer and offer.price_per_base_unit <= self.target_price)


class PushSubscription(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="push_subscriptions")
    endpoint = models.URLField(max_length=1000, unique=True)
    p256dh = models.CharField(max_length=255)
    auth = models.CharField(max_length=255)
    user_agent = models.CharField(max_length=300, blank=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return f"{self.user} · {self.endpoint[:60]}"


class ShoppingList(models.Model):
    name = models.CharField(max_length=180)
    created_at = models.DateTimeField(auto_now_add=True)
    archived = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name


class ShoppingListItem(models.Model):
    shopping_list = models.ForeignKey(ShoppingList, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="shopping_items")
    quantity = models.DecimalField(
        "cantitate necesară", max_digits=12, decimal_places=3,
        validators=[MinValueValidator(Decimal("0.001"))],
    )
    purchased = models.BooleanField(default=False)

    class Meta:
        ordering = ["product__name"]
        constraints = [
            models.UniqueConstraint(fields=["shopping_list", "product"], name="unique_product_per_shopping_list")
        ]

    def __str__(self):
        return f"{self.product} × {self.quantity}"
