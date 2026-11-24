"""Envío por email de documentos emitidos (PDF adjunto). Sin NIF ni cuerpo en logs: solo ids."""
import logging
import smtplib

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import EmailMessage
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone

from documents.models import DocumentAuditLog, DocumentSend, IssuedDocument
from documents.services.issuing import billing_settings_for

logger = logging.getLogger(__name__)

RETRYABLE = (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError, smtplib.SMTPHeloError,
             smtplib.SMTPDataError, TimeoutError, ConnectionError)
MAX_BODY = 5000


def fill_template(template: str, values: dict) -> str:
    """Sustituye {marcadores} sin `str.format` (la plantilla la edita el usuario)."""
    for key, value in values.items():
        template = template.replace("{" + key + "}", value)
    return template


def _money(value) -> str:
    return f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") + " €"


def default_message(document) -> dict:
    """Destinatario, asunto y cuerpo propuestos a partir de la plantilla de ajustes."""
    bs = billing_settings_for(document.client)
    values = {
        "tipo": document.get_document_type_display().lower(),
        "numero": document.document_number or "",
        "empresa": document.client.name,
        "total": _money(document.total_amount or 0),
    }
    from documents.services.public_quotes import public_url

    link = public_url(document) if document.document_type in ("quote", "proforma") else ""
    values["enlace"] = link
    body = fill_template(bs.email_body, values)
    if link and "{enlace}" not in bs.email_body:
        body += f"\n\nPuedes verlo y aprobarlo online: {link}"
    return {
        "to_email": document.company.email if document.company_id else "",
        "subject": fill_template(bs.email_subject, values),
        "body": body,
    }


def _check_sendable(document):
    if not document.is_issued or document.status != "approved" or not document.file:
        raise ValidationError("Solo se pueden enviar documentos emitidos.")


def queue_email(document, user, *, to_email, subject, body) -> DocumentSend:
    """Valida, registra el envío y lo encola al confirmarse la transacción."""
    from documents.tasks import send_document_email

    _check_sendable(document)
    to_email = (to_email or "").strip()
    validate_email(to_email)
    subject = " ".join((subject or "").split())[:255]
    if not subject:
        raise ValidationError("Indica el asunto.")
    body = (body or "")[:MAX_BODY]

    with transaction.atomic():
        send = DocumentSend.objects.create(
            document=document, channel="email", to_email=to_email, subject=subject, user=user,
        )
        transaction.on_commit(lambda: _enqueue(send_document_email, send.pk, body))
    return send


def _enqueue(task, send_id, body):
    try:
        task.delay(send_id, body)
    except Exception:  # broker caído: no se pierde el registro, queda en cola para reintentar a mano
        logger.error("send %s: broker no disponible", send_id, exc_info=True)
        DocumentSend.objects.filter(pk=send_id, status="queued").update(
            status="failed", error="Cola no disponible"
        )


def deliver_email(send_id, body) -> str:
    """Envía el email. Idempotente: un envío ya `sent` no se repite."""
    send = DocumentSend.objects.select_related("document", "document__issued", "document__client").get(pk=send_id)
    if send.status == "sent":
        return "sent"
    doc = send.document
    issuer_email = (doc.issued.issuer_snapshot or {}).get("email", "")
    message = EmailMessage(
        subject=send.subject, body=body, from_email=settings.DEFAULT_FROM_EMAIL, to=[send.to_email],
        reply_to=[issuer_email] if issuer_email else None,
    )
    with doc.file.open("rb") as fh:
        message.attach(doc.original_name, fh.read(), "application/pdf")
    try:
        message.send(fail_silently=False)
    except smtplib.SMTPRecipientsRefused:
        mark_failed(send_id, "Destinatario rechazado")
        return "failed"

    now = timezone.now()
    with transaction.atomic():
        send.status, send.sent_at, send.error = "sent", now, ""
        send.save(update_fields=["status", "sent_at", "error"])
        # Se relee con bloqueo: durante el envío (o sus reintentos) el cliente pudo responder desde
        # el enlace y no se debe pisar su aceptación/rechazo con un estado antiguo.
        issued = IssuedDocument.objects.select_for_update().get(pk=doc.issued.pk)
        fields = ["sent_at"]
        issued.sent_at = now
        if issued.quote_status == "draft":
            issued.quote_status = "sent"
            fields.append("quote_status")
        issued.save(update_fields=fields)
        DocumentAuditLog(document=doc, user=send.user, action="sent", changes={"channel": "email"}).save()
    logger.info("send=%s document=%s outcome=sent", send.pk, doc.pk)
    return "sent"


def mark_failed(send_id, reason) -> None:
    DocumentSend.objects.filter(pk=send_id).exclude(status="sent").update(
        status="failed", error=str(reason)[:255]
    )
    logger.warning("send=%s outcome=failed", send_id)


def share_link(document, user) -> str:
    """Activa el enlace público de un presupuesto sin enviar email (lo copia el usuario)."""
    from documents.services.public_quotes import public_url

    _check_sendable(document)
    if document.document_type not in ("quote", "proforma") or not document.issued.public_token:
        raise ValidationError("Solo los presupuestos tienen enlace público.")
    now = timezone.now()
    with transaction.atomic():
        issued = IssuedDocument.objects.select_for_update().get(pk=document.issued.pk)   # estado actual, no el cargado antes
        if issued.quote_status in ("declined", "converted"):
            raise ValidationError("Este presupuesto ya no admite respuesta.")
        if issued.quote_status == "draft":
            issued.quote_status = "sent"
        issued.sent_at = issued.sent_at or now
        issued.save(update_fields=["quote_status", "sent_at"])
        DocumentSend.objects.create(document=document, channel="link", status="sent", sent_at=now, user=user)
        DocumentAuditLog(document=document, user=user, action="sent", changes={"channel": "link"}).save()
    return public_url(document)
