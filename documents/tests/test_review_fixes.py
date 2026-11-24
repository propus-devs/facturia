"""Regresiones de la revisión de código de la emisión de facturas (fallos 1-5 y 2 menores)."""
import smtplib
from datetime import date
from decimal import Decimal
from unittest import mock

import pytest
from django.core.exceptions import ValidationError
from django.test import Client as WebClient
from django.urls import reverse

from documents.models import Document, DocumentSend, IssuedDocument
from documents.services import issuing, retention, sending
from documents.tests.factories import CompanyFactory, UserFactory

LINES = [{"description": "A", "quantity": "2", "unit_price": "50", "tax_rate": "21", "discount_pct": "0"}]


@pytest.fixture
def ctx(db):
    user = UserFactory()
    return user, CompanyFactory(client=user.client, is_provider=False, is_customer=True)


@pytest.fixture
def web(ctx):
    c = WebClient(); c.force_login(ctx[0]); return c


def make(ctx, document_type="invoice", approve=True, issue_date=date(2026, 3, 10)):
    user, company = ctx
    d = issuing.create_draft(client=user.client, user=user, company=company, document_type=document_type,
                             issue_date=issue_date, lines=LINES)
    return issuing.approve(d, user) if approve else d


def form_data(company, doc_type, action):
    return {
        "document_type": doc_type, "company": company.pk, "issue_date": "2026-03-11", "payment_method": "transfer",
        "global_discount_pct": "", "withholding_pct": "", "action": action,
        "lines-TOTAL_FORMS": 1, "lines-INITIAL_FORMS": 0, "lines-MIN_NUM_FORMS": 0, "lines-MAX_NUM_FORMS": 1000,
        "lines-0-description": "A", "lines-0-quantity": "1", "lines-0-unit_price": "50",
        "lines-0-tax_rate": "21", "lines-0-discount_pct": "0",
    }


# 1 -------------------------------------------------------------------------------------------
@pytest.mark.django_db
def test_rectification_draft_can_be_saved_and_issued_from_the_form(web, ctx):
    orig = make(ctx)
    rect = issuing.issue_rectification(orig, ctx[0], "Error")
    url = reverse("sales:edit", args=[rect.pk])
    r = web.post(url, form_data(ctx[1], "corrected_invoice", "save"))
    assert r.status_code == 302, r.content.decode()[:400]
    r = web.post(url, form_data(ctx[1], "corrected_invoice", "approve"))
    assert r.status_code == 302
    rect.refresh_from_db()
    assert rect.status == "approved" and rect.document_number == "REC-2026-0001" and rect.total_amount == Decimal("60.50")


@pytest.mark.django_db
def test_edit_cannot_change_the_type_of_a_draft(web, ctx):
    d = make(ctx, approve=False)
    r = web.post(reverse("sales:edit", args=[d.pk]), form_data(ctx[1], "receipt", "save"))
    assert r.status_code == 200                                  # tipo no ofrecido → error de formulario
    d.refresh_from_db()
    assert d.document_type == "invoice"


# 2 -------------------------------------------------------------------------------------------
@pytest.mark.django_db
def test_issued_document_cannot_use_the_old_rectify_flow(web, ctx):
    orig = make(ctx)
    with pytest.raises(ValidationError):
        orig.create_rectification(ctx[0], reason="x")
    sales = reverse("sales:detail", args=[orig.pk])
    r = web.post(reverse("documents:detail", args=[orig.pk]), {"action": "rectify"})
    assert r.status_code == 302 and r["Location"] == sales
    r = web.get(reverse("documents:rectify", args=[orig.pk]))
    assert r.status_code == 302 and r["Location"] == sales
    r = web.post(reverse("documents:rectify", args=[orig.pk]), {"rectification_reason": "x", "document_number": "X"})
    assert r.status_code == 302 and r["Location"] == sales
    orig.refresh_from_db()
    assert orig.is_current and orig.status == "approved"
    assert Document.all_objects.filter(client=orig.client).count() == 1
    html = web.get(reverse("documents:detail", args=[orig.pk])).content.decode()
    assert 'data-action="rectify"' not in html and sales in html


@pytest.mark.django_db
def test_received_documents_still_rectify_with_the_old_flow(web, ctx):
    from documents.tests.factories import DocumentFactory
    doc = DocumentFactory(client=ctx[0].client, status="approved")
    assert doc.create_rectification(ctx[0], reason="x").version == 2


# 3 -------------------------------------------------------------------------------------------
@pytest.mark.django_db
@pytest.mark.parametrize("exc", [smtplib.SMTPAuthenticationError(535, b"bad creds"), OSError("ssl"), FileNotFoundError("pdf")])
def test_unexpected_send_errors_mark_the_send_as_failed(ctx, exc):
    from documents.tasks import send_document_email
    doc = make(ctx)
    send = sending.queue_email(doc, ctx[0], to_email="a@b.com", subject="S", body="B")
    with mock.patch.object(sending, "deliver_email", side_effect=exc), \
         mock.patch.object(send_document_email, "retry") as retry:
        assert send_document_email.run(send.pk, "B") == "failed"
    retry.assert_not_called()
    send.refresh_from_db()
    assert send.status == "failed" and send.error == type(exc).__name__


