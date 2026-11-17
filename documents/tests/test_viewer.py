"""Visor a pantalla completa de documentos (dentro de la app, con botón «Volver») y su plantilla compartida."""
import pytest
from django.apps import apps
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from billing_ai.viewer import file_kind


@pytest.fixture
def other_document(db, other_client_entity):
    Document = apps.get_model("documents", "Document")
    return Document.all_objects.create(
        client=other_client_entity,
        external_id="other-view-001",
        original_name="secret.pdf",
        file=SimpleUploadedFile("secret.pdf", b"%PDF-1.4 other tenant"),
        document_type="invoice",
        document_number="X-9",
        confidence={"confianza_extraccion": 0.5},
        status="pending",
        review_level="required",
    )


@pytest.mark.parametrize("name, kind", [
    ("factura.pdf", "pdf"), ("FACTURA.PDF", "pdf"),
    ("foto.jpg", "image"), ("foto.JPEG", "image"), ("scan.png", "image"), ("a.webp", "image"),
    ("hoja.xlsx", "other"), ("sin_extension", "other"), ("", "other"), (None, "other"),
])
def test_file_kind(name, kind):
    assert file_kind(name) == kind


@pytest.mark.django_db
class TestDocumentViewer:
    def test_requires_login(self, client, document):
        resp = client.get(reverse("documents:viewer", args=[document.pk]))
        assert resp.status_code == 302
        assert "/login/" in resp["Location"]

    def test_owner_sees_viewer_with_back_button(self, auth_client, document):
        resp = auth_client.get(reverse("documents:viewer", args=[document.pk]))
        assert resp.status_code == 200
        html = resp.content.decode()
        assert 'data-file-type="pdf"' in html
        assert f'data-file-url="{reverse("documents:file", args=[document.pk])}"' in html
        # botón «Volver»: enlace al detalle que history-back.js convierte en «atrás» del historial
        assert f'href="{reverse("documents:detail", args=[document.pk])}" data-back' in html
        # zoom y giro disponibles (también para imágenes), y paginación solo en PDF
        assert 'id="zoom-in"' in html and 'id="rotate-left"' in html and 'id="rotate-right"' in html
        assert 'id="next-page"' in html

    def test_image_document_has_no_page_controls_but_can_rotate(self, auth_client, document):
        type(document).all_objects.filter(pk=document.pk).update(file="1/documents/foto.jpg")
        html = auth_client.get(reverse("documents:viewer", args=[document.pk])).content.decode()
        assert 'data-file-type="image"' in html
        assert 'id="rotate-right"' in html
        assert 'id="next-page"' not in html

    def test_unsupported_type_offers_download(self, auth_client, document):
        type(document).all_objects.filter(pk=document.pk).update(file="1/documents/hoja.xlsx")
        html = auth_client.get(reverse("documents:viewer", args=[document.pk])).content.decode()
        assert "No se puede visualizar" in html
        assert 'id="zoom-in"' not in html

    def test_other_tenant_gets_404(self, auth_client, other_document):
        resp = auth_client.get(reverse("documents:viewer", args=[other_document.pk]))
        assert resp.status_code == 404

    def test_archived_document_is_viewable(self, auth_client, document):
        type(document).all_objects.filter(pk=document.pk).update(is_archived=True)
        resp = auth_client.get(reverse("documents:viewer", args=[document.pk]))
        assert resp.status_code == 200


@pytest.mark.django_db
class TestViewerLinks:
    def test_detail_links_to_fullscreen_viewer_and_back_uses_history(self, auth_client, document):
        html = auth_client.get(reverse("documents:detail", args=[document.pk])).content.decode()
        assert reverse("documents:viewer", args=[document.pk]) in html
        assert 'id="rotate-left"' in html
        assert "data-back" in html
        # el archivo suelto ya no se abre en una pestaña nueva (en la PWA no tiene botón atrás)
        assert 'target="_blank"' not in html

    def test_list_filename_opens_in_app_viewer(self, auth_client, document):
        html = auth_client.get(reverse("documents:list")).content.decode()
        assert reverse("documents:viewer", args=[document.pk]) in html
        assert f'href="{reverse("documents:file", args=[document.pk])}"' not in html

    def test_rectify_page_uses_shared_viewer(self, auth_client, approved_document):
        html = auth_client.get(reverse("documents:rectify", args=[approved_document.pk])).content.decode()
        assert 'data-viewer-surface' in html
        assert 'id="rotate-right"' in html
