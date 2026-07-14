from django import forms

from .models import Invoice, InvoiceLine, MetroOffer, Product, Supplier


class DateInput(forms.DateInput):
    input_type = "date"


class SupplierForm(forms.ModelForm):
    class Meta:
        model = Supplier
        fields = ["name", "tax_id", "is_metro", "notes"]


class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = ["name", "brand", "ean", "base_unit", "active"]


class MetroOfferForm(forms.ModelForm):
    class Meta:
        model = MetroOffer
        fields = ["product", "units_per_package", "unit_size", "price_gross", "valid_from", "source", "active"]
        widgets = {"valid_from": DateInput()}


class MetroImportForm(forms.Form):
    file = forms.FileField(label="Fișier CSV")


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    def clean(self, data, initial=None):
        clean_one = super().clean
        if isinstance(data, (list, tuple)):
            if len(data) > 12:
                raise forms.ValidationError("Poți încărca maximum 12 imagini/PDF-uri pentru un document.")
            return [clean_one(item, initial) for item in data]
        return [clean_one(data, initial)] if data else []


class InvoiceForm(forms.ModelForm):
    documents = MultipleFileField(
        label="Fotografii sau PDF-uri",
        required=False,
        widget=MultipleFileInput(attrs={"accept": "image/*,.pdf"}),
        help_text="Pentru un bon lung poți selecta mai multe fotografii, în ordinea de sus în jos.",
    )
    process_now = forms.BooleanField(label="Procesează automat după salvare", required=False, initial=True)

    class Meta:
        model = Invoice
        fields = ["document_type", "supplier", "number", "issued_at", "ocr_text", "notes"]
        widgets = {
            "issued_at": DateInput(),
            "ocr_text": forms.Textarea(attrs={"rows": 7, "placeholder": "Poți lipi aici textul OCR sau liniile facturii..."}),
        }


class InvoiceLineForm(forms.ModelForm):
    class Meta:
        model = InvoiceLine
        fields = [
            "original_name", "quantity", "units_per_package", "unit_size", "base_unit",
            "unit_price_gross", "vat_rate", "line_total_gross", "matched_product", "needs_review",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["matched_product"].help_text = "Pentru un document METRO poți lăsa gol și debifa «necesită verificare»; produsul va fi creat automat."
        self.fields["needs_review"].help_text = "Debifează numai după ce ai verificat cantitatea, ambalarea și prețul."
