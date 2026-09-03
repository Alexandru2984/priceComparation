from django import forms

from .models import (
    InventoryItem,
    Invoice,
    InvoiceLine,
    MetroOffer,
    MetroOfferTier,
    PriceAlert,
    Product,
    SalesImportLine,
    ShoppingList,
    ShoppingListItem,
    StockMovement,
    Supplier,
    SupplierParsingProfile,
)
from .services.barcodes import is_valid_gtin, normalize_barcode
from .validators import (
    MAX_DOCUMENT_PAGES,
    MAX_DOCUMENT_TOTAL_SIZE,
    validate_csv_upload,
    validate_document_upload,
    validate_price_list_upload,
)
from .widgets import ProductAutocompleteWidget, set_product_widget_label


class DateInput(forms.DateInput):
    input_type = "date"


class DataExportForm(forms.Form):
    start_date = forms.DateField(label="De la data", required=False, widget=DateInput())
    end_date = forms.DateField(label="Până la data", required=False, widget=DateInput())
    include_inactive = forms.BooleanField(
        label="Include produse/oferte inactive și liste arhivate",
        required=False,
    )

    def clean(self):
        cleaned = super().clean()
        start_date = cleaned.get("start_date")
        end_date = cleaned.get("end_date")
        if start_date and end_date and start_date > end_date:
            raise forms.ValidationError("Data de început nu poate fi după data de sfârșit.")
        return cleaned


class SupplierForm(forms.ModelForm):
    class Meta:
        model = Supplier
        fields = [
            "name", "tax_id", "is_metro", "minimum_order_gross", "transport_gross",
            "free_transport_from", "notes",
        ]


class SupplierParsingProfileForm(forms.ModelForm):
    class Meta:
        model = SupplierParsingProfile
        fields = ["parser_mode", "apply_default_vat", "default_vat_rate"]


class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = ["name", "brand", "ean", "category", "base_unit", "active"]

    def clean_ean(self):
        value = normalize_barcode(self.cleaned_data.get("ean"))
        if value and not is_valid_gtin(value):
            raise forms.ValidationError("EAN/GTIN invalid. Verifică cifrele codului de bare.")
        duplicate = Product.objects.filter(ean=value).exclude(pk=self.instance.pk).first() if value else None
        if duplicate:
            raise forms.ValidationError(f"Codul există deja la produsul «{duplicate.name}».")
        return value


class MetroOfferForm(forms.ModelForm):
    product = forms.ModelChoiceField(
        label="Produs", queryset=Product.objects.filter(active=True), widget=ProductAutocompleteWidget()
    )
    volume_min_packages = forms.IntegerField(
        label="Preț de volum: de la câte pachete",
        min_value=2,
        required=False,
    )
    volume_price_gross = forms.DecimalField(
        label="Preț de volum/pachet cu TVA",
        min_value=0,
        max_digits=12,
        decimal_places=2,
        required=False,
    )

    class Meta:
        model = MetroOffer
        fields = ["product", "units_per_package", "unit_size", "price_gross", "valid_from", "source", "active"]
        widgets = {"valid_from": DateInput()}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        set_product_widget_label(self.fields["product"], getattr(self.instance, "product", None))

    def clean(self):
        cleaned = super().clean()
        threshold = cleaned.get("volume_min_packages")
        price = cleaned.get("volume_price_gross")
        if (threshold is None) != (price is None):
            raise forms.ValidationError("Completează atât pragul de pachete, cât și prețul de volum.")
        return cleaned

    def save(self, commit=True):
        offer = super().save(commit=commit)
        if commit and self.cleaned_data.get("volume_min_packages") is not None:
            MetroOfferTier.objects.update_or_create(
                offer=offer,
                min_packages=self.cleaned_data["volume_min_packages"],
                defaults={"price_gross": self.cleaned_data["volume_price_gross"]},
            )
        return offer


class MetroImportForm(forms.Form):
    file = forms.FileField(label="Fișier CSV", validators=[validate_csv_upload])


class SupplierPriceListUploadForm(forms.Form):
    supplier = forms.ModelChoiceField(label="Furnizor", queryset=Supplier.objects.all())
    effective_at = forms.DateField(label="Prețuri valabile la", widget=DateInput())
    file = forms.FileField(
        label="Listă CSV/XLSX",
        validators=[validate_price_list_upload],
        widget=forms.ClearableFileInput(attrs={"accept": ".csv,.xlsx"}),
        help_text="Coloane minime: produs/denumire și preț. Opțional: EAN, gramaj, unitate, bucăți/bax.",
    )


class SalesImportUploadForm(forms.Form):
    default_date = forms.DateField(
        label="Data implicită a vânzărilor",
        widget=DateInput(),
        help_text="Folosită numai dacă fișierul nu are o coloană de dată.",
    )
    file = forms.FileField(
        label="Export POS CSV/XLSX",
        validators=[validate_price_list_upload],
        widget=forms.ClearableFileInput(attrs={"accept": ".csv,.xlsx"}),
        help_text="Coloane minime: cantitate și EAN sau denumire. Opțional: dată și număr bon.",
    )


