"""Uso por cliente (vista aparte, solo superusuarios) y extracción en Métricas."""
from datetime import timedelta
from decimal import Decimal as D

import pytest
from django.urls import reverse
from django.utils import timezone

from documents.models import ExtractionRun
from documents.services.client_overview import client_overview
from documents.tests.factories import ClientFactory, DocumentFactory

pytestmark = pytest.mark.django_db

TODAY = timezone.now().date()
URL = "client_usage"


def run(doc, cost, **kw):
    return ExtractionRun.objects.create(client=doc.client, document=doc, status="succeeded", cost_usd=D(cost), **kw)


def doc(client, **kw):
    kw.setdefault("issue_date", TODAY)
    return DocumentFactory(client=client, **kw)


def overview(**kw):
    return client_overview(TODAY - timedelta(days=1), TODAY + timedelta(days=1), **kw)


def by_name(result):
    return {r["name"]: r for r in result["rows"]}


class TestClientOverview:
    def test_empty_clients_have_zeroes_not_errors(self, client_entity):
        r = overview()
        row = r["rows"][0]
        assert row["documents"] == 0 and row["confidence"] is None and row["cost_usd"] == 0
        assert r["totals"]["confidence"] is None and r["rankings"]["documents"] == []

    def test_documents_and_cost_per_client(self, client_entity, other_client_entity):
        doc(client_entity, status="approved", flow="out", total_amount=D("200.00"))
        doc(client_entity, status="approved", flow="in", total_amount=D("900.00"))
        d = doc(other_client_entity, status="pending")
        run(d, "0.30"); run(d, "0.10")
        rows = by_name(overview())
        assert rows[client_entity.name]["documents"] == 2
        other = rows[other_client_entity.name]
        assert other["pending"] == 1
        assert other["cost_usd"] == D("0.40") and other["cost_per_document"] == D("0.40") and other["runs"] == 2

    def test_does_not_expose_client_financials(self, client_entity):
        doc(client_entity, status="approved", flow="out", total_amount=D("200.00"))
        r = overview()
        assert not {"expense", "income"} & set(r["rows"][0]) and not {"expense", "income"} & set(r["totals"])
        assert "expense" not in r["rankings"] and not {"expense", "income"} & {k for k, *_ in r["columns"]}

    def test_confidence_rates_and_tone(self, client_entity):
        doc(client_entity, confidence_global=D("0.9500"))
        doc(client_entity, confidence_global=D("0.5000"))
        doc(client_entity, source="app", confidence_global=None)     # emitido en la app: sin OCR
        row = by_name(overview())[client_entity.name]
        assert row["documents"] == 3 and row["confidence_docs"] == 2
        assert row["confidence"] == 72.5 and row["low_confidence_pct"] == 50.0
        assert row["confidence_tone"] == "danger"

    def test_fallback_and_failed(self, client_entity):
        a, b = doc(client_entity), doc(client_entity, processing_status="failed")
        run(a, "0.01"); run(a, "0.05", is_fallback=True); run(b, "0.01")
        row = by_name(overview())[client_entity.name]
        assert row["fallback_pct"] == 50.0 and row["failed"] == 1

    def test_rankings_and_sort(self, client_entity, other_client_entity):
        for _ in range(2):
            run(doc(client_entity, confidence_global=D("0.99")), "0.01")
        run(doc(other_client_entity, confidence_global=D("0.60")), "0.50")
        r = overview()
        assert r["rankings"]["documents"][0]["name"] == client_entity.name
        assert r["rankings"]["cost"][0]["name"] == other_client_entity.name
        assert r["rankings"]["low_confidence"][0]["name"] == other_client_entity.name
        assert overview(sort="cost")["rows"][0]["name"] == other_client_entity.name
        assert overview(sort="confidence")["rows"][0]["name"] == other_client_entity.name
        assert overview(sort="expense")["sort"] == "documents"      # orden financiero o no válido → por defecto

    def test_sort_puts_clients_without_data_last(self, client_entity, other_client_entity):
        doc(other_client_entity, confidence_global=D("0.99"))
        assert overview(sort="confidence")["rows"][-1]["name"] == client_entity.name

    def test_period_filters_activity(self, client_entity):
        doc(client_entity)
        old = client_overview(TODAY - timedelta(days=60), TODAY - timedelta(days=30))
        assert old["totals"]["documents"] == 0

    def test_drafts_and_voided_are_not_counted(self, client_entity):
        doc(client_entity, source="app", status="draft")
        assert overview()["totals"]["documents"] == 0


class TestClientUsageView:
    def test_anonymous_redirects_to_login(self, client):
        assert client.get(reverse(URL)).status_code == 302

    def test_regular_user_gets_404(self, auth_client, other_client_entity):
        assert auth_client.get(reverse(URL)).status_code == 404

    def test_superuser_sees_all_clients(self, auth_client, user, document, other_client_entity):
        run(document, "0.25")
        doc(other_client_entity)
        user.is_superuser = True
        user.save()
        r = auth_client.get(reverse(URL))
        body = r.content.decode()
        assert r.status_code == 200
        assert other_client_entity.name in body and document.client.name in body
        assert "Más documentos" in body and "Menor confianza" in body
        assert r.context["totals"]["documents"] == 2
        assert "Gasto" not in body and "Ingresos" not in body and "€" not in body

    def test_period_and_sort_params(self, auth_client, user):
        user.is_superuser = True
        user.save()
        r = auth_client.get(reverse(URL), {"start": "2026-02-01", "end": "2026-01-01", "sort": "cost"})
        assert r.status_code == 200 and r.context["sort"] == "cost"
        assert r.context["period"]["start"] < r.context["period"]["end"]   # invertido → se corrige

    def test_sidebar_link_only_for_superuser(self, auth_client, user):
        assert reverse(URL) not in auth_client.get(reverse("dashboard")).content.decode()
        user.is_superuser = True
        user.save()
        assert reverse(URL) in auth_client.get(reverse("dashboard")).content.decode()


class TestMetricsNoLongerShowsClients:
    def test_superuser_metrics_has_no_client_table(self, auth_client, user, document, other_client_entity):
        run(document, "0.25")
        run(doc(other_client_entity), "0.50")
        user.is_superuser = True
        user.save()
        r = auth_client.get(reverse("metrics"))
        assert "client_usage" not in r.context
        assert "Uso por cliente" not in r.content.decode().split("</aside>")[-1]
        assert other_client_entity.name not in r.content.decode()


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
