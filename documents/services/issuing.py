"""Emisión de facturas, tickets y presupuestos desde la app.

Ciclo: `create_draft` → `update_draft`* → `approve` (número, PDF, cierre) → `void` (anular).
Un documento aprobado no se edita (ver `Document._check_issued_lock`); se anula o se rectifica.
Las vistas solo llaman aquí: la lógica no vive en la vista ni en el template.
"""
import hashlib
import secrets
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from clients.models import BillingSettings
from documents.models import (
    Document, DocumentAuditLog, DocumentFile, InvoiceLine, IssuedDocument, TaxBreakdown,
)
from documents.services.issued_totals import compute_totals
from documents.services.numbering import KIND_BY_TYPE, next_number

ISSUABLE_TYPES = tuple(KIND_BY_TYPE)            # invoice, corrected_invoice, receipt, quote, proforma
NUMBERED_ON_CREATE = ("quote", "proforma")      # el resto se numera al aprobar (sin huecos)
OPTION_FIELDS = (
    "due_date", "valid_until", "payment_method", "notes",
    "global_discount_pct", "withholding_pct", "apply_surcharge",
)


def billing_settings_for(client) -> BillingSettings:
    return BillingSettings.objects.get_or_create(client=client)[0]


def build_snapshots(client, company) -> tuple[dict, dict]:
    """Datos fiscales del emisor y del receptor tal como saldrán en el documento."""
    bs = billing_settings_for(client)
    issuer = {
        "name": client.name, "tax_id": client.tax_id or "", "address": bs.address,
        "postal_code": bs.postal_code, "city": bs.city or client.city, "email": bs.email,
        "phone": client.phone, "iban": bs.iban,
    }
    recipient = {}
    if company is not None:
        recipient = {
            "name": company.name, "tax_id": company.tax_id or "", "address": company.address,
            "postal_code": company.postal_code, "city": company.city, "country": company.country,
            "email": company.email,
        }
    return issuer, recipient


def _require_draft(document):
    if not document.is_issued or document.status != "draft":
        raise ValidationError("Solo se pueden editar borradores emitidos en la app.")


def _check_company(client, company):
    if company is not None and company.client_id != client.id:
        raise ValidationError("El tercero no pertenece a este cliente.")


def _clean_lines(lines):
    out = []
    for ln in lines or []:
        desc = (ln.get("description") or "").strip()
        if not desc and not ln.get("unit_price"):
            continue                                   # fila vacía del formulario
        qty = Decimal(str(ln.get("quantity") if ln.get("quantity") not in (None, "") else 1))
        if qty != qty.to_integral_value():
            raise ValidationError("La cantidad de cada línea debe ser un número entero.")
        if qty <= 0:
            raise ValidationError("La cantidad de cada línea debe ser mayor que 0.")
        qty = qty.to_integral_value()
        disc = Decimal(str(ln.get("discount_pct") or 0))
        if not (0 <= disc <= 100):
            raise ValidationError("El descuento debe estar entre 0 y 100.")
        rate = Decimal(str(ln.get("tax_rate") or 0))
        if not (0 <= rate <= 100):
            raise ValidationError("El impuesto debe estar entre 0 y 100.")
        out.append({
            "description": desc[:500], "quantity": qty,
            "unit_price": Decimal(str(ln.get("unit_price") or 0)),
            "tax_rate": rate, "discount_pct": disc,
        })
    return out


def _apply_lines_and_totals(document, issued, lines):
    """Sustituye líneas y desglose, y rellena los importes del documento."""
    totals = compute_totals(
        lines, global_discount_pct=issued.global_discount_pct,
        withholding_pct=issued.withholding_pct, apply_surcharge=issued.apply_surcharge,
    )
    document.lines.all().delete()
    document.tax_lines.all().delete()
    InvoiceLine.objects.bulk_create([
        InvoiceLine(document=document, position=i, amount=amt, **ln)
        for i, (ln, amt) in enumerate(zip(lines, totals.line_amounts), start=1)
    ])
    rows = [TaxBreakdown(document=document, kind="vat", rate=r, base=b, amount=a)
            for r, (b, a) in totals.vat.items()]
    rows += [TaxBreakdown(document=document, kind="surcharge", rate=r, base=b, amount=a)
             for r, (b, a) in totals.surcharge.items()]
    if totals.withholding:
        r, b, a = totals.withholding
        rows.append(TaxBreakdown(document=document, kind="withholding", rate=r, base=b, amount=a))
    TaxBreakdown.objects.bulk_create(rows)

    document.base_amount = totals.base
    document.tax_amount = totals.vat_total
    document.tax_percentage = next(iter(totals.vat)) if len(totals.vat) == 1 else None
    document.total_amount = totals.total
    return totals


def _set_options(issued, options):
    for name in OPTION_FIELDS:
        if name in options and options[name] is not None:
            setattr(issued, name, options[name])


