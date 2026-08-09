from django import forms

from .models import (
    Invoice,
    InvoiceLine,
    MetroOffer,
    PriceAlert,
    Product,
    ShoppingList,
    ShoppingListItem,
    Supplier,
)
from .validators import MAX_DOCUMENT_TOTAL_SIZE, validate_csv_upload, validate_document_upload
from .services.barcodes import is_valid_gtin, normalize_barcode
from .widgets import ProductAutocompleteWidget, set_product_widget_label


class DateInput(forms.DateInput):
    input_type = "date"


class SupplierForm(forms.ModelForm):
    class Meta:
        model = Supplier
        fields = ["name", "tax_id", "is_metro", "notes"]


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

    class Meta:
        model = MetroOffer
        fields = ["product", "units_per_package", "unit_size", "price_gross", "valid_from", "source", "active"]
        widgets = {"valid_from": DateInput()}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        set_product_widget_label(self.fields["product"], getattr(self.instance, "product", None))


class MetroImportForm(forms.Form):
    file = forms.FileField(label="Fișier CSV", validators=[validate_csv_upload])


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    def clean(self, data, initial=None):
        clean_one = super().clean
        if isinstance(data, (list, tuple)):
            if len(data) > 12:
                raise forms.ValidationError("Poți încărca maximum 12 imagini/PDF-uri pentru un document.")
            cleaned = [clean_one(item, initial) for item in data]
        else:
            cleaned = [clean_one(data, initial)] if data else []
        if sum(item.size for item in cleaned) > MAX_DOCUMENT_TOTAL_SIZE:
            raise forms.ValidationError("Documentul poate avea maximum 50 MB în total.")
        return cleaned


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
    documents = MultipleFileField(
        label="Fotografii sau PDF-uri",
        required=False,
        validators=[validate_document_upload],
        widget=MultipleFileInput(attrs={"accept": "image/*,.pdf"}),
        help_text="Pentru un bon lung poți selecta mai multe fotografii, în ordinea de sus în jos.",
    )
    process_now = forms.BooleanField(label="Procesează automat după salvare", required=False, initial=True)

    class Meta:
        model = Invoice
        fields = [
            "document_type", "supplier", "number", "issued_at", "transport_gross",
            "document_discount_gross", "document_total_gross", "ocr_text", "notes",
        ]
        widgets = {
            "issued_at": DateInput(),
            "ocr_text": forms.Textarea(attrs={"rows": 7, "placeholder": "Poți lipi aici textul OCR sau liniile facturii..."}),
        }

    def clean_transport_gross(self):
        return self.cleaned_data.get("transport_gross") or 0

    def clean_document_discount_gross(self):
        return self.cleaned_data.get("document_discount_gross") or 0


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
        fields = ["name"]


class ShoppingListItemForm(forms.ModelForm):
    product = forms.ModelChoiceField(
        label="Produs", queryset=Product.objects.filter(active=True), widget=ProductAutocompleteWidget()
    )

    class Meta:
        model = ShoppingListItem
        fields = ["product", "quantity", "purchased"]
