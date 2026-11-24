import logging

from celery import shared_task
from celery.exceptions import MaxRetriesExceededError, SoftTimeLimitExceeded
from django.conf import settings

from documents.services.extraction import ExtractionError, ProviderTimeoutError
from documents.services import processing

logger = logging.getLogger(__name__)


@shared_task(bind=True, name="documents.process_document")
def process_document(self, document_id):
    """Preprocesa, extrae y aplica el resultado. Idempotente y con reintentos con backoff."""
    try:
        return processing.run_processing(document_id)
    except SoftTimeLimitExceeded:
        exc = ProviderTimeoutError("Límite de tiempo de la tarea")
        return _retry(self, document_id, exc)
    except ExtractionError as exc:  # solo llegan los reintentables
        return _retry(self, document_id, exc)


def _retry(task, document_id, exc):
    delay = processing.retry_delay(task.request.retries, exc)
    logger.info("document %s: reintento %s en %ss (%s)", document_id, task.request.retries + 1, delay, exc.code)
    try:
        # Sin `exc=`: así Celery lanza MaxRetriesExceededError al agotar los reintentos.
        raise task.retry(countdown=delay, max_retries=settings.EXTRACTION_MAX_RETRIES)
    except MaxRetriesExceededError:
        processing.mark_failed_after_retries(document_id, exc)
        return "failed"


@shared_task(bind=True, name="documents.send_document_email")
def send_document_email(self, send_id, body):
    """Envía un documento emitido por email; reintenta con backoff si el SMTP falla."""
    from documents.services import sending

    try:
        return sending.deliver_email(send_id, body)
    except sending.RETRYABLE as exc:
        delay = settings.EMAIL_RETRY_BASE_SECONDS * (2 ** self.request.retries)
        try:
            raise self.retry(countdown=delay, max_retries=settings.EMAIL_MAX_RETRIES)
        except MaxRetriesExceededError:
            sending.mark_failed(send_id, type(exc).__name__)
            return "failed"
    except Exception as exc:  # noqa: BLE001  (auth SMTP, SSL, PDF ausente…: no se reintenta y no queda «en cola»)
        # Solo el tipo: el mensaje de un error SMTP puede incluir direcciones de correo.
        logger.error("send %s: fallo no reintentable (%s)", send_id, type(exc).__name__)
        sending.mark_failed(send_id, type(exc).__name__)
        return "failed"
