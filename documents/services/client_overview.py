"""Panorama de clientes para superusuarios: actividad, confianza de extracción y coste.

Cruza tenants a propósito: solo debe mostrarse a superusuarios. No expone datos contables de los
clientes (importes de facturas, gasto, ingresos): solo uso de la plataforma.
"""
from decimal import Decimal

from django.conf import settings
from django.db.models import Avg, Count, Q, Sum

from clients.models import Client
from documents.models import Document, ExtractionRun

ZERO = Decimal("0")
TOP_N = 5

# clave de orden → (campo de la fila, descendente por defecto)
SORTS = {
    "documents": ("documents", True),
    "confidence": ("confidence", False),   # primero los de menor confianza
    "cost": ("cost_usd", True),
    "name": ("name", False),
}
DEFAULT_SORT = "documents"

# (clave de orden o "", etiqueta, columna numérica)
COLUMNS = [
    ("name", "Cliente", False), ("documents", "Docs", True), ("confidence", "Confianza", True),
    ("", "Baja conf.", True), ("", "Auto", True), ("", "Fallos", True), ("", "Respaldo", True), ("cost", "Coste", True),
    ("", "Peso", False),
]


def confidence_tone(value):
    """Color de la confianza (%): verde ≥ 90, ámbar sobre el umbral de respaldo, rojo por debajo."""
    if value is None:
        return "info"
    if value >= 90:
        return "success"
    return "warning" if value >= settings.EXTRACTION_FALLBACK_CONFIDENCE * 100 else "danger"


def _documents():
    """Documentos vigentes del flujo de revisión de todos los clientes (incluye archivados)."""
    return Document.all_objects.filter(is_current=True).exclude(status__in=("draft", "voided"))


def _by_client(rows):
    return {r.pop("client_id"): r for r in rows}


def _activity(start, end):
    low = settings.EXTRACTION_FALLBACK_CONFIDENCE
    extracted = ~Q(source="app")
    qs = _documents().filter(created_at__date__range=(start, end))
    return _by_client(
        qs.values("client_id").annotate(
            documents=Count("id"),
            confidence=Avg("confidence_global", filter=extracted),
            confidence_docs=Count("id", filter=extracted & Q(confidence_global__isnull=False)),
            low_confidence=Count("id", filter=extracted & Q(confidence_global__lt=low)),
            failed=Count("id", filter=Q(processing_status="failed")),
            pending=Count("id", filter=Q(status="pending")),
            auto_approved=Count("id", filter=Q(status="approved", is_auto_approved=True)),
        )
    )


def _runs(start, end):
    qs = ExtractionRun.objects.filter(created_at__date__range=(start, end))
    return _by_client(
        qs.values("client_id").annotate(
            run_documents=Count("document_id", distinct=True),
            runs=Count("id"),
            failed_runs=Count("id", filter=Q(status="failed")),
            fallback_documents=Count("document_id", distinct=True, filter=Q(is_fallback=True)),
            cost_usd=Sum("cost_usd"),
        )
    )


def _rate(part, whole):
    return round(part * 100 / whole, 1) if whole else None


def _row(client, act, runs):
    documents = act.get("documents", 0)
    cost = runs.get("cost_usd") or ZERO
    run_documents = runs.get("run_documents", 0)
    confidence = act.get("confidence")
    confidence = round(float(confidence) * 100, 1) if confidence is not None else None
    return {
        "client_id": client["id"],
        "name": client["name"],
        "is_active": client["is_active"],
        "documents": documents,
        "confidence": confidence,
        "confidence_tone": confidence_tone(confidence),
        "confidence_docs": act.get("confidence_docs", 0),
        "low_confidence_pct": _rate(act.get("low_confidence", 0), act.get("confidence_docs", 0)),
        "auto_pct": _rate(act.get("auto_approved", 0), documents),
        "failed": act.get("failed", 0),
        "pending": act.get("pending", 0),
        "runs": runs.get("runs", 0),
        "fallback_pct": _rate(runs.get("fallback_documents", 0), run_documents),
        "cost_usd": cost,
        "cost_per_document": cost / run_documents if run_documents else ZERO,
    }


def _sort(rows, sort):
    field, desc = SORTS.get(sort, SORTS[DEFAULT_SORT])
    if field == "name":
        return sorted(rows, key=lambda r: r["name"].lower())
    present = [r for r in rows if r[field] is not None]
    missing = [r for r in rows if r[field] is None]          # sin dato → siempre al final
    present.sort(key=lambda r: (r[field], r["name"].lower()), reverse=desc)
    return present + missing


def _ranking(rows, field, *, desc=True, keep=lambda r: True, percent=False):
    pool = [r for r in rows if r[field] is not None and keep(r)]
    pool.sort(key=lambda r: (r[field], r["name"].lower()), reverse=desc)
    pool = pool[:TOP_N]
    top = 100 if percent else max((abs(r[field]) for r in pool), default=0)   # % absolutos o relativos al primero
    return [{**r, "value": r[field], "pct": round(abs(r[field]) * 100 / top) if top else 0} for r in pool]


def client_overview(start, end, sort=DEFAULT_SORT):
    """Tabla por cliente, rankings y totales del periodo [start, end] (fechas inclusivas)."""
    act, runs = _activity(start, end), _runs(start, end)
    clients = Client.objects.values("id", "name", "is_active")
    rows = [_row(c, act.get(c["id"], {}), runs.get(c["id"], {})) for c in clients]

    weighted = sum((r["confidence"] or 0) * r["confidence_docs"] for r in rows)
    confidence_docs = sum(r["confidence_docs"] for r in rows)
    total_cost = sum((r["cost_usd"] for r in rows), ZERO)
    for r in rows:
        r["cost_share"] = float(r["cost_usd"] * 100 / total_cost) if total_cost else 0

    totals = {
        "clients": len(rows),
        "active_clients": sum(1 for r in rows if r["documents"]),
        "documents": sum(r["documents"] for r in rows),
        "failed": sum(r["failed"] for r in rows),
        "cost_usd": total_cost,
        "confidence": round(weighted / confidence_docs, 1) if confidence_docs else None,
    }
    totals["confidence_tone"] = confidence_tone(totals["confidence"])
    rankings = {
        "documents": _ranking(rows, "documents", keep=lambda r: r["documents"] > 0),
        "cost": _ranking(rows, "cost_usd", keep=lambda r: r["cost_usd"] > 0),
        "low_confidence": _ranking(rows, "confidence", desc=False, percent=True,
                                  keep=lambda r: r["confidence_docs"] > 0),
    }
    return {"rows": _sort(rows, sort), "totals": totals, "rankings": rankings, "columns": COLUMNS,
            "low_threshold": round(settings.EXTRACTION_FALLBACK_CONFIDENCE * 100),
            "sort": sort if sort in SORTS else DEFAULT_SORT}
