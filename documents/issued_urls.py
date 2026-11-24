from django.urls import path
from django.views.generic import RedirectView

from documents.issued_views import (
    BillingSettingsView, ContactCreateView, IssuedDetailView, IssuedFormView, IssuedListView, IssuedPDFView,
)

app_name = "sales"

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="sales:invoices"), name="index"),
    path("facturas/", IssuedListView.as_view(kind="facturas"), name="invoices"),
    path("facturas/nueva/", IssuedFormView.as_view(kind="facturas"), name="invoice_new"),
    path("presupuestos/", IssuedListView.as_view(kind="presupuestos"), name="quotes"),
    path("presupuestos/nuevo/", IssuedFormView.as_view(kind="presupuestos"), name="quote_new"),
    path("<int:pk>/", IssuedDetailView.as_view(), name="detail"),
    path("<int:pk>/editar/", IssuedFormView.as_view(), name="edit"),
    path("<int:pk>/pdf/", IssuedPDFView.as_view(), name="pdf"),
    path("contactos/nuevo/", ContactCreateView.as_view(), name="contact_create"),
    path("ajustes/", BillingSettingsView.as_view(), name="settings"),
]
