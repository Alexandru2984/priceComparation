from django import forms
from django.urls import reverse

from .models import Product


class ProductAutocompleteWidget(forms.HiddenInput):
    template_name = "comparator/widgets/product_autocomplete.html"

    def __init__(self, attrs=None, initial_label=""):
        super().__init__(attrs)
        self.initial_label = initial_label

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        label = self.initial_label
        if value and not label:
            product = Product.objects.filter(pk=value).only("name", "brand", "ean").first()
            if product:
                label = product.name
                if product.brand:
                    label += f" · {product.brand}"
                if product.ean:
                    label += f" · {product.ean}"
        context["widget"]["label"] = label
        context["widget"]["search_url"] = reverse("comparator:product_search")
        context["widget"]["search_id"] = f"{context['widget']['attrs']['id']}_search"
        return context


def set_product_widget_label(field, product):
    if product:
        label = product.name
        if product.brand:
            label += f" · {product.brand}"
        if product.ean:
            label += f" · {product.ean}"
        field.widget.initial_label = label
