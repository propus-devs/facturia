"""Visor a pantalla completa de recibos (dentro de la app, con botón «Volver»)."""
from datetime import date
from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from finance.models import FinancialMovement

pytestmark = pytest.mark.django_db


@pytest.fixture
def movement_with_receipt(client_entity, user, movement_category):
    return FinancialMovement.objects.create(
        client=client_entity,
        movement_type="income",
        created_by=user,
        category=movement_category,
        description="Con recibo",
        amount=Decimal("10.00"),
        payment_method="cash",
        date=date(2026, 3, 10),
        receipt=SimpleUploadedFile("ticket.pdf", b"%PDF-1.4", content_type="application/pdf"),
    )


class TestReceiptViewer:
    def test_requires_login(self, client, movement_with_receipt):
        resp = client.get(reverse("finance:receipt_view", args=[movement_with_receipt.pk]))
        assert resp.status_code == 302
        assert "/login/" in resp["Location"]

    def test_owner_sees_viewer_with_back_to_movement(self, auth_client, movement_with_receipt):
        resp = auth_client.get(reverse("finance:receipt_view", args=[movement_with_receipt.pk]))
        assert resp.status_code == 200
        html = resp.content.decode()
        assert f'data-file-url="{reverse("finance:receipt", args=[movement_with_receipt.pk])}"' in html
        assert 'data-file-type="pdf"' in html
        assert f'href="{reverse("finance:edit_movement", args=[movement_with_receipt.pk])}" data-back' in html
        assert 'id="rotate-left"' in html

    def test_movement_without_receipt_is_404(self, auth_client, financial_movement):
        resp = auth_client.get(reverse("finance:receipt_view", args=[financial_movement.pk]))
        assert resp.status_code == 404

    def test_other_tenant_gets_404(self, client, other_user, movement_with_receipt):
        client.force_login(other_user)
        resp = client.get(reverse("finance:receipt_view", args=[movement_with_receipt.pk]))
        assert resp.status_code == 404

    def test_list_receipt_link_opens_in_app_viewer(self, auth_client, movement_with_receipt):
        html = auth_client.get(reverse("finance:movements")).content.decode()
        assert reverse("finance:receipt_view", args=[movement_with_receipt.pk]) in html
        assert f'href="{reverse("finance:receipt", args=[movement_with_receipt.pk])}"' not in html
