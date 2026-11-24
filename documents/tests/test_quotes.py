"""Fase 5: presupuestos — enlace público, respuesta del cliente, conversión a factura."""
from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import Client as WebClient
from django.urls import reverse
from django.utils import timezone

from documents.models import Document
from documents.services import issuing, public_quotes, sending
from documents.tests.factories import ClientFactory, CompanyFactory, UserFactory

LINE = {"description": "Diseño web", "quantity": "2", "unit_price": "50", "tax_rate": "21", "discount_pct": "10"}


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()


@pytest.fixture
def ctx(db):
    user = UserFactory()
    return user, CompanyFactory(client=user.client, is_provider=False, is_customer=True, email="c@example.com")


def quote(ctx, *, shared=True, **kw):
    user, company = ctx
    doc = issuing.create_draft(client=user.client, user=user, company=company, document_type="quote",
                               issue_date=timezone.localdate(), lines=[LINE], notes="Notas", **kw)
    doc = issuing.approve(doc, user)
    if shared:
        sending.share_link(doc, user)
        doc.issued.refresh_from_db()
    return doc


def url(doc, suffix=""):
    return reverse("public_quote", args=[doc.issued.public_token]) + suffix


# --- token y acceso ---------------------------------------------------------------

@pytest.mark.django_db
def test_token_created_on_approve_only_for_quotes(ctx):
    user, company = ctx
    q = quote(ctx, shared=False)
    inv = issuing.approve(issuing.create_draft(client=user.client, user=user, company=company,
                                               document_type="invoice", lines=[LINE]), user)
    assert len(q.issued.public_token) >= 40 and inv.issued.public_token is None
    assert issuing.create_draft(client=user.client, user=user, document_type="quote", lines=[LINE]).issued.public_token is None


@pytest.mark.django_db
def test_link_not_visible_until_shared_and_never_when_voided(ctx):
    q = quote(ctx, shared=False)
    web = WebClient()
    assert web.get(url(q)).status_code == 404                    # sin enviar
    sending.share_link(q, ctx[0])
    r = web.get(url(q))
    assert r.status_code == 200 and "Diseño web" in r.content.decode()
    issuing.void(q, ctx[0])
    assert web.get(url(q)).status_code == 404                    # anulado


@pytest.mark.django_db
def test_unknown_token_and_invoice_token_404(ctx):
    web = WebClient()
    assert web.get(reverse("public_quote", args=["x" * 43])).status_code == 404
    assert web.get(reverse("public_quote", args=["x" * 90])).status_code == 404
    assert web.get(reverse("public_quote_pdf", args=["nope"])).status_code == 404


@pytest.mark.django_db
def test_public_page_headers_and_pdf(ctx):
    q = quote(ctx)
    web = WebClient()
    r = web.get(url(q))
    assert r["X-Robots-Tag"].startswith("noindex") and r["Referrer-Policy"] == "same-origin"
    assert "no-store" in r["Cache-Control"]
    pdf = web.get(reverse("public_quote_pdf", args=[q.issued.public_token]) + "?download=1")
    assert pdf["Content-Disposition"].startswith("attachment") and b"".join(pdf.streaming_content).startswith(b"%PDF-")


@pytest.mark.django_db
def test_public_page_does_not_leak_other_data(ctx):
    other = UserFactory()
    secret = CompanyFactory(client=other.client, name="Secreta Ajena SL")
    issuing.approve(issuing.create_draft(client=other.client, user=other, company=secret, document_type="quote",
                                         lines=[LINE]), other)
    assert "Secreta Ajena" not in WebClient().get(url(quote(ctx))).content.decode()


# --- respuesta del cliente -----------------------------------------------------------

@pytest.mark.django_db
def test_accept_with_comment(ctx):
    q = quote(ctx)
    r = WebClient().post(url(q), {"action": "accept", "comment": "Perfecto <b>gracias</b>"})
    assert r.status_code == 302 and r["Location"].endswith("?r=accepted")
    q.issued.refresh_from_db()
    assert q.issued.quote_status == "accepted" and q.issued.accepted_at
    assert "Perfecto" in q.issued.customer_comment
    log = q.audit_logs.get(action="accepted")
    assert log.user is None
    html = WebClient().get(url(q)).content.decode()
    assert "<b>gracias</b>" not in html                              # escapado


