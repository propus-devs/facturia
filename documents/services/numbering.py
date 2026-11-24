"""Numeración correlativa por cliente, tipo y año (sin huecos: se asigna dentro de la transacción)."""
from django.db import transaction

from documents.models import NumberSequence

KIND_BY_TYPE = {
    "invoice": "invoice",
    "corrected_invoice": "corrected",
    "receipt": "receipt",
    "quote": "quote",
    "proforma": "proforma",
}
DEFAULT_PREFIX = {
    "invoice": "FAC-", "corrected": "REC-", "receipt": "TIC-", "quote": "PRE-", "proforma": "PRO-",
}


def next_number(client, document_type: str, issue_date) -> str:
    """Reserva y devuelve el siguiente número (p. ej. «FAC-2026-0001»).

    Debe llamarse dentro de la misma transacción que guarda el documento: si esta falla, el
    contador se revierte y no queda hueco. Los presupuestos tienen su propia serie."""
    kind = KIND_BY_TYPE[document_type]
    with transaction.atomic():
        seq, _ = NumberSequence.objects.get_or_create(
            client=client, kind=kind, prefix=DEFAULT_PREFIX[kind], year=issue_date.year
        )
        seq = NumberSequence.objects.select_for_update().get(pk=seq.pk)
        number = seq.format(seq.next_number)
        seq.next_number += 1
        seq.save(update_fields=["next_number"])
    return number
