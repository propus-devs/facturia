"""Ventas: crear, emitir, ver y anular facturas, tickets y presupuestos (vistas finas)."""
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.clickjacking import xframe_options_sameorigin

from billing_ai.storage import private_file_response
from clients.forms import BillingSettingsForm
from documents.forms.issued import (
    ContactQuickForm, IssuedDocumentForm, IssuedLineFormSet, lines_from_formset,
)
from documents.models import Document
from documents.selectors.document_selector import DocumentSelector
from documents.services import issuing, public_quotes, sending
from documents.services.companies import get_or_create_company
from documents.services.pdf_issued import render_issued_pdf

KINDS = {
    "facturas": {
        "title": "Facturas", "new_label": "Nueva factura", "list_url": "sales:invoices", "new_url": "sales:invoice_new",
        "types": ("invoice", "corrected_invoice", "receipt"), "creatable": ("invoice", "receipt"),
    },
    "presupuestos": {
        "title": "Presupuestos", "new_label": "Nuevo presupuesto", "list_url": "sales:quotes", "new_url": "sales:quote_new",
        "types": ("quote", "proforma"), "creatable": ("quote", "proforma"),
    },
}
STATUS_TABS = [("", "Todas"), ("draft", "Borradores"), ("approved", "Emitidas"), ("voided", "Anuladas")]


def kind_of(document_type):
    return "presupuestos" if document_type in KINDS["presupuestos"]["types"] else "facturas"


def _whole(quantity):
    """2.0000 → 2 para el formulario; una cantidad con decimales (datos antiguos) se deja tal cual
    para que la validación pida corregirla."""
    return int(quantity) if quantity == quantity.to_integral_value() else quantity


def _issued_or_404(request, pk):
    return get_object_or_404(
        DocumentSelector.issued(request.user.client).select_related("company", "issued"), pk=pk
    )


class IssuedListView(LoginRequiredMixin, View):
    template_name = "private/sales/issued_list.html"
    kind = "facturas"

    def get(self, request):
        cfg = KINDS[self.kind]
        qs = DocumentSelector.issued(request.user.client).filter(document_type__in=cfg["types"])
        q = (request.GET.get("q") or "").strip()
        status = request.GET.get("status") or ""
        if q:
            qs = qs.filter(Q(document_number__icontains=q) | Q(company__name__icontains=q))
        if status in ("draft", "approved", "voided"):
            qs = qs.filter(status=status)
        qs = qs.select_related("company", "issued").order_by("-issue_date", "-pk")
        page = Paginator(qs, 20).get_page(request.GET.get("page"))
        return render(request, self.template_name, {
            "kind": self.kind, "cfg": cfg, "page_obj": page, "documents": page.object_list,
            "q": q, "status": status, "status_tabs": STATUS_TABS,
        })


class IssuedFormView(LoginRequiredMixin, View):
    """Crear (`kind` en la URL) o editar un borrador (`pk`). Mismo formulario para todos los tipos."""
    template_name = "private/sales/issued_form.html"
    kind = "facturas"

    def dispatch(self, request, *args, **kwargs):
        self.document = None
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if kwargs.get("pk"):
            self.document = _issued_or_404(request, kwargs["pk"])
            if self.document.status != "draft":
                messages.warning(request, "Un documento emitido no se puede editar: anúlalo o emite una rectificativa.")
                return redirect("sales:detail", pk=self.document.pk)
            self.kind = kind_of(self.document.document_type)
        return super().dispatch(request, *args, **kwargs)

    def _forms(self, request, data=None):
        client = request.user.client
        doc = self.document
        # Un borrador solo conserva su tipo (también una rectificativa, que no se crea desde aquí).
        types = (doc.document_type,) if doc is not None else KINDS[self.kind]["creatable"]
        if data is None and doc is not None:
            iss = doc.issued
            initial = {
                "document_type": doc.document_type, "company": doc.company, "issue_date": doc.issue_date,
                "due_date": iss.due_date, "valid_until": iss.valid_until, "payment_method": iss.payment_method,
                "global_discount_pct": iss.global_discount_pct, "withholding_pct": iss.withholding_pct,
                "apply_surcharge": iss.apply_surcharge, "category": doc.category, "tags": doc.tags.all(),
                "notes": iss.notes,
            }
            lines = [{"description": l.description, "quantity": _whole(l.quantity), "unit_price": l.unit_price,
                      "tax_rate": l.tax_rate, "discount_pct": l.discount_pct} for l in doc.lines.all()]
            form = IssuedDocumentForm(initial=initial, client=client, types=types)
            formset = IssuedLineFormSet(initial=lines, prefix="lines")
            return form, formset
        if data is None:
            from django.utils import timezone
            form = IssuedDocumentForm(initial={"issue_date": timezone.localdate()}, client=client, types=types)
            return form, IssuedLineFormSet(prefix="lines")
        form = IssuedDocumentForm(data, client=client, types=types)
        return form, IssuedLineFormSet(data, prefix="lines")

    def _render(self, request, form, formset):
        cfg = KINDS[self.kind]
        return render(request, self.template_name, {
            "kind": self.kind, "cfg": cfg, "form": form, "formset": formset, "document": self.document,
            "is_quote_kind": self.kind == "presupuestos",
            "rectifies": self.document.issued.rectifies if self.document else None,
        })

    def get(self, request, **kwargs):
        return self._render(request, *self._forms(request))

    def post(self, request, **kwargs):
        form, formset = self._forms(request, request.POST)
        if not (form.is_valid() and formset.is_valid()):
            return self._render(request, form, formset)
        user, client = request.user, request.user.client
        lines = lines_from_formset(formset)
        try:
            with transaction.atomic():
                opts = form.service_kwargs()
                if self.document is None:
                    doc = issuing.create_draft(
                        client=client, user=user, document_type=form.cleaned_data["document_type"],
                        lines=lines, **opts,
                    )
                else:
                    doc = issuing.update_draft(self.document, user, lines=lines, **opts)
        except ValidationError as exc:
            form.add_error(None, exc)
            return self._render(request, form, formset)

        if request.POST.get("action") == "approve":
            try:
                doc = issuing.approve(doc, user)
            except ValidationError as exc:
                messages.error(request, " ".join(exc.messages))
                return redirect("sales:edit", pk=doc.pk)
            messages.success(request, f"{doc.get_document_type_display()} {doc.document_number} emitido.")
        else:
            messages.success(request, "Borrador guardado.")
        return redirect("sales:detail", pk=doc.pk)


