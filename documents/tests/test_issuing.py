"""Fase 2 de emisión: totales, numeración, borrador → aprobar → anular, PDF."""
from datetime import date
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError

from documents.models import DocumentAuditLog, DocumentFile, NumberSequence
from documents.services import issuing
from documents.services.issued_totals import compute_totals
from documents.services.numbering import next_number
from documents.tests.factories import ClientFactory, CompanyFactory, UserFactory

D = Decimal


def L(qty="1", price="100", vat="21", dto="0", desc="Servicio"):
    return {"description": desc, "quantity": qty, "unit_price": price, "tax_rate": vat, "discount_pct": dto}


# --- totales (puros) ---------------------------------------------------------

def test_totals_basic_vat():
    t = compute_totals([{"quantity": D(2), "unit_price": D("50"), "tax_rate": D(21), "discount_pct": D(0)}])
    assert (t.base, t.vat_total, t.total) == (D("100.00"), D("21.00"), D("121.00"))


def test_totals_line_and_global_discount():
    lines = [{"quantity": D(1), "unit_price": D("200"), "tax_rate": D(21), "discount_pct": D(10)}]
    t = compute_totals(lines, global_discount_pct=D(10))
    assert t.line_amounts == [D("180.00")]
    assert t.base == D("162.00") and t.vat_total == D("34.02") and t.total == D("196.02")


def test_totals_mixed_vat_surcharge_and_withholding():
    lines = [
        {"quantity": D(1), "unit_price": D("100"), "tax_rate": D(21), "discount_pct": D(0)},
        {"quantity": D(1), "unit_price": D("100"), "tax_rate": D(10), "discount_pct": D(0)},
    ]
    t = compute_totals(lines, withholding_pct=D(15), apply_surcharge=True)
    assert t.vat_total == D("31.00")
    assert t.surcharge_total == D("6.60")          # 5,2 % de 100 + 1,4 % de 100
    assert t.withholding_total == D("30.00")
    assert t.total == D("200.00") + D("31.00") + D("6.60") - D("30.00")


def test_totals_empty_and_zero_vat():
    assert compute_totals([]).total == D("0.00")
    t = compute_totals([{"quantity": D(1), "unit_price": D("10"), "tax_rate": D(0), "discount_pct": D(0)}])
    assert t.total == D("10.00") and t.vat_total == D("0.00")


def test_totals_are_decimal_and_half_up():
    t = compute_totals([{"quantity": D("1"), "unit_price": D("0.125"), "tax_rate": D(0), "discount_pct": D(0)}])
    assert t.base == D("0.13") and isinstance(t.total, Decimal)


# --- numeración --------------------------------------------------------------

@pytest.mark.django_db
def test_numbering_is_sequential_and_separate_per_kind_and_client():
    a, b = ClientFactory(), ClientFactory()
    d = date(2026, 5, 1)
    assert [next_number(a, "invoice", d) for _ in range(3)] == ["FAC-2026-0001", "FAC-2026-0002", "FAC-2026-0003"]
    assert next_number(a, "quote", d) == "PRE-2026-0001"       # serie propia
    assert next_number(b, "invoice", d) == "FAC-2026-0001"     # otro cliente
    assert next_number(a, "invoice", date(2027, 1, 1)) == "FAC-2027-0001"   # año nuevo


@pytest.mark.django_db
def test_failed_transaction_leaves_no_gap():
    from django.db import transaction
    c = ClientFactory()
    with pytest.raises(RuntimeError), transaction.atomic():
        next_number(c, "invoice", date(2026, 1, 1))
        raise RuntimeError
    assert next_number(c, "invoice", date(2026, 1, 1)) == "FAC-2026-0001"
    assert NumberSequence.objects.get(client=c).next_number == 2


# --- ciclo de vida -----------------------------------------------------------

@pytest.fixture
def ctx(db):
    user = UserFactory()
    company = CompanyFactory(client=user.client, is_provider=False, is_customer=True)
    return user, company


def make(ctx, **kw):
    user, company = ctx
    kw.setdefault("lines", [L()])
    kw.setdefault("document_type", "invoice")
    return issuing.create_draft(client=user.client, user=user, company=company, issue_date=date(2026, 3, 10), **kw)


@pytest.mark.django_db
def test_create_draft_computes_totals_defaults_and_no_number(ctx):
    doc = make(ctx, lines=[L(qty="2", price="50"), L(price="10", vat="10")])
    assert doc.status == "draft" and doc.source == "app" and doc.flow == "in"
    assert not doc.document_number and not doc.file
    assert (doc.base_amount, doc.tax_amount, doc.total_amount) == (D("110.00"), D("22.00"), D("132.00"))
    assert doc.issued.due_date == date(2026, 4, 9)         # +30 días por defecto
    assert doc.lines.count() == 2 and doc.tax_lines.count() == 2
    assert doc.audit_logs.filter(action="created").exists()


@pytest.mark.django_db
def test_quote_is_numbered_on_create_with_validity(ctx):
    doc = make(ctx, document_type="quote")
    assert doc.document_number == "PRE-2026-0001"
    assert doc.issued.quote_status == "draft" and doc.issued.valid_until == date(2026, 4, 9)


@pytest.mark.django_db
def test_empty_rows_are_ignored_and_bad_values_rejected(ctx):
    doc = make(ctx, lines=[L(), {"description": "", "unit_price": None}])
    assert doc.lines.count() == 1
    with pytest.raises(ValidationError):
        make(ctx, lines=[L(qty="0")])
    with pytest.raises(ValidationError):
        make(ctx, lines=[L(dto="150")])


