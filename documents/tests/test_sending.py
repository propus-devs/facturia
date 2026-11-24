"""Fase 4: envío por email de documentos emitidos."""
import smtplib
from datetime import date
from unittest import mock

import pytest
from django.core import mail
from django.core.exceptions import ValidationError
from django.urls import reverse

from documents.models import DocumentSend
from documents.services import issuing, sending
from documents.tests.factories import CompanyFactory, UserFactory

LINE = {"description": "Servicio", "quantity": "1", "unit_price": "100", "tax_rate": "21", "discount_pct": "0"}


@pytest.fixture
def ctx(db):
    user = UserFactory()
    company = CompanyFactory(client=user.client, is_provider=False, is_customer=True, email="cliente@example.com")
    return user, company


def make(ctx, approve=True, **kw):
    user, company = ctx
    doc = issuing.create_draft(client=user.client, user=user, company=company, issue_date=date(2026, 3, 10),
                               document_type=kw.pop("document_type", "invoice"), lines=[LINE], **kw)
    return issuing.approve(doc, user) if approve else doc


def queue(doc, user, **kw):
    kw.setdefault("to_email", "x@example.com")
    kw.setdefault("subject", "Asunto")
    kw.setdefault("body", "Hola")
    return sending.queue_email(doc, user, **kw)


def test_fill_template_ignores_unknown_and_attribute_syntax():
    assert sending.fill_template("{tipo} {x} {0.__class__}", {"tipo": "factura"}) == "factura {x} {0.__class__}"


@pytest.mark.django_db
def test_default_message_uses_settings_template_and_contact_email(ctx):
    doc = make(ctx)
    msg = sending.default_message(doc)
    assert msg["to_email"] == "cliente@example.com"
    assert msg["subject"] == f"factura FAC-2026-0001 de {ctx[0].client.name}"
    assert "121,00 €" in msg["body"]


@pytest.mark.django_db
def test_queue_validates_state_email_and_subject(ctx):
    user, _ = ctx
    with pytest.raises(ValidationError):
        queue(make(ctx, approve=False), user)                       # borrador
    doc = make(ctx)
    with pytest.raises(ValidationError):
        queue(doc, user, to_email="no-es-email")
    with pytest.raises(ValidationError):
        queue(doc, user, subject="   ")
    assert not DocumentSend.objects.exists()


@pytest.mark.django_db
def test_queue_enqueues_after_commit_and_deliver_sends_pdf(ctx, django_capture_on_commit_callbacks):
    user, _ = ctx
    doc = make(ctx)
    with mock.patch("documents.tasks.send_document_email.delay") as delay:
        with django_capture_on_commit_callbacks(execute=True):
            send = queue(doc, user, subject="Tu\nfactura", body="Cuerpo")
        delay.assert_called_once_with(send.pk, "Cuerpo")
    assert send.status == "queued" and send.subject == "Tu factura"        # sin saltos de línea
    assert sending.deliver_email(send.pk, "Cuerpo") == "sent"
    m = mail.outbox[0]
    assert m.to == ["x@example.com"] and m.body == "Cuerpo"
    name, content, mime = m.attachments[0]
    assert name == "FAC-2026-0001.pdf" and content.startswith(b"%PDF-") and mime == "application/pdf"
    send.refresh_from_db(); doc.issued.refresh_from_db()
    assert send.status == "sent" and send.sent_at and doc.issued.sent_at
    assert doc.audit_logs.filter(action="sent").exists()
    assert sending.deliver_email(send.pk, "Cuerpo") == "sent" and len(mail.outbox) == 1   # idempotente


@pytest.mark.django_db
def test_sending_a_quote_marks_it_sent(ctx):
    user, _ = ctx
    doc = make(ctx, document_type="quote")
    sending.deliver_email(queue(doc, user).pk, "x")
    doc.issued.refresh_from_db()
    assert doc.issued.quote_status == "sent"


@pytest.mark.django_db
def test_refused_recipient_marks_failed_without_retry(ctx):
    user, _ = ctx
    send = queue(make(ctx), user)
    with mock.patch("django.core.mail.EmailMessage.send", side_effect=smtplib.SMTPRecipientsRefused({})):
        assert sending.deliver_email(send.pk, "x") == "failed"
    send.refresh_from_db()
    assert send.status == "failed" and send.error and not mail.outbox


@pytest.mark.django_db
def test_task_retries_on_transient_error_and_fails_when_exhausted(ctx, settings):
    from celery.exceptions import MaxRetriesExceededError
    from documents.tasks import send_document_email
    user, _ = ctx
    send = queue(make(ctx), user)
    with mock.patch.object(sending, "deliver_email", side_effect=smtplib.SMTPServerDisconnected()), \
         mock.patch.object(send_document_email, "retry", side_effect=MaxRetriesExceededError()):
        assert send_document_email.run(send.pk, "x") == "failed"
    send.refresh_from_db()
    assert send.status == "failed" and send.error == "SMTPServerDisconnected"


@pytest.mark.django_db
def test_broker_down_marks_send_failed_not_500(ctx, django_capture_on_commit_callbacks):
    user, _ = ctx
    with mock.patch("documents.tasks.send_document_email.delay", side_effect=RuntimeError("redis")):
        with django_capture_on_commit_callbacks(execute=True):
            send = queue(make(ctx), user)
    send.refresh_from_db()
    assert send.status == "failed"


# --- vista ---------------------------------------------------------------------

@pytest.mark.django_db
def test_send_action_in_detail_view_and_tenant_scope(ctx, django_capture_on_commit_callbacks):
    from django.test import Client as WebClient
    web = WebClient(); web.force_login(ctx[0])
    doc = make(ctx)
    url = reverse("sales:detail", args=[doc.pk])
    html = web.get(url).content.decode()
    assert 'id="send-dialog"' in html and "cliente@example.com" in html
    with mock.patch("documents.tasks.send_document_email.delay") as delay:
        with django_capture_on_commit_callbacks(execute=True):
            r = web.post(url, {"action": "send", "to_email": "a@b.com", "subject": "S", "body": "B"})
        assert r.status_code == 302 and delay.called
    assert DocumentSend.objects.get().to_email == "a@b.com"
    web.post(url, {"action": "send", "to_email": "malo", "subject": "S", "body": "B"})   # inválido: no crea
    assert DocumentSend.objects.count() == 1
    other = UserFactory()
    w2 = WebClient(); w2.force_login(other)
    assert w2.post(url, {"action": "send", "to_email": "a@b.com", "subject": "S"}).status_code == 404


@pytest.mark.django_db
def test_draft_page_has_no_send_button(ctx):
    from django.test import Client as WebClient
    web = WebClient(); web.force_login(ctx[0])
    assert 'id="send-dialog"' not in web.get(reverse("sales:detail", args=[make(ctx, approve=False).pk])).content.decode()
