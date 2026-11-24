"""Fase 3: vistas de Ventas (lista, formulario, detalle, PDF, contactos, ajustes) y aislamiento de tenants."""
from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from documents.models import Document
from documents.services import issuing
from documents.tests.factories import ClientFactory, CompanyFactory, UserFactory


@pytest.fixture
def ctx(db):
    user = UserFactory()
    company = CompanyFactory(client=user.client, is_provider=False, is_customer=True)
    return user, company


@pytest.fixture
def web(ctx):
    from django.test import Client as WebClient
    c = WebClient()
    c.force_login(ctx[0])
    return c


def line(**kw):
    d = {"description": "Servicio", "quantity": "1", "unit_price": "100", "tax_rate": "21", "discount_pct": "0"}
    d.update(kw)
    return d


def post_data(company, *, action="save", lines=None, **extra):
    lines = lines if lines is not None else [line()]
    data = {
        "document_type": "invoice", "company": company.pk, "issue_date": "2026-03-10", "payment_method": "transfer",
        "global_discount_pct": "", "withholding_pct": "", "action": action,
        "lines-TOTAL_FORMS": len(lines), "lines-INITIAL_FORMS": 0, "lines-MIN_NUM_FORMS": 0, "lines-MAX_NUM_FORMS": 1000,
    }
    for i, ln in enumerate(lines):
        for k, v in ln.items():
            data[f"lines-{i}-{k}"] = v
    data.update(extra)
    return data


def draft(ctx, **kw):
    user, company = ctx
    kw.setdefault("lines", [line()])
    kw.setdefault("document_type", "invoice")
    return issuing.create_draft(client=user.client, user=user, company=company, issue_date=date(2026, 3, 10), **kw)


@pytest.mark.django_db
def test_all_pages_require_login(client, ctx):
    doc = draft(ctx)
    for name, args in [("sales:invoices", []), ("sales:quotes", []), ("sales:invoice_new", []), ("sales:quote_new", []),
                       ("sales:detail", [doc.pk]), ("sales:edit", [doc.pk]), ("sales:pdf", [doc.pk]), ("sales:settings", [])]:
        r = client.get(reverse(name, args=args))
        assert r.status_code == 302 and "/login/" in r["Location"], name


@pytest.mark.django_db
def test_pages_render(web, ctx):
    doc = draft(ctx)
    for name, args in [("sales:invoices", []), ("sales:quotes", []), ("sales:invoice_new", []), ("sales:quote_new", []),
                       ("sales:detail", [doc.pk]), ("sales:edit", [doc.pk]), ("sales:settings", [])]:
        assert web.get(reverse(name, args=args)).status_code == 200, name


@pytest.mark.django_db
def test_create_draft_via_form(web, ctx):
    r = web.post(reverse("sales:invoice_new"), post_data(ctx[1], lines=[line(), line(description="", unit_price="")]))
    doc = Document.all_objects.get(source="app")
    assert r.status_code == 302 and r["Location"] == reverse("sales:detail", args=[doc.pk])
    assert doc.status == "draft" and doc.total_amount == Decimal("121.00") and doc.lines.count() == 1


@pytest.mark.django_db
def test_create_and_approve_in_one_step(web, ctx):
    web.post(reverse("sales:invoice_new"), post_data(ctx[1], action="approve"))
    doc = Document.all_objects.get(source="app")
    assert doc.status == "approved" and doc.document_number == "FAC-2026-0001" and doc.file


@pytest.mark.django_db
def test_approve_failure_keeps_draft_and_redirects_to_edit(web, ctx):
    r = web.post(reverse("sales:invoice_new"), post_data(ctx[1], action="approve", lines=[line(description="", unit_price="")]))
    doc = Document.all_objects.get(source="app")
    assert doc.status == "draft" and r["Location"] == reverse("sales:edit", args=[doc.pk])


@pytest.mark.django_db
def test_comma_decimals_and_invalid_input(web, ctx):
    web.post(reverse("sales:invoice_new"), post_data(ctx[1], lines=[line(quantity="3", unit_price="10,50")]))
    assert Document.all_objects.get(source="app").base_amount == Decimal("31.50")
    r = web.post(reverse("sales:invoice_new"), post_data(ctx[1], lines=[line(tax_rate="150")]))
    assert r.status_code == 200 and Document.all_objects.filter(source="app").count() == 1


