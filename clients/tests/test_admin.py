import pytest
from django.urls import reverse

from clients.models import CustomUser


@pytest.fixture
def admin_client_logged(client, client_entity):
    admin = CustomUser.objects.create_superuser(
        username="root", email="root@example.com", password="pass", client=client_entity
    )
    client.force_login(admin)
    return client


@pytest.mark.django_db
def test_add_user_form_shows_client_field(admin_client_logged):
    response = admin_client_logged.get(reverse("admin:clients_customuser_add"))
    assert response.status_code == 200
    assert 'name="client"' in response.content.decode()


@pytest.mark.django_db
def test_add_user_with_client(admin_client_logged, client_entity):
    response = admin_client_logged.post(
        reverse("admin:clients_customuser_add"),
        {
            "client": client_entity.pk,
            "username": "nuevo",
            "email": "nuevo@example.com",
            "usable_password": "true",
            "password1": "Una-clave-larga-123",
            "password2": "Una-clave-larga-123",
        },
    )
    assert response.status_code == 302
    assert CustomUser.objects.get(username="nuevo").client == client_entity


@pytest.mark.django_db
def test_add_user_without_client_fails(admin_client_logged):
    response = admin_client_logged.post(
        reverse("admin:clients_customuser_add"),
        {
            "username": "sin_cliente",
            "usable_password": "true",
            "password1": "Una-clave-larga-123",
            "password2": "Una-clave-larga-123",
        },
    )
    assert response.status_code == 200
    assert not CustomUser.objects.filter(username="sin_cliente").exists()
