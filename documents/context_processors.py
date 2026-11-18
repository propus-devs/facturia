from documents.models import Document


def sidebar_counts(request):
    """Documentos pendientes de revisión del cliente (badge del menú lateral)."""
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated or not getattr(user, "client_id", None):
        return {}
    pending = Document.objects.filter(
        client_id=user.client_id, status="pending", processing_status="processed"
    ).count()
    return {"sidebar_pending": pending}
