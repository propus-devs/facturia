"""Enlace público de un presupuesto: ver, aceptar, rechazar y comentar sin iniciar sesión.

Seguridad: el token (`secrets.token_urlsafe(32)`) es el único acceso; solo es visible tras enviarse
(`quote_status != draft`), nunca si está anulado, y solo da acceso a ESE documento."""
from datetime import date

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from documents.models import Document, DocumentAuditLog

MAX_COMMENT = 2000
SHARED_STATES = ("sent", "accepted", "declined", "converted")


def public_url(document) -> str:
    token = document.issued.public_token
    return f"{settings.PUBLIC_BASE_URL}{reverse('public_quote', args=[token])}" if token else ""


def get_by_token(token):
    """Presupuesto visible por enlace, o None (token desconocido, sin enviar o anulado)."""
    if not token or len(token) > 64:
        return None
    return (
        Document.all_objects.select_related("company", "client", "issued")
        .filter(
            issued__public_token=token, source="app", status="approved",
            document_type__in=("quote", "proforma"), issued__quote_status__in=SHARED_STATES,
        )
        .first()
    )


def is_expired(document, today: date | None = None) -> bool:
    valid_until = document.issued.valid_until
    return bool(valid_until and valid_until < (today or timezone.localdate()))


def can_respond(document) -> bool:
    return document.issued.quote_status == "sent" and not is_expired(document)


def throttled(ip: str, token: str) -> bool:
    """Límite sencillo de respuestas por IP, enlace y hora (anti-abuso). Se incluye el token porque
    tras un proxy la IP puede ser común a todos los visitantes."""
    key = f"public-quote:{token[:16]}:{ip}:{timezone.now():%Y%m%d%H}"
    cache.add(key, 0, 3600)
    try:
        count = cache.incr(key)
    except ValueError:
        return False
    return count > settings.PUBLIC_RESPONSE_LIMIT_PER_HOUR


def respond(token, action, comment="") -> tuple[str, "Document"]:
    """Aplica la respuesta del cliente. `action`: accept | decline | comment."""
    comment = (comment or "").strip()[:MAX_COMMENT]
    with transaction.atomic():
        found = get_by_token(token)
        if found is None:
            raise ValidationError("Enlace no válido.")
        # of=("self",): sin él, PostgreSQL falla («FOR UPDATE cannot be applied to the nullable side
        # of an outer join») porque `issued` es una relación inversa (LEFT JOIN). SQLite lo ignora.
        doc = Document.all_objects.select_for_update(of=("self",)).select_related("issued").get(pk=found.pk)
        issued = doc.issued
        if not can_respond(doc):
            raise ValidationError(
                "Este presupuesto ha caducado." if is_expired(doc) and issued.quote_status == "sent"
                else "Este presupuesto ya tiene respuesta."
            )
        stamp = timezone.now().strftime("%d/%m/%Y %H:%M")
        if comment:
            issued.customer_comment = (issued.customer_comment + f"\n[{stamp}] " + comment).strip()
        if action == "accept":
            issued.quote_status, issued.accepted_at, log = "accepted", timezone.now(), "accepted"
        elif action == "decline":
            issued.quote_status, log = "declined", "declined"
        elif action == "comment":
            if not comment:
                raise ValidationError("Escribe un comentario.")
            log = "commented"
        else:
            raise ValidationError("Acción no válida.")
        issued.save(update_fields=["customer_comment", "quote_status", "accepted_at"])
        DocumentAuditLog(document=doc, user=None, action=log, changes={"via": "public_link"}).save()
    return log, doc