@pytest.mark.django_db
def test_quote_kind_only_creates_quote_types(web, ctx):
    r = web.post(reverse("sales:quote_new"), post_data(ctx[1], document_type="invoice"))
    assert r.status_code == 200 and not Document.all_objects.filter(source="app").exists()
    web.post(reverse("sales:quote_new"), post_data(ctx[1], document_type="quote"))
    assert Document.all_objects.get(source="app").document_number == "PRE-2026-0001"


@pytest.mark.django_db
def test_edit_draft_and_closed_document_cannot_be_edited(web, ctx):
    doc = draft(ctx)
    web.post(reverse("sales:edit", args=[doc.pk]), post_data(ctx[1], lines=[line(unit_price="200")]))
    doc.refresh_from_db()
    assert doc.total_amount == Decimal("242.00")
    web.post(reverse("sales:detail", args=[doc.pk]), {"action": "approve"})
    doc.refresh_from_db()
    assert doc.status == "approved"
    r = web.get(reverse("sales:edit", args=[doc.pk]))
    assert r.status_code == 302 and r["Location"] == reverse("sales:detail", args=[doc.pk])
    web.post(reverse("sales:edit", args=[doc.pk]), post_data(ctx[1], lines=[line(unit_price="1")]))
    doc.refresh_from_db()
    assert doc.total_amount == Decimal("242.00")


@pytest.mark.django_db
def test_void_action_and_pdf(web, ctx):
    doc = draft(ctx)
    r = web.get(reverse("sales:pdf", args=[doc.pk]))                     # borrador: PDF al vuelo
    assert r.status_code == 200 and b"".join(r.streaming_content if r.streaming else [r.content]).startswith(b"%PDF-")
    web.post(reverse("sales:detail", args=[doc.pk]), {"action": "approve"})
    r = web.get(reverse("sales:pdf", args=[doc.pk]) + "?download=1")     # emitido: archivo guardado
    assert r["Content-Disposition"].startswith("attachment") and r["X-Frame-Options"] == "SAMEORIGIN"
    web.post(reverse("sales:detail", args=[doc.pk]), {"action": "void", "reason": "Duplicada"})
    doc.refresh_from_db()
    assert doc.status == "voided"


@pytest.mark.django_db
def test_list_filters_and_scope(web, ctx):
    a = draft(ctx)
    b = issuing.approve(draft(ctx), ctx[0])
    draft(ctx, document_type="quote")
    html = web.get(reverse("sales:invoices")).content.decode()
    assert html.count(reverse("sales:detail", args=[a.pk])) >= 1 and reverse("sales:detail", args=[b.pk]) in html
    only_draft = web.get(reverse("sales:invoices") + "?status=draft").content.decode()
    assert reverse("sales:detail", args=[a.pk]) in only_draft and reverse("sales:detail", args=[b.pk]) not in only_draft
    assert len(web.get(reverse("sales:quotes")).context["documents"]) == 1


@pytest.mark.django_db
def test_other_tenant_gets_404(web, ctx):
    other_user = UserFactory()
    other_company = CompanyFactory(client=other_user.client)
    foreign = issuing.create_draft(client=other_user.client, user=other_user, company=other_company,
                                   document_type="invoice", lines=[line()])
    for name in ("sales:detail", "sales:edit", "sales:pdf"):
        assert web.get(reverse(name, args=[foreign.pk])).status_code == 404
    assert web.post(reverse("sales:detail", args=[foreign.pk]), {"action": "approve"}).status_code == 404
    foreign.refresh_from_db()
    assert foreign.status == "draft"
    # contacto de otro cliente rechazado en el formulario
    r = web.post(reverse("sales:invoice_new"), post_data(other_company))
    assert r.status_code == 200 and not Document.all_objects.filter(client=ctx[0].client, source="app").exists()


