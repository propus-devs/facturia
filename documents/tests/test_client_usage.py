"""Uso de extracción por cliente en Métricas: solo superusuarios."""
from decimal import Decimal as D

import pytest
from django.urls import reverse

from documents.models import ExtractionRun
from documents.services.usage import usage_by_client

pytestmark = pytest.mark.django_db


def run(doc, cost, client=None):
    return ExtractionRun.objects.create(
        client=client or doc.client, document=doc, status="succeeded", cost_usd=D(cost)
    )


class TestUsageByClient:
    def test_empty(self):
        assert usage_by_client() == []

    def test_groups_per_client_with_cost_per_doc_and_share(self, document, client_entity, other_client_entity):
        from documents.tests.factories import DocumentFactory
        other_doc = DocumentFactory(client=other_client_entity)
        run(document, "0.30"); run(document, "0.10")      # 1 doc, 2 llamadas
        run(other_doc, "0.60")
        rows = {r["client_id"]: r for r in usage_by_client()}
        mine = rows[client_entity.pk]
        assert mine["documents"] == 1 and mine["runs"] == 2 and mine["cost_usd"] == D("0.40")
        assert mine["cost_per_document"] == D("0.40") and round(mine["share"]) == 40
        assert rows[other_client_entity.pk]["share"] == 60


class TestMetricsView:
    def test_regular_user_does_not_see_other_clients(self, auth_client, document, other_client_entity):
        from documents.tests.factories import DocumentFactory
        run(DocumentFactory(client=other_client_entity), "0.50")
        r = auth_client.get(reverse("metrics"))
        assert "client_usage" not in r.context
        assert "Uso por cliente" not in r.content.decode()
        assert other_client_entity.name not in r.content.decode()

    def test_superuser_sees_table(self, auth_client, user, document):
        run(document, "0.25")
        user.is_superuser = True
        user.save()
        r = auth_client.get(reverse("metrics"))
        assert [c["documents"] for c in r.context["client_usage"]] == [1]
        assert "Uso por cliente" in r.content.decode()


class TestExtractionCostVisibility:
    def test_regular_user_does_not_see_cost_or_calls(self, auth_client, document):
        run(document, "0.25")
        body = auth_client.get(reverse("metrics")).content.decode()
        assert "US$" not in body and "llamadas" not in body

    def test_superuser_sees_cost_and_calls(self, auth_client, user, document):
        run(document, "0.25")
        user.is_superuser = True
        user.save()
        body = auth_client.get(reverse("metrics")).content.decode()
        assert "llamadas" in body and "US$/doc" in body

    def test_service_hides_extraction_for_regular_user(self, user, document):
        from documents.services.metrics_service import MetricsService
        run(document, "0.25")
        assert MetricsService.get_user_metrics(user=user, start=None, end=None)["extraction"] == {}
        user.is_superuser = True
        assert MetricsService.get_user_metrics(user=user, start=None, end=None)["extraction"]["runs"] == 1
