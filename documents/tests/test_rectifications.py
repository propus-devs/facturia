"""Fase 6: rectificativas y tickets emitidos."""
from datetime import date
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.test import Client as WebClient
from django.urls import reverse

from documents.models import Document
from documents.selectors.document_selector import DocumentSelector
from documents.services import issuing
from documents.services.signs import signed_amount
from documents.tests.factories import CompanyFactory, UserFactory

D = Decimal
LINES = [
    {"description": "A", "quantity": "2", "unit_price": "50", "tax_rate": "21", "discount_pct": "0"},
    {"description": "B", "quantity": "1", "unit_price": "100", "tax_rate": "10", "discount_pct": "0"},
]


@pytest.fixture
def ctx(db):
    user = UserFactory()
    return user, CompanyFactory(client=user.client, is_provider=False, is_customer=True)


def invoice(ctx, document_type="invoice", lines=LINES):
    user, company = ctx
    d = issuing.create_draft(client=user.client, user=user, company=company, document_type=document_type,
                             issue_date=date(2026, 3, 10), lines=lines)
    return issuing.approve(d, user)



@pytest.mark.django_db
def test_rectification_draft_copies_lines_and_links_original(ctx):
    user, company = ctx
    orig = invoice(ctx)
    r = issuing.issue_rectification(orig, user, "Precio mal puesto")
    assert r.document_type == "corrected_invoice" and r.status == "draft" and not r.document_number
    assert r.issued.rectifies == orig and r.rectification_reason == "Precio mal puesto" and r.rectified_by == user
    assert r.company == company and r.lines.count() == 2 and r.total_amount == orig.total_amount == D("231.00")
    assert orig.audit_logs.filter(action="rectified").exists()
    orig.refresh_from_db()
    assert orig.status == "approved" and orig.is_current          # el original no cambia


@pytest.mark.django_db
def test_requires_reason_and_valid_original(ctx):
    user, _ = ctx
    orig = invoice(ctx)
    with pytest.raises(ValidationError, match="motivo"):
        issuing.issue_rectification(orig, user, "  ")
    draft = issuing.create_draft(client=user.client, user=user, document_type="invoice", lines=LINES)
    with pytest.raises(ValidationError):
        issuing.issue_rectification(draft, user, "x")            # borrador
    issuing.void(orig, user)
    with pytest.raises(ValidationError):
        issuing.issue_rectification(orig, user, "x")             # anulada
    quote = invoice(ctx, "quote")
    with pytest.raises(ValidationError):
        issuing.issue_rectification(quote, user, "x")            # presupuesto


@pytest.mark.django_db
def test_existing_draft_rectification_is_reused(ctx):
    user, _ = ctx
    orig = invoice(ctx)
    a = issuing.issue_rectification(orig, user, "uno")
    assert issuing.issue_rectification(orig, user, "dos") == a
    assert Document.all_objects.filter(document_type="corrected_invoice").count() == 1


@pytest.mark.django_db
def test_approve_numbers_in_own_series_and_pdf_references_original(ctx):
    from unittest import mock
    from documents.services import pdf_issued
    user, _ = ctx
    orig = invoice(ctx)
    r = issuing.issue_rectification(orig, user, "Error de precio")
    seen = {}
    real = pdf_issued.render_issued_html
    with mock.patch.object(pdf_issued, "render_issued_html", lambda d: seen.setdefault("h", real(d))):
        r = issuing.approve(r, user)
    assert r.document_number == "REC-2026-0001" and orig.document_number == "FAC-2026-0001"
    h = seen["h"]
    assert "Factura rectificativa" in h and "FAC-2026-0001" in h and "Error de precio" in h and "BORRADOR" not in h


@pytest.mark.django_db
def test_rectifications_cannot_exceed_original_total(ctx):
    user, _ = ctx
    orig = invoice(ctx)                                          # 231,00
    r1 = issuing.issue_rectification(orig, user, "parcial")
    issuing.update_draft(r1, user, lines=[{**LINES[0], "unit_price": "40"}])     # 80 + 21 % = 96,80
    issuing.approve(r1, user)
    r2 = issuing.issue_rectification(orig, user, "otra")
    issuing.update_draft(r2, user, lines=[{**LINES[0], "unit_price": "100"}])    # 242 → total 338,80 > 231
    with pytest.raises(ValidationError, match="superan"):
        issuing.approve(r2, user)
    issuing.update_draft(r2, user, lines=[{**LINES[0], "unit_price": "40"}])     # 96,80 → 193,60 ≤ 231
    assert issuing.approve(r2, user).status == "approved"


@pytest.mark.django_db
def test_corrected_without_original_cannot_be_approved(ctx):
    user, company = ctx
    d = issuing.create_draft(client=user.client, user=user, company=company, document_type="corrected_invoice", lines=LINES)
    with pytest.raises(ValidationError, match="rectifica"):
        issuing.approve(d, user)


