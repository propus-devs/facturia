"""Badge de pendientes del menú lateral y enlaces de administración."""
import pytest
from django.urls import reverse

from documents.tests.factories import DocumentFactory

pytestmark = pytest.mark.django_db


def test_pending_badge_counts_only_own_processed_pending(auth_client, user, other_client_entity):
    DocumentFactory(client=user.client, status="pending", processing_status="processed")
    DocumentFactory(client=user.client, status="pending", processing_status="processed")
    DocumentFactory(client=user.client, status="approved", processing_status="processed")
    DocumentFactory(client=user.client, status="pending", processing_status="processing")
    DocumentFactory(client=other_client_entity, status="pending", processing_status="processed")
    r = auth_client.get(reverse("documents:list"))
    assert r.context["sidebar_pending"] == 2
    assert "2 pendientes de revisión" in r.content.decode()


def test_no_badge_when_nothing_pending(auth_client):
    r = auth_client.get(reverse("documents:list"))
    assert r.context["sidebar_pending"] == 0
    assert 'class="nav-badge"' not in r.content.decode()


def test_admin_link_only_for_superuser(auth_client, user):
    body = auth_client.get(reverse("dashboard")).content.decode()
    assert "Uso por cliente" not in body and reverse("admin:index") not in body
    user.is_superuser = True
    user.save()
    body = auth_client.get(reverse("dashboard")).content.decode()
    assert "Uso por cliente" in body and f'href="{reverse("admin:index")}"' in body


def test_login_page_has_no_counts(client):
    assert client.get(reverse("login")).status_code == 200