@pytest.mark.django_db
def test_decline_and_comment_only(ctx):
    q = quote(ctx)
    WebClient().post(url(q), {"action": "comment", "comment": "¿Podéis bajar el precio?"})
    q.issued.refresh_from_db()
    assert q.issued.quote_status == "sent" and "bajar" in q.issued.customer_comment      # sigue abierto
    WebClient().post(url(q), {"action": "decline"})
    q.issued.refresh_from_db()
    assert q.issued.quote_status == "declined" and q.audit_logs.filter(action="declined").exists()


@pytest.mark.django_db
def test_cannot_respond_twice_when_expired_or_empty_comment(ctx):
    q = quote(ctx)
    assert WebClient().post(url(q), {"action": "comment", "comment": "  "}).status_code == 400
    WebClient().post(url(q), {"action": "accept"})
    r = WebClient().post(url(q), {"action": "decline"})
    assert r.status_code == 400
    q.issued.refresh_from_db()
    assert q.issued.quote_status == "accepted"
    old = quote(ctx, valid_until=date(2020, 1, 1))
    assert old.issued.valid_until < timezone.localdate()
    r = WebClient().post(url(old), {"action": "accept"})
    assert r.status_code == 400 and "caducado" in r.content.decode()
    old.issued.refresh_from_db()
    assert old.issued.quote_status == "sent"


@pytest.mark.django_db
def test_invalid_action_and_long_comment_truncated(ctx):
    q = quote(ctx)
    with pytest.raises(ValidationError):
        public_quotes.respond(q.issued.public_token, "hack", "x")
    public_quotes.respond(q.issued.public_token, "comment", "a" * 5000)
    q.issued.refresh_from_db()
    assert len(q.issued.customer_comment) < 2100


@pytest.mark.django_db
def test_throttle(ctx, settings):
    settings.PUBLIC_RESPONSE_LIMIT_PER_HOUR = 2
    q = quote(ctx)
    web = WebClient()
    codes = [web.post(url(q), {"action": "comment", "comment": f"c{i}"}).status_code for i in range(4)]
    assert codes[:2] == [302, 302] and codes[2:] == [429, 429]


@pytest.mark.django_db
def test_get_page_states(ctx):
    q = quote(ctx)
    assert "Aceptar presupuesto" in WebClient().get(url(q)).content.decode()
    WebClient().post(url(q), {"action": "accept"})
    html = WebClient().get(url(q) + "?r=accepted").content.decode()
    assert "Aceptar presupuesto" not in html


# --- conversión ------------------------------------------------------------------------

@pytest.mark.django_db
def test_convert_copies_everything_and_blocks_repeat(ctx):
    user, company = ctx
    q = quote(ctx, withholding_pct=Decimal("15"), global_discount_pct=Decimal("5"))
    inv = issuing.convert_quote_to_invoice(q, user)
    assert inv.document_type == "invoice" and inv.status == "draft" and not inv.document_number
    assert inv.company == company and inv.client == user.client
    assert (inv.base_amount, inv.total_amount) == (q.base_amount, q.total_amount)
    assert [(l.description, l.quantity, l.unit_price, l.tax_rate, l.discount_pct) for l in inv.lines.all()] == \
           [(l.description, l.quantity, l.unit_price, l.tax_rate, l.discount_pct) for l in q.lines.all()]
    assert inv.issued.withholding_pct == Decimal("15") and inv.issued.notes == "Notas"
    q.issued.refresh_from_db()
    assert q.issued.quote_status == "converted" and q.issued.converted_to == inv
    assert q.audit_logs.filter(action="converted").exists()
    with pytest.raises(ValidationError):
        issuing.convert_quote_to_invoice(q, user)
    assert issuing.approve(inv, user).document_number == "FAC-%d-0001" % inv.issue_date.year


