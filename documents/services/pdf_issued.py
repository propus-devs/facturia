"""PDF de un documento emitido (factura, ticket, presupuesto, rectificativa, proforma)."""
from django.template.loader import render_to_string
from django.utils import timezone

from documents.utils import _logo_as_data_uri, render_pdf_from_html

TITLES = {
    "invoice": "Factura", "corrected_invoice": "Factura rectificativa", "receipt": "Ticket",
    "quote": "Presupuesto", "proforma": "Factura proforma",
}


def build_context(document) -> dict:
    from documents.services.issuing import build_snapshots

    issued = document.issued
    if issued.issuer_snapshot:                      # aprobado: datos congelados
        issuer, recipient = issued.issuer_snapshot, issued.recipient_snapshot
    else:                                           # borrador: datos actuales
        issuer, recipient = build_snapshots(document.client, document.company)
    tax_lines = list(document.tax_lines.all())
    return {
        "doc": document,
        "issued": issued,
        "title": TITLES.get(document.document_type, "Documento"),
        "issuer": issuer,
        "recipient": recipient,
        "lines": list(document.lines.all()),
        "vat_lines": [t for t in tax_lines if t.kind == "vat"],
        "surcharge_lines": [t for t in tax_lines if t.kind == "surcharge"],
        "withholding_lines": [t for t in tax_lines if t.kind == "withholding"],
        "has_discounts": issued.global_discount_pct > 0 or any(l.discount_pct > 0 for l in document.lines.all()),
        "rectified": issued.rectifies if issued.rectifies_id else None,
        "rectification_reason": document.rectification_reason or "",
        "is_quote": document.document_type in ("quote", "proforma"),
        "is_draft": document.status == "draft",
        "logo_data_uri": _logo_as_data_uri(document.client),
        "footer": document.client.billing_settings.footer_notes
        if hasattr(document.client, "billing_settings") else "",
        "generated_at": timezone.now(),
    }


def render_issued_html(document) -> str:
    return render_to_string("private/documents/issued_pdf.html", build_context(document))


def render_issued_pdf(document) -> bytes:
    return render_pdf_from_html(render_issued_html(document))
