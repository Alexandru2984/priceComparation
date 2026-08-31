from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.admin.sites import NotRegistered

from .models import (
    ActivityLog,
    AutomationRun,
    DocumentPage,
    DocumentProcessingJob,
    Invoice,
    InvoiceLine,
    InvoiceRevision,
    InventoryItem,
    MetroOffer,
    MetroOfferTier,
    MetroPriceAnomaly,
    MetroProductState,
    MetroScrapeJob,
    MetroScrapedProduct,
    MetroScrapeTerm,
    PriceAlert,
    Product,
    ProductAlias,
    ProductCode,
    PushSubscription,
    SalesImport,
    SalesImportLine,
    ShoppingList,
    ShoppingListItem,
    StockMovement,
    Supplier,
    SupplierOffer,
    SupplierParsingProfile,
    SupplierPriceImport,
)


admin.site.site_header = "PriceMatch · administrare securizată"
admin.site.site_title = "PriceMatch Admin"
admin.site.index_title = "Datele private ale magazinului"

# Conturile sunt administrate exclusiv prin comenzile locale PriceMatch. Astfel,
# compromiterea unei sesiuni web nu poate fi folosită pentru a crea alt cont.
for account_model in (get_user_model(), Group):
    try:
        admin.site.unregister(account_model)
    except NotRegistered:
        pass

admin.site.register(SupplierParsingProfile)
admin.site.register(SupplierPriceImport)
admin.site.register(SalesImport)
admin.site.register(SalesImportLine)


class MetroOfferInline(admin.TabularInline):
    model = MetroOffer
    extra = 0


class MetroOfferTierInline(admin.TabularInline):
    model = MetroOfferTier
    extra = 0


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "brand", "category", "ean", "base_unit", "active")
    search_fields = ("name", "brand", "ean")
    list_filter = ("category", "base_unit", "active")
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


@admin.register(InvoiceRevision)
class InvoiceRevisionAdmin(admin.ModelAdmin):
    list_display = ("invoice", "reason", "line_count", "created_by", "created_at")
    list_filter = ("reason", "created_at")
    readonly_fields = ("invoice", "reason", "snapshot", "line_count", "created_by", "created_at")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


admin.site.register(Supplier)
@admin.register(MetroOffer)
class MetroOfferAdmin(admin.ModelAdmin):
    list_display = ("product", "price_gross", "valid_from", "source", "active")
    inlines = [MetroOfferTierInline]
admin.site.register(MetroProductState)
admin.site.register(ProductAlias)
admin.site.register(MetroScrapeJob)
admin.site.register(MetroScrapedProduct)
admin.site.register(MetroScrapeTerm)
admin.site.register(ProductCode)
admin.site.register(SupplierOffer)
admin.site.register(PriceAlert)
admin.site.register(ShoppingList)
admin.site.register(ShoppingListItem)
admin.site.register(InventoryItem)
admin.site.register(StockMovement)
admin.site.register(PushSubscription)
admin.site.register(DocumentProcessingJob)
admin.site.register(MetroPriceAnomaly)
admin.site.register(AutomationRun)


@admin.register(ActivityLog)
class ActivityLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "user", "method", "view_name", "status_code", "outcome", "ip_address")
    list_filter = ("outcome", "method", "created_at")
    search_fields = ("user__username", "path", "view_name", "ip_address")
    readonly_fields = ("user", "method", "path", "view_name", "status_code", "outcome", "ip_address", "created_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