class IssuedDetailView(LoginRequiredMixin, View):
    template_name = "private/sales/issued_detail.html"

    def get(self, request, pk):
        doc = _issued_or_404(request, pk)
        return render(request, self.template_name, {
            "document": doc, "issued": doc.issued, "kind": kind_of(doc.document_type),
            "cfg": KINDS[kind_of(doc.document_type)],
            "lines": doc.lines.all(), "tax_lines": doc.tax_lines.all(),
            "audit_logs": doc.audit_logs.select_related("user"),
            "can_edit": doc.status == "draft", "can_approve": doc.status == "draft",
            "can_void": doc.status == "approved",
            "can_send": doc.status == "approved",
            "can_rectify": doc.status == "approved" and doc.document_type in issuing.RECTIFIABLE_TYPES,
            "rectifies": doc.issued.rectifies,
            "rectifications": DocumentSelector.issued(doc.client).filter(issued__rectifies=doc).order_by("-pk"),
            "is_quote": doc.document_type in ("quote", "proforma"),
            "quote_open": doc.document_type in ("quote", "proforma") and doc.status == "approved"
                          and doc.issued.quote_status not in ("declined", "converted"),
            "public_url": public_quotes.public_url(doc) if doc.status == "approved"
                          and doc.issued.quote_status not in ("", "draft") else "",
            "email_defaults": sending.default_message(doc) if doc.status == "approved" else None,
            "sends": doc.sends.select_related("user")[:10],
        })

    def post(self, request, pk):
        doc = _issued_or_404(request, pk)
        action = request.POST.get("action")
        try:
            if action == "approve":
                doc = issuing.approve(doc, request.user)
                messages.success(request, f"Emitido con el número {doc.document_number}.")
            elif action == "send":
                sending.queue_email(
                    doc, request.user, to_email=request.POST.get("to_email"),
                    subject=request.POST.get("subject"), body=request.POST.get("body"),
                )
                messages.success(request, "Envío en cola: se enviará en unos instantes.")
            elif action == "share":
                sending.share_link(doc, request.user)
                messages.success(request, "Enlace activado: cópialo desde la tarjeta «Enlace para el cliente».")
            elif action == "rectify":
                rect = issuing.issue_rectification(doc, request.user, request.POST.get("reason", ""))
                messages.success(request, "Rectificativa creada en borrador: ajusta las líneas si solo corriges una parte y emítela.")
                return redirect("sales:edit", pk=rect.pk)
            elif action == "convert":
                invoice = issuing.convert_quote_to_invoice(doc, request.user)
                messages.success(request, "Factura creada en borrador a partir del presupuesto. Revísala y emítela.")
                return redirect("sales:edit", pk=invoice.pk)
            elif action == "void":
                issuing.void(doc, request.user, request.POST.get("reason", ""))
                messages.success(request, "Documento anulado.")
            else:
                messages.error(request, "Acción no válida.")
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        return redirect("sales:detail", pk=pk)


@method_decorator(xframe_options_sameorigin, name="dispatch")
class IssuedPDFView(LoginRequiredMixin, View):
    """PDF del documento: el archivo guardado si está emitido; si es borrador, se genera al vuelo."""

    def get(self, request, pk):
        doc = _issued_or_404(request, pk)
        if doc.file:
            response = private_file_response(doc.file, download_name=doc.original_name)
        elif doc.status == "draft":
            response = HttpResponse(render_issued_pdf(doc), content_type="application/pdf")
            response["Content-Disposition"] = f'inline; filename="borrador-{doc.pk}.pdf"'
            response["Cache-Control"] = "private, no-store"
        else:
            raise Http404("Archivo no encontrado")
        if request.GET.get("download"):
            response["Content-Disposition"] = response["Content-Disposition"].replace("inline", "attachment", 1)
        return response


class ContactCreateView(LoginRequiredMixin, View):
    """Alta rápida de contacto desde el formulario («+ Añadir»). Devuelve JSON."""

    def post(self, request):
        form = ContactQuickForm(request.POST)
        if not form.is_valid():
            return JsonResponse({"errors": form.errors.get_json_data()}, status=400)
        d = form.cleaned_data
        company = get_or_create_company(
            client=request.user.client, name=d["name"], tax_id=d["tax_id"], is_customer=True
        )
        if d["email"] and not company.email:
            company.email = d["email"]
            company.save(update_fields=["email"])
        return JsonResponse({"id": company.pk, "name": company.name, "tax_id": company.tax_id or ""})


class BillingSettingsView(LoginRequiredMixin, View):
    template_name = "private/sales/billing_settings.html"

    def _form(self, request, data=None):
        settings_obj = issuing.billing_settings_for(request.user.client)
        return BillingSettingsForm(data, instance=settings_obj)

    def get(self, request):
        return render(request, self.template_name, {"form": self._form(request)})

    def post(self, request):
        form = self._form(request, request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Ajustes de facturación guardados.")
            return redirect("sales:settings")
        return render(request, self.template_name, {"form": form})
