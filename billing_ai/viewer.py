"""Visor a pantalla completa de archivos privados (documentos, recibos).

Se muestra DENTRO de la app (con cabecera y botón «Volver»), en vez de abrir el archivo suelto en el
navegador: en la PWA instalada un PDF/imagen a pantalla completa no tiene botón atrás y obligaba a cerrar
la aplicación. El archivo se sigue sirviendo por la vista autenticada de cada app (sin URL pública).
"""
import os

from django.shortcuts import render

PDF_EXTENSIONS = {".pdf"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def file_kind(name):
    """'pdf', 'image' u 'other' según la extensión (lo que el visor sabe dibujar)."""
    ext = os.path.splitext(name or "")[1].lower()
    if ext in PDF_EXTENSIONS:
        return "pdf"
    if ext in IMAGE_EXTENSIONS:
        return "image"
    return "other"


def render_file_viewer(request, *, title, file_url, kind, back_url):
    return render(request, "private/file_viewer.html", {
        "title": title,
        "file_url": file_url,
        "kind": kind,
        "is_pdf": kind == "pdf",
        "back_url": back_url,
    })
