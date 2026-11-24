"""Página pública de un presupuesto (sin sesión): ver, aceptar, rechazar, comentar y descargar el PDF."""
from django.core.exceptions import ValidationError
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.clickjacking import xframe_options_sameorigin

from billing_ai.storage import private_file_response
from documents.services import public_quotes

RESULTS = {"accept": "accepted", "decline": "declined", "comment": "commented"}


def _harden(response):
    # same-origin y NO no-referrer: con no-referrer el navegador envía `Origin: null` en los POST
    # y CsrfViewMiddleware los rechaza. same-origin no filtra la URL (con el token) a terceros.
    response["Referrer-Policy"] = "same-origin"
    response["X-Robots-Tag"] = "noindex, nofollow"
    response["Cache-Control"] = "private, no-store"
    return response


def _doc_or_404(token):
    doc = public_quotes.get_by_token(token)
    if doc is None:
        raise Http404("Enlace no válido")
    return doc


class PublicQuoteView(View):
    template_name = "public/quote.html"

    def _render(self, request, doc, *, error="", status=200):
        issued = doc.issued
        tax_lines = list(doc.tax_lines.all())
        response = render(request, self.template_name, {
            "document": doc, "issued": issued, "issuer": issued.issuer_snapshot,
            "recipient": issued.recipient_snapshot, "lines": doc.lines.all(),
            "vat_lines": [t for t in tax_lines if t.kind == "vat"],
            "surcharge_lines": [t for t in tax_lines if t.kind == "surcharge"],
            "withholding_lines": [t for t in tax_lines if t.kind == "withholding"],
            "token": request.resolver_match.kwargs["token"],
            "can_respond": public_quotes.can_respond(doc),
            "expired": issued.quote_status == "sent" and public_quotes.is_expired(doc),
            "quote_status": issued.quote_status,
            "result": request.GET.get("r") if request.GET.get("r") in RESULTS.values() else "",
            "error": error,
        }, status=status)
        return _harden(response)

    def get(self, request, token):
        return self._render(request, _doc_or_404(token))

    def post(self, request, token):
        doc = _doc_or_404(token)
        if public_quotes.throttled(request.META.get("REMOTE_ADDR", ""), token):
            return _harden(HttpResponse("Demasiados intentos. Inténtalo más tarde.", status=429))
        action = request.POST.get("action", "")
        try:
            public_quotes.respond(token, action, request.POST.get("comment", ""))
        except ValidationError as exc:
            return self._render(request, _doc_or_404(token), error=" ".join(exc.messages), status=400)
        return _harden(redirect(f"{request.path}?r={RESULTS[action]}"))


@method_decorator(xframe_options_sameorigin, name="dispatch")
class PublicQuotePDFView(View):
    def get(self, request, token):
        doc = _doc_or_404(token)
        response = private_file_response(doc.file, download_name=doc.original_name)
        if request.GET.get("download"):
            response["Content-Disposition"] = response["Content-Disposition"].replace("inline", "attachment", 1)
        return _harden(response)
