from django.contrib import admin

from .models import (
    DocumentPage,
    Invoice,
    InvoiceLine,
    MetroOffer,
    MetroScrapeJob,
    MetroScrapedProduct,
    Product,
    ProductAlias,
    Supplier,
)


class MetroOfferInline(admin.TabularInline):
    model = MetroOffer
    extra = 0


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "brand", "ean", "base_unit", "active")
    search_fields = ("name", "brand", "ean")
    list_filter = ("base_unit", "active")
    inlines = [MetroOfferInline]


class InvoiceLineInline(admin.TabularInline):
    model = InvoiceLine
    extra = 0


class DocumentPageInline(admin.TabularInline):
    model = DocumentPage
    extra = 0


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ("supplier", "number", "issued_at", "status")
    list_filter = ("status", "supplier")
    inlines = [DocumentPageInline, InvoiceLineInline]


admin.site.register(Supplier)
admin.site.register(MetroOffer)
admin.site.register(ProductAlias)
admin.site.register(MetroScrapeJob)
admin.site.register(MetroScrapedProduct)