@pytest.mark.django_db
def test_convert_rejects_declined_draft_and_non_quotes(ctx):
    user, company = ctx
    q = quote(ctx)
    WebClient().post(url(q), {"action": "decline"})
    with pytest.raises(ValidationError):
        issuing.convert_quote_to_invoice(q, user)
    draft = issuing.create_draft(client=user.client, user=user, company=company, document_type="quote", lines=[LINE])
    with pytest.raises(ValidationError):
        issuing.convert_quote_to_invoice(draft, user)
    inv = issuing.approve(issuing.create_draft(client=user.client, user=user, company=company,
                                               document_type="invoice", lines=[LINE]), user)
    with pytest.raises(ValidationError):
        issuing.convert_quote_to_invoice(inv, user)


@pytest.mark.django_db
def test_convert_after_acceptance_allowed(ctx):
    q = quote(ctx)
    WebClient().post(url(q), {"action": "accept"})
    assert issuing.convert_quote_to_invoice(q, ctx[0]).status == "draft"


# --- vistas internas -------------------------------------------------------------------

@pytest.fixture
def web(ctx):
    c = WebClient()
    c.force_login(ctx[0])
    return c


@pytest.mark.django_db
def test_detail_share_and_convert_actions(web, ctx):
    q = quote(ctx, shared=False)
    d = reverse("sales:detail", args=[q.pk])
    assert web.get(d).context["public_url"] == ""                            # aún no compartido
    web.post(d, {"action": "share"})
    q.issued.refresh_from_db()
    assert q.issued.quote_status == "sent"
    assert web.get(d).context["public_url"].endswith(f"/p/{q.issued.public_token}/")
    r = web.post(d, {"action": "convert"})
    inv = Document.all_objects.get(document_type="invoice")
    assert r.status_code == 302 and r["Location"] == reverse("sales:edit", args=[inv.pk])


@pytest.mark.django_db
def test_other_tenant_cannot_share_or_convert(ctx):
    q = quote(ctx, shared=False)
    w2 = WebClient(); w2.force_login(UserFactory())
    d = reverse("sales:detail", args=[q.pk])
    assert w2.post(d, {"action": "share"}).status_code == 404
    assert w2.post(d, {"action": "convert"}).status_code == 404
    assert not Document.all_objects.filter(document_type="invoice").exists()


@pytest.mark.django_db
def test_quote_email_contains_public_link(ctx):
    q = quote(ctx, shared=False)
    msg = sending.default_message(q)
    assert q.issued.public_token in msg["body"]
    assert "http" not in sending.default_message(
        issuing.approve(issuing.create_draft(client=ctx[0].client, user=ctx[0], company=ctx[1],
                                             document_type="invoice", lines=[LINE]), ctx[0]))["body"]


@pytest.mark.django_db
def test_public_form_works_with_csrf_enforced_and_browser_origin(ctx):
    """El cliente de pruebas no aplica CSRF por defecto: aquí sí, como el navegador."""
    import re
    q = quote(ctx)
    web = WebClient(enforce_csrf_checks=True)
    page = web.get(url(q))
    token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', page.content.decode()).group(1)
    assert "csrftoken" in web.cookies                                   # la página siembra la cookie
    ok = web.post(url(q), {"action": "accept", "csrfmiddlewaretoken": token}, HTTP_ORIGIN="http://testserver")
    assert ok.status_code == 302
    q.issued.refresh_from_db()
    assert q.issued.quote_status == "accepted"
    # sin token → 403; y `Origin: null` (lo que enviaría el navegador con no-referrer) también
    assert web.post(url(q), {"action": "decline"}).status_code == 403
    assert web.post(url(q), {"action": "decline", "csrfmiddlewaretoken": token}, HTTP_ORIGIN="null").status_code == 403


@pytest.mark.django_db
def test_respond_service_locks_row_without_outer_join_error(ctx):
    """Regresión: select_for_update + select_related de la relación inversa fallaba en PostgreSQL."""
    q = quote(ctx)
    log, doc = public_quotes.respond(q.issued.public_token, "accept", "ok")
    assert log == "accepted" and doc.issued.quote_status == "accepted"