def create_draft(*, client, user, document_type, issue_date=None, company=None, lines=None,
                 category=None, tags=(), rectifies=None, rectification_reason="", **options) -> Document:
    """Crea un borrador (o un presupuesto/proforma, que se numeran al crear)."""
    if document_type not in ISSUABLE_TYPES:
        raise ValidationError("Tipo de documento no emitible.")
    _check_company(client, company)
    lines = _clean_lines(lines)
    issue_date = issue_date or timezone.localdate()
    bs = billing_settings_for(client)
    is_quote = document_type in NUMBERED_ON_CREATE

    with transaction.atomic():
        doc = Document(
            client=client, company=company, original_name=f"borrador-{document_type}",
            document_type=document_type, source="app", status="draft", flow="in",
            review_level="manual", processing_status="processed", currency="EUR",
            confidence={"emitido": 1}, issue_date=issue_date, category=category,
            iban=bs.iban,
        )
        if rectifies is not None:
            doc.rectification_reason = (rectification_reason or "")[:255]
            doc.rectified_by = user
        issued = IssuedDocument(
            document=doc, rectifies=rectifies,
            payment_method=bs.default_payment_method,
            quote_status="draft" if is_quote else "",
        )
        _set_options(issued, options)
        if is_quote and not issued.valid_until:
            issued.valid_until = issue_date + timedelta(days=bs.quote_validity_days)
        if not is_quote and not issued.due_date:
            issued.due_date = issue_date + timedelta(days=bs.default_payment_days)
        if is_quote:
            doc.document_number = next_number(client, document_type, issue_date)
            doc.original_name = f"{doc.document_number}.pdf"
        doc.save()
        issued.document = doc
        issued.save()
        _apply_lines_and_totals(doc, issued, lines)
        doc.save()
        doc.tags.set([t for t in tags if t.client_id == client.id])
        DocumentAuditLog(document=doc, user=user, action="created", changes={"origin": "app"}).save()
    return doc


def update_draft(document, user, *, lines=None, company=..., issue_date=None, category=...,
                 tags=None, **options) -> Document:
    """Edita un borrador: opciones, tercero, fecha y líneas (se reemplazan)."""
    _require_draft(document)
    with transaction.atomic():
        doc = Document.all_objects.select_for_update().get(pk=document.pk)
        _require_draft(doc)
        issued = doc.issued
        if company is not ...:
            _check_company(doc.client, company)
            doc.company = company
        if category is not ...:
            doc.category = category
        if issue_date:
            doc.issue_date = issue_date
        _set_options(issued, options)
        current = lines if lines is not None else [
            {"description": l.description, "quantity": l.quantity, "unit_price": l.unit_price,
             "tax_rate": l.tax_rate, "discount_pct": l.discount_pct}
            for l in doc.lines.all()
        ]
        current = _clean_lines(current)
        _apply_lines_and_totals(doc, issued, current)
        doc.edited_at = timezone.now()
        doc.save()
        issued.save()
        if tags is not None:
            doc.tags.set([t for t in tags if t.client_id == doc.client_id])
        DocumentAuditLog(document=doc, user=user, action="edited", changes={"origin": "app"}).save()
    return doc


def approve(document, user) -> Document:
    """Emite el documento: número, snapshots fiscales, PDF y cierre (ya no se edita)."""
    from documents.services.pdf_issued import render_issued_pdf

    with transaction.atomic():
        doc = Document.all_objects.select_for_update().get(pk=document.pk)
        _require_draft(doc)
        problems = list(doc.approval_blockers())
        if not doc.lines.exists():
            problems.append("al menos una línea")
        if doc.company_id is None and doc.document_type != "receipt":
            problems.append("el contacto")
        if doc.document_type == "corrected_invoice" and doc.issued.rectifies_id is None:
            problems.append("la factura que rectifica")
        if problems:
            raise ValidationError("Falta: " + ", ".join(problems) + ".")

        issued = doc.issued
        if issued.rectifies_id:
            _check_rectification_amount(doc, issued.rectifies)
        if not doc.document_number:
            doc.document_number = next_number(doc.client, doc.document_type, doc.issue_date)
        issued.issuer_snapshot, issued.recipient_snapshot = build_snapshots(doc.client, doc.company)
        if doc.document_type in NUMBERED_ON_CREATE and not issued.public_token:
            issued.public_token = secrets.token_urlsafe(32)   # el enlace solo funciona tras enviarlo
        issued.save()

        # El estado se fija ANTES de renderizar: la plantilla marca «BORRADOR» según `status`
        # y el PDF guardado no se puede regenerar después.
        doc.status = "approved"
        doc.approved_at = timezone.now()
        pdf = render_issued_pdf(doc)
        name = f"{doc.document_number}.pdf"
        doc.file.save(name, ContentFile(pdf), save=False)
        doc.original_name = name
        doc.approved_by = user
        doc.reviewed_by = user
        doc._issuing_op = True
        doc.save()
        DocumentFile.objects.create(
            document=doc, client=doc.client, sha256=hashlib.sha256(pdf).hexdigest(),
            mime_type="application/pdf", size_bytes=len(pdf), origin="issued", uploaded_by=user,
        )
        DocumentAuditLog(
            document=doc, user=user, action="issued", changes={"number": doc.document_number},
        ).save()
    return doc