@pytest.mark.django_db
def test_rectification_subtracts_in_totals_and_original_stays_visible(ctx):
    user, _ = ctx
    orig = invoice(ctx)
    r = issuing.approve(issuing.issue_rectification(orig, user, "abono total"), user)
    qs = DocumentSelector.for_client(user.client)
    assert {orig.pk, r.pk} <= set(qs.values_list("pk", flat=True))
    assert qs.aggregate(t=Sum(signed_amount("total_amount")))["t"] == D("0.00")      # 231 − 231


@pytest.mark.django_db
def test_partial_rectification_by_editing_lines(ctx):
    user, _ = ctx
    orig = invoice(ctx)
    r = issuing.issue_rectification(orig, user, "devolución de A")
    issuing.update_draft(r, user, lines=[LINES[0]])
    r = issuing.approve(r, user)
    assert r.total_amount == D("121.00")
    assert DocumentSelector.for_client(user.client).aggregate(t=Sum(signed_amount("total_amount")))["t"] == D("110.00")


# --- vistas ------------------------------------------------------------------------------

@pytest.fixture
def web(ctx):
    c = WebClient(); c.force_login(ctx[0]); return c


@pytest.mark.django_db
def test_detail_rectify_flow(web, ctx):
    orig = invoice(ctx)
    d = reverse("sales:detail", args=[orig.pk])
    html = web.get(d).content.decode()
    assert 'id="rectify-dialog"' in html
    r = web.post(d, {"action": "rectify", "reason": ""})
    assert r.status_code == 302 and not Document.all_objects.filter(document_type="corrected_invoice").exists()
    r = web.post(d, {"action": "rectify", "reason": "Error"})
    rect = Document.all_objects.get(document_type="corrected_invoice")
    assert r["Location"] == reverse("sales:edit", args=[rect.pk])
    edit = web.get(r["Location"]).content.decode()
    assert "Rectifica la factura" in edit and orig.document_number in edit
    assert "Factura rectificativa" in edit and "Ticket" not in edit.split('name="document_type"')[1].split("</select>")[0]
    page = web.get(d).content.decode()
    assert reverse("sales:detail", args=[rect.pk]) in page                       # lista de rectificativas
    assert reverse("sales:detail", args=[orig.pk]) in web.get(reverse("sales:detail", args=[rect.pk])).content.decode()


@pytest.mark.django_db
def test_draft_has_no_rectify_button_and_other_tenant_404(web, ctx):
    user, company = ctx
    draft = issuing.create_draft(client=user.client, user=user, company=company, document_type="invoice", lines=LINES)
    assert 'id="rectify-dialog"' not in web.get(reverse("sales:detail", args=[draft.pk])).content.decode()
    orig = invoice(ctx)
    w2 = WebClient(); w2.force_login(UserFactory())
    assert w2.post(reverse("sales:detail", args=[orig.pk]), {"action": "rectify", "reason": "x"}).status_code == 404
    assert not Document.all_objects.filter(document_type="corrected_invoice").exists()


@pytest.mark.django_db
def test_edit_form_offers_only_the_documents_own_type(web, ctx):
    user, company = ctx
    d = issuing.create_draft(client=user.client, user=user, company=company, document_type="invoice", lines=LINES)
    sel = web.get(reverse("sales:edit", args=[d.pk])).content.decode().split('name="document_type"')[1].split("</select>")[0]
    assert "Factura" in sel and "Ticket" not in sel


# --- tickets -------------------------------------------------------------------------------

@pytest.mark.django_db
def test_ticket_from_new_form_without_contact_and_own_series(web, ctx):
    data = {
        "document_type": "receipt", "company": "", "issue_date": "2026-03-10", "payment_method": "cash",
        "global_discount_pct": "", "withholding_pct": "", "action": "approve",
        "lines-TOTAL_FORMS": 1, "lines-INITIAL_FORMS": 0, "lines-MIN_NUM_FORMS": 0, "lines-MAX_NUM_FORMS": 1000,
        "lines-0-description": "Café", "lines-0-quantity": "2", "lines-0-unit_price": "1,50",
        "lines-0-tax_rate": "10", "lines-0-discount_pct": "0",
    }
    r = web.post(reverse("sales:invoice_new"), data)
    t = Document.all_objects.get(document_type="receipt")
    assert r.status_code == 302 and t.status == "approved" and t.document_number == "TIC-2026-0001" and t.company is None
    assert t.total_amount == D("3.30")
    assert web.get(reverse("sales:pdf", args=[t.pk])).status_code == 200
    # un ticket también se puede rectificar
    assert issuing.issue_rectification(t, t.client.users.first(), "Devolución").document_type == "corrected_invoice"