@pytest.mark.django_db
def test_auth_error_is_failed_not_queued_and_not_retried(ctx):
    from documents.tasks import send_document_email
    send = sending.queue_email(make(ctx), ctx[0], to_email="a@b.com", subject="S", body="B")
    with mock.patch("django.core.mail.EmailMessage.send", side_effect=smtplib.SMTPAuthenticationError(535, b"x")), \
         mock.patch.object(send_document_email, "retry") as retry:
        assert send_document_email.run(send.pk, "B") == "failed"
    retry.assert_not_called()
    send.refresh_from_db()
    assert send.status == "failed" and send.error == "SMTPAuthenticationError" and "a@b.com" not in send.error


@pytest.mark.django_db
def test_missing_pdf_marks_send_failed(ctx):
    from documents.tasks import send_document_email
    doc = make(ctx)
    send = sending.queue_email(doc, ctx[0], to_email="a@b.com", subject="S", body="B")
    doc.file.storage.delete(doc.file.name)
    assert send_document_email.run(send.pk, "B") == "failed"
    assert DocumentSend.objects.get(pk=send.pk).status == "failed"


# 4 -------------------------------------------------------------------------------------------
@pytest.mark.django_db
def test_cannot_void_an_invoice_with_issued_rectifications(ctx):
    user, _ = ctx
    orig = make(ctx)
    rect = issuing.approve(issuing.issue_rectification(orig, user, "abono"), user)
    with pytest.raises(ValidationError, match="rectificativas"):
        issuing.void(orig, user)
    orig.refresh_from_db()
    assert orig.status == "approved"
    issuing.void(rect, user, "deshacer")                          # anular la rectificativa primero
    assert issuing.void(orig, user).status == "voided"


@pytest.mark.django_db
def test_draft_rectifications_do_not_block_voiding(ctx):
    user, _ = ctx
    orig = make(ctx)
    issuing.issue_rectification(orig, user, "borrador")
    assert issuing.void(orig, user).status == "voided"


# 5 -------------------------------------------------------------------------------------------
def _age(*docs, year):
    Document.all_objects.filter(pk__in=[d.pk for d in docs]).update(issue_date=date(year, 1, 5))


@pytest.mark.django_db
def test_purge_keeps_original_until_its_rectification_expires_then_deletes_both(ctx):
    user, _ = ctx
    orig = make(ctx)
    rect = issuing.approve(issuing.issue_rectification(orig, user, "abono"), user)
    today = date(2026, 6, 1)
    _age(orig, year=2000)                                         # original caducada, rectificativa reciente
    assert retention.expired_chains(today=today) == []
    assert retention.purge_expired(apply=True, today=today)["documents"] == 0
    assert Document.all_objects.filter(pk__in=[orig.pk, rect.pk]).count() == 2
    _age(rect, year=2000)                                         # ahora caducan las dos
    result = retention.purge_expired(apply=True, today=today)     # antes: ProtectedError
    assert result["chains"] == 1 and result["documents"] == 2
    assert not Document.all_objects.filter(pk__in=[orig.pk, rect.pk]).exists()


@pytest.mark.django_db
def test_purge_separates_unrelated_issued_documents(ctx):
    a, b = make(ctx), make(ctx)
    _age(a, year=2000)
    assert retention.purge_expired(apply=True, today=date(2026, 6, 1))["documents"] == 1
    assert Document.all_objects.filter(pk=b.pk).exists() and not Document.all_objects.filter(pk=a.pk).exists()


# minor: SMTP/URL en producción ---------------------------------------------------------------------
def test_production_email_config_checks():
    from billing_ai.settings.checks import email_config_problems as problems
    assert problems("smtp.x.com", "", "https://app.example.com") == []
    assert problems("", "django.core.mail.backends.smtp.EmailBackend", "https://app.example.com") == []
    assert any("EMAIL_HOST" in p for p in problems("", "", "https://app.example.com"))
    assert any("PUBLIC_BASE_URL" in p for p in problems("smtp.x.com", "", "http://localhost:8000"))
    assert any("PUBLIC_BASE_URL" in p for p in problems("smtp.x.com", "", "http://127.0.0.1:8000"))
    assert any("PUBLIC_BASE_URL" in p for p in problems("smtp.x.com", "", ""))


# minor: respuesta del cliente pisada por un email retrasado -----------------------------------------------
@pytest.mark.django_db
def test_late_email_does_not_overwrite_customer_response(ctx):
    user, company = ctx
    q = issuing.approve(issuing.create_draft(client=user.client, user=user, company=company, document_type="quote",
                                             lines=LINES), user)
    send = sending.queue_email(q, user, to_email="a@b.com", subject="S", body="B")

    def customer_accepts_meanwhile(*a, **k):
        IssuedDocument.objects.filter(document=q).update(quote_status="accepted")

    with mock.patch("django.core.mail.EmailMessage.send", side_effect=customer_accepts_meanwhile):
        assert sending.deliver_email(send.pk, "B") == "sent"
    q.issued.refresh_from_db()
    assert q.issued.quote_status == "accepted" and q.issued.sent_at


@pytest.mark.django_db
def test_email_still_marks_unsent_quote_as_sent(ctx):
    user, company = ctx
    q = issuing.approve(issuing.create_draft(client=user.client, user=user, company=company, document_type="quote",
                                             lines=LINES), user)
    sending.deliver_email(sending.queue_email(q, user, to_email="a@b.com", subject="S", body="B").pk, "B")
    q.issued.refresh_from_db()
    assert q.issued.quote_status == "sent"