@pytest.mark.django_db
def test_update_draft_replaces_lines(ctx):
    user, _ = ctx
    doc = make(ctx)
    issuing.update_draft(doc, user, lines=[L(price="200")], notes="Gracias")
    doc.refresh_from_db()
    assert doc.total_amount == D("242.00") and doc.lines.count() == 1 and doc.issued.notes == "Gracias"


@pytest.mark.django_db
def test_approve_assigns_number_pdf_and_locks(ctx):
    user, _ = ctx
    doc = issuing.approve(make(ctx), user)
    assert doc.status == "approved" and doc.document_number == "FAC-2026-0001"
    assert doc.file and doc.original_name == "FAC-2026-0001.pdf"
    with doc.file.open("rb") as f:
        assert f.read(5) == b"%PDF-"
    assert DocumentFile.objects.get(document=doc).origin == "issued"
    assert doc.issued.issuer_snapshot["name"] == user.client.name and doc.issued.recipient_snapshot["name"]
    assert doc.audit_logs.filter(action="issued").exists()
    # cerrada: ni editar ni volver a aprobar
    with pytest.raises(ValidationError):
        issuing.update_draft(doc, user, lines=[L(price="1")])
    with pytest.raises(ValidationError):
        issuing.approve(doc, user)


@pytest.mark.django_db
def test_second_invoice_gets_next_number(ctx):
    user, _ = ctx
    assert issuing.approve(make(ctx), user).document_number == "FAC-2026-0001"
    assert issuing.approve(make(ctx), user).document_number == "FAC-2026-0002"


@pytest.mark.django_db
def test_approve_requires_lines_and_contact(ctx):
    user, _ = ctx
    with pytest.raises(ValidationError, match="línea"):
        issuing.approve(make(ctx, lines=[]), user)
    no_contact = issuing.create_draft(client=user.client, user=user, document_type="invoice", lines=[L()])
    with pytest.raises(ValidationError, match="contacto"):
        issuing.approve(no_contact, user)
    assert not no_contact.document_number      # un intento fallido no consume número
    assert not NumberSequence.objects.filter(client=user.client).exists()


@pytest.mark.django_db
def test_receipt_can_be_approved_without_contact(ctx):
    user, _ = ctx
    doc = issuing.create_draft(client=user.client, user=user, document_type="receipt", lines=[L()])
    assert issuing.approve(doc, user).document_number.startswith("TIC-")


@pytest.mark.django_db
def test_void_keeps_record_and_number(ctx):
    user, _ = ctx
    doc = issuing.void(issuing.approve(make(ctx), user), user, "Error")
    assert doc.status == "voided" and doc.document_number == "FAC-2026-0001"
    assert doc.audit_logs.filter(action="voided").exists()
    with pytest.raises(ValidationError):
        issuing.void(doc, user)
    with pytest.raises(ValidationError):                  # un borrador no se anula (se edita o descarta)
        issuing.void(make(ctx), user)


@pytest.mark.django_db
def test_other_tenant_company_and_tags_rejected(ctx):
    user, _ = ctx
    foreign = CompanyFactory(client=ClientFactory())
    with pytest.raises(ValidationError):
        issuing.create_draft(client=user.client, user=user, document_type="invoice", company=foreign, lines=[L()])
    doc = make(ctx)
    with pytest.raises(ValidationError):
        issuing.update_draft(doc, user, company=foreign)


@pytest.mark.django_db
def test_issued_pdf_renders_draft_and_html_contains_data(ctx):
    from documents.services.pdf_issued import render_issued_html, render_issued_pdf
    user, company = ctx
    doc = make(ctx, lines=[L(desc="Consultoría", dto="10")], global_discount_pct=D(5), withholding_pct=D(15))
    html = render_issued_html(doc)
    assert "Consultoría" in html and "BORRADOR" in html and company.name in html and "Retención IRPF" in html
    assert render_issued_pdf(doc).startswith(b"%PDF-")


@pytest.mark.django_db
def test_issued_documents_are_not_visible_to_other_tenant(ctx):
    from documents.selectors.document_selector import DocumentSelector
    user, _ = ctx
    doc = issuing.approve(make(ctx), user)
    other = ClientFactory()
    assert not DocumentSelector.for_client(other).filter(pk=doc.pk).exists()


@pytest.mark.django_db
@pytest.mark.parametrize("document_type", ["invoice", "quote", "receipt"])
def test_stored_pdf_is_rendered_as_issued_not_draft(ctx, document_type):
    from unittest import mock
    from documents.services import pdf_issued
    user, _ = ctx
    seen = {}
    real = pdf_issued.render_issued_html

    def spy(document):
        seen["status"], seen["html"] = document.status, real(document)
        return seen["html"]

    with mock.patch.object(pdf_issued, "render_issued_html", spy):
        doc = issuing.approve(make(ctx, document_type=document_type), user)
    assert seen["status"] == "approved" and "BORRADOR" not in seen["html"]
    assert doc.document_number in seen["html"]
    assert "BORRADOR" in pdf_issued.render_issued_html(make(ctx))        # el borrador sí lo lleva


@pytest.mark.django_db
def test_service_rejects_fractional_quantity_and_stores_integer(ctx):
    with pytest.raises(ValidationError, match="entero"):
        make(ctx, lines=[L(qty="1.5")])
    doc = make(ctx, lines=[L(qty="3.0")])
    assert doc.lines.get().quantity == 3 and doc.total_amount == D("363.00")