@pytest.mark.django_db
def test_drafts_and_voided_hidden_from_review_flow_and_metrics(ctx):
    from documents.selectors.document_selector import DocumentSelector
    user, _ = ctx
    d = draft(ctx)
    v = issuing.void(issuing.approve(draft(ctx), user), user)
    ok = issuing.approve(draft(ctx), user)
    ids = set(DocumentSelector.for_client(user.client).values_list("pk", flat=True))
    assert ids == {ok.pk} and d.pk not in ids and v.pk not in ids


@pytest.mark.django_db
def test_contact_quick_create(web, ctx):
    from documents.models import Company
    r = web.post(reverse("sales:contact_create"), {"name": "Nuevo Cliente SL", "tax_id": "B12345674", "email": "a@b.com"})
    assert r.status_code == 200
    c = Company.objects.get(pk=r.json()["id"])
    assert c.client == ctx[0].client and c.is_customer and c.email == "a@b.com"
    assert web.post(reverse("sales:contact_create"), {"name": "", "email": "mal"}).status_code == 400
    assert web.get(reverse("sales:contact_create")).status_code == 405


@pytest.mark.django_db
def test_billing_settings_view_saves_per_client(web, ctx):
    from clients.models import BillingSettings
    assert web.get(reverse("sales:settings")).status_code == 200
    r = web.post(reverse("sales:settings"), {
        "address": "Calle 1", "postal_code": "28001", "city": "Madrid", "email": "f@x.com", "iban": "ES00",
        "default_payment_days": 15, "default_payment_method": "cash", "quote_validity_days": 10,
        "footer_notes": "", "email_subject": "S", "email_body": "B",
    })
    assert r.status_code == 302
    bs = BillingSettings.objects.get(client=ctx[0].client)
    assert bs.default_payment_days == 15 and BillingSettings.objects.count() == 1
    assert draft(ctx).issued.due_date == date(2026, 3, 25)       # el servicio usa los ajustes


@pytest.mark.django_db
def test_new_form_defaults_issue_date_to_today_in_iso_format(web):
    from django.utils import timezone
    html = web.get(reverse("sales:invoice_new")).content.decode()
    assert f'name="issue_date" value="{timezone.localdate().isoformat()}"' in html
    html = web.get(reverse("sales:quote_new")).content.decode()
    assert f'name="issue_date" value="{timezone.localdate().isoformat()}"' in html


@pytest.mark.django_db
def test_edit_form_shows_saved_dates_in_iso_format(web, ctx):
    doc = draft(ctx)
    html = web.get(reverse("sales:edit", args=[doc.pk])).content.decode()
    assert 'name="issue_date" value="2026-03-10"' in html and 'name="due_date" value="2026-04-09"' in html


@pytest.mark.django_db
@pytest.mark.parametrize("bad", ["1,5", "2.5", "0", "-1", "abc"])
def test_line_quantity_must_be_a_positive_integer(web, ctx, bad):
    r = web.post(reverse("sales:invoice_new"), post_data(ctx[1], lines=[line(quantity=bad)]))
    assert r.status_code == 200 and not Document.all_objects.filter(source="app").exists()


@pytest.mark.django_db
def test_edit_form_shows_whole_quantities_and_integer_input(web, ctx):
    doc = draft(ctx, lines=[line(quantity="2")])
    html = web.get(reverse("sales:edit", args=[doc.pk])).content.decode()
    assert 'name="lines-0-quantity"' in html and 'value="2"' in html and "2.0000" not in html
    assert 'step="1"' in html
    web.post(reverse("sales:edit", args=[doc.pk]), post_data(ctx[1], lines=[line(quantity="5")]))
    assert doc.lines.get().quantity == 5
    assert "5 ×" in web.get(reverse("sales:detail", args=[doc.pk])).content.decode()


@pytest.mark.django_db
def test_legacy_fractional_quantity_asks_to_fix_on_edit(web, ctx):
    doc = draft(ctx)
    doc.lines.update(quantity=Decimal("1.5"))                  # dato antiguo
    r = web.get(reverse("sales:edit", args=[doc.pk]))
    assert 'value="1.5' in r.content.decode()
    r = web.post(reverse("sales:edit", args=[doc.pk]), post_data(ctx[1], lines=[line(quantity="1.5")]))
    assert r.status_code == 200                                # no se guarda hasta corregirla