def void(document, user, reason="") -> Document:
    """Anula un documento emitido y aprobado (no se borra: queda el rastro y el número)."""
    with transaction.atomic():
        doc = Document.all_objects.select_for_update().get(pk=document.pk)
        if not doc.is_issued or doc.status != "approved":
            raise ValidationError("Solo se puede anular un documento emitido y aprobado.")
        if Document.all_objects.filter(issued__rectifies=doc, status="approved").exists():
            # Si no, la factura saldría de las métricas y su rectificativa seguiría restando.
            raise ValidationError("Tiene rectificativas emitidas: anúlalas antes de anular la factura.")
        doc.status = "voided"
        doc._issuing_op = True
        doc.save()
        DocumentAuditLog(
            document=doc, user=user, action="voided", changes={"reason": (reason or "")[:255]},
        ).save()
    return doc


def convert_quote_to_invoice(quote, user) -> Document:
    """Crea una factura en borrador con los datos de un presupuesto/proforma emitido.

    Copia contacto, líneas, precios, descuentos, impuestos y condiciones. La factura recibe su número
    al aprobarla. Un presupuesto solo se convierte una vez y no si el cliente lo rechazó."""
    with transaction.atomic():
        q = Document.all_objects.select_for_update().get(pk=quote.pk)
        if not q.is_issued or q.document_type not in NUMBERED_ON_CREATE or q.status != "approved":
            raise ValidationError("Solo se puede convertir un presupuesto emitido.")
        qi = q.issued
        if qi.quote_status == "converted":
            raise ValidationError("Este presupuesto ya se convirtió en factura.")
        if qi.quote_status == "declined":
            raise ValidationError("El cliente rechazó este presupuesto.")
        lines = [
            {"description": l.description, "quantity": l.quantity, "unit_price": l.unit_price,
             "tax_rate": l.tax_rate, "discount_pct": l.discount_pct}
            for l in q.lines.all()
        ]
        invoice = create_draft(
            client=q.client, user=user, document_type="invoice", company=q.company, lines=lines,
            category=q.category, tags=list(q.tags.all()), payment_method=qi.payment_method, notes=qi.notes,
            global_discount_pct=qi.global_discount_pct, withholding_pct=qi.withholding_pct,
            apply_surcharge=qi.apply_surcharge,
        )
        qi.quote_status = "converted"
        qi.converted_to = invoice
        qi.save(update_fields=["quote_status", "converted_to"])
        DocumentAuditLog(document=q, user=user, action="converted", changes={"invoice": invoice.pk}).save()
        DocumentAuditLog(document=invoice, user=user, action="created",
                         changes={"origin": "quote", "quote": q.pk}).save()
    return invoice


RECTIFIABLE_TYPES = ("invoice", "receipt")


def _check_rectification_amount(rectification, original):
    """Las rectificativas emitidas no pueden restar más que la factura original."""
    if original.status != "approved":
        raise ValidationError("La factura original está anulada: no se puede rectificar.")
    done = (
        Document.all_objects.filter(issued__rectifies=original, status="approved")
        .exclude(pk=rectification.pk).aggregate(t=Sum("total_amount"))["t"] or Decimal("0")
    )
    if done + (rectification.total_amount or 0) > (original.total_amount or 0):
        raise ValidationError(
            f"Las rectificativas ({done + rectification.total_amount} €) superan el importe de la "
            f"factura original ({original.total_amount} €)."
        )


def issue_rectification(original, user, reason) -> Document:
    """Crea una rectificativa en borrador a partir de una factura/ticket emitido.

    Copia contacto, líneas, descuentos e impuestos (importes en positivo: la rectificativa resta).
    El borrador se edita como cualquier otro: para rectificar solo una parte, se ajustan las líneas.
    Si ya hay una rectificativa en borrador de esta factura, se devuelve esa."""
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError("Indica el motivo de la rectificación.")
    with transaction.atomic():
        orig = Document.all_objects.select_for_update().get(pk=original.pk)
        if not orig.is_issued or orig.status != "approved" or orig.document_type not in RECTIFIABLE_TYPES:
            raise ValidationError("Solo se puede rectificar una factura o ticket emitido y vigente.")
        existing = Document.all_objects.filter(issued__rectifies=orig, status="draft").first()
        if existing:
            return existing
        oi = orig.issued
        lines = [
            {"description": l.description, "quantity": l.quantity, "unit_price": l.unit_price,
             "tax_rate": l.tax_rate, "discount_pct": l.discount_pct}
            for l in orig.lines.all()
        ]
        doc = create_draft(
            client=orig.client, user=user, document_type="corrected_invoice", company=orig.company,
            lines=lines, category=orig.category, tags=list(orig.tags.all()),
            payment_method=oi.payment_method, global_discount_pct=oi.global_discount_pct,
            withholding_pct=oi.withholding_pct, apply_surcharge=oi.apply_surcharge,
            rectifies=orig, rectification_reason=reason,
        )
        DocumentAuditLog(
            document=orig, user=user, action="rectified", changes={"rectification": doc.pk, "reason": reason[:255]},
        ).save()
    return doc