class InitialDataImportForm(forms.Form):
    file = forms.FileField(
        label="Registru inițial XLSX",
        validators=[validate_price_list_upload],
        widget=forms.ClearableFileInput(attrs={"accept": ".xlsx"}),
        help_text="Folosește șablonul cu foile Furnizori, Produse și Stoc.",
    )

    def clean_file(self):
        upload = self.cleaned_data["file"]
        if not upload.name.lower().endswith(".xlsx"):
            raise forms.ValidationError("Importul inițial acceptă numai registrul XLSX.")
        return upload


class SalesImportLineForm(forms.ModelForm):
    product = forms.ModelChoiceField(
        label="Produs din catalog",
        queryset=Product.objects.filter(active=True),
        required=False,
        widget=ProductAutocompleteWidget(),
    )

    class Meta:
        model = SalesImportLine
        fields = ["product", "quantity", "sold_at", "ignored"]
        widgets = {"sold_at": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M")}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["sold_at"].input_formats = ["%Y-%m-%dT%H:%M"]
        set_product_widget_label(self.fields["product"], getattr(self.instance, "product", None))


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    def clean(self, data, initial=None):
        clean_one = super().clean
        if isinstance(data, (list, tuple)):
            if len(data) > MAX_DOCUMENT_PAGES:
                raise forms.ValidationError(
                    f"Poți încărca maximum {MAX_DOCUMENT_PAGES} imagini/PDF-uri pentru un document."
                )
            cleaned = [clean_one(item, initial) for item in data]
        else:
            cleaned = [clean_one(data, initial)] if data else []
        if sum(item.size for item in cleaned) > MAX_DOCUMENT_TOTAL_SIZE:
            raise forms.ValidationError("Documentul poate avea maximum 50 MB în total.")
        return cleaned


DOCUMENT_UPLOAD_ACCEPT = (
    ".jpg,.jpeg,.png,.webp,.tif,.tiff,.pdf,"
    "image/jpeg,image/png,image/webp,image/tiff,application/pdf"
)


def _combine_document_uploads(form, *, required=False):
    camera_uploads = form.cleaned_data.get("camera_documents") or []
    selected_uploads = form.cleaned_data.get("documents") or []
    uploads = [*camera_uploads, *selected_uploads]
    if required and not uploads:
        form.add_error("documents", "Fotografiază documentul sau alege cel puțin un fișier.")
    if len(uploads) > MAX_DOCUMENT_PAGES:
        form.add_error(
            "documents",
            f"Poți încărca maximum {MAX_DOCUMENT_PAGES} imagini/PDF-uri pentru un document.",
        )
    if sum(upload.size for upload in uploads) > MAX_DOCUMENT_TOTAL_SIZE:
        form.add_error("documents", "Documentul poate avea maximum 50 MB în total.")
    return uploads


class InvoiceIdentityValidationMixin:
    def clean_number(self):
        return (self.cleaned_data.get("number") or "").strip()

    def clean(self):
        cleaned = super().clean()
        supplier = cleaned.get("supplier")
        number = cleaned.get("number")
        issued_at = cleaned.get("issued_at")
        if supplier and number and issued_at:
            duplicate = Invoice.objects.filter(
                supplier=supplier,
                number__iexact=number,
                issued_at=issued_at,
            ).exclude(pk=self.instance.pk).first()
            if duplicate:
                self.add_error(
                    "number",
                    f"Documentul există deja (#{duplicate.pk}). Deschide înregistrarea existentă.",
                )
        return cleaned


class InvoiceForm(InvoiceIdentityValidationMixin, forms.ModelForm):
    transport_gross = forms.DecimalField(label="Transport cu TVA", required=False, initial=0, min_value=0)
    document_discount_gross = forms.DecimalField(
        label="Reducere document", required=False, initial=0, min_value=0
    )
    camera_documents = MultipleFileField(
        label="Fotografii realizate acum",
        required=False,
        validators=[validate_document_upload],
        widget=MultipleFileInput(
            attrs={
                "accept": "image/jpeg,image/png,image/webp",
                "capture": "environment",
                "class": "upload-native-input",
                "data-camera-input": "true",
                "multiple": False,
            }
        ),
    )
    documents = MultipleFileField(
        label="Fotografii sau PDF-uri",
        required=False,
        validators=[validate_document_upload],
        widget=MultipleFileInput(
            attrs={
                "accept": DOCUMENT_UPLOAD_ACCEPT,
                "class": "upload-native-input",
                "data-gallery-input": "true",
            }
        ),
        help_text="Pentru un bon lung poți selecta mai multe fotografii, în ordinea de sus în jos.",
    )
    process_now = forms.BooleanField(label="Procesează automat după salvare", required=False, initial=True)

    class Meta:
        model = Invoice
        fields = [
            "document_type", "supplier", "number", "issued_at", "transport_gross",
            "document_discount_gross", "document_total_gross", "receive_into_stock", "ocr_text", "notes",
        ]
        widgets = {
            "issued_at": DateInput(),
            "ocr_text": forms.Textarea(attrs={"rows": 7, "placeholder": "Poți lipi aici textul OCR sau liniile facturii..."}),
        }

    def clean_transport_gross(self):
        return self.cleaned_data.get("transport_gross") or 0

    def clean_document_discount_gross(self):
        return self.cleaned_data.get("document_discount_gross") or 0

    def clean(self):
        cleaned = super().clean()
        cleaned["uploads"] = _combine_document_uploads(self)
        return cleaned


class DocumentPagesForm(forms.Form):
    camera_documents = MultipleFileField(
        label="Fotografii realizate acum",
        required=False,
        validators=[validate_document_upload],
        widget=MultipleFileInput(
            attrs={
                "accept": "image/jpeg,image/png,image/webp",
                "capture": "environment",
                "class": "upload-native-input",
                "data-camera-input": "true",
                "multiple": False,
            }
        ),
    )
    documents = MultipleFileField(
        label="Adaugă fotografii sau PDF-uri",
        required=False,
        validators=[validate_document_upload],
        widget=MultipleFileInput(
            attrs={
                "accept": DOCUMENT_UPLOAD_ACCEPT,
                "class": "upload-native-input",
                "data-gallery-input": "true",
            }
        ),
        help_text="Fișierele sunt adăugate la final; apoi le poți muta în ordinea corectă.",
    )

    def clean(self):
        cleaned = super().clean()
        cleaned["uploads"] = _combine_document_uploads(self, required=True)
        return cleaned


class InvoiceEditForm(InvoiceIdentityValidationMixin, forms.ModelForm):
    class Meta:
        model = Invoice
        fields = [
            "document_type",
            "supplier",
            "number",
            "issued_at",
            "transport_gross",
            "document_discount_gross",
            "document_total_gross",
            "receive_into_stock",
            "notes",
        ]
        widgets = {"issued_at": DateInput()}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk and self.instance.lines.exists():
            for field_name in ("document_type", "supplier", "number", "issued_at"):
                self.fields[field_name].disabled = True
                self.fields[field_name].help_text = (
                    "Câmp blocat după adăugarea liniilor, pentru a proteja istoricul de preț."
                )


class InvoiceLineForm(forms.ModelForm):
    matched_product = forms.ModelChoiceField(
        label="Produs asociat",
        queryset=Product.objects.filter(active=True),
        required=False,
        widget=ProductAutocompleteWidget(),
    )
    discount_gross = forms.DecimalField(label="Reducere linie", required=False, initial=0, min_value=0)
    deposit_gross = forms.DecimalField(label="SGR/garanție", required=False, initial=0, min_value=0)
    class Meta:
        model = InvoiceLine
        fields = [
            "original_name", "ean", "quantity", "units_per_package", "unit_size", "base_unit",
            "unit_price_gross", "vat_rate", "line_total_gross", "discount_gross", "deposit_gross",
            "matched_product", "needs_review",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        set_product_widget_label(
            self.fields["matched_product"], getattr(self.instance, "matched_product", None)
        )
        self.fields["matched_product"].help_text = "Pentru un document METRO poți lăsa gol și debifa «necesită verificare»; produsul va fi creat automat."
        self.fields["needs_review"].help_text = "Debifează numai după ce ai verificat cantitatea, ambalarea și prețul."

    def clean_discount_gross(self):
        return self.cleaned_data.get("discount_gross") or 0

    def clean_deposit_gross(self):
        return self.cleaned_data.get("deposit_gross") or 0


InvoiceLineFormSet = forms.modelformset_factory(InvoiceLine, form=InvoiceLineForm, extra=0, can_delete=True)


class PriceAlertForm(forms.ModelForm):
    product = forms.ModelChoiceField(
        label="Produs", queryset=Product.objects.filter(active=True), widget=ProductAutocompleteWidget()
    )

    class Meta:
        model = PriceAlert
        fields = ["product", "target_price", "note", "active"]


class ShoppingListForm(forms.ModelForm):
    class Meta:
        model = ShoppingList
        fields = ["name", "budget_gross"]


class ShoppingListItemForm(forms.ModelForm):
    product = forms.ModelChoiceField(
        label="Produs", queryset=Product.objects.filter(active=True), widget=ProductAutocompleteWidget()
    )

    class Meta:
        model = ShoppingListItem
        fields = ["product", "quantity", "priority", "purchased"]


class InventoryItemForm(forms.ModelForm):
    product = forms.ModelChoiceField(
        label="Produs", queryset=Product.objects.filter(active=True), widget=ProductAutocompleteWidget()
    )

    class Meta:
        model = InventoryItem
        fields = [
            "product",
            "minimum_quantity",
            "target_quantity",
            "shelf_life_days",
            "retail_price_gross",
            "retail_unit_size",
            "retail_vat_rate",
            "purchase_vat_rate",
            "target_margin_percent",
            "expected_waste_percent",
            "active",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        set_product_widget_label(self.fields["product"], getattr(self.instance, "product", None))


class StockMovementForm(forms.ModelForm):
    class Meta:
        model = StockMovement
        fields = ["quantity_delta", "reason", "note"]

    def clean_quantity_delta(self):
        value = self.cleaned_data["quantity_delta"]
        if value == 0:
            raise forms.ValidationError("Modificarea nu poate fi zero.")
        return value
