"""Fase 1 de emisión: modelos base (borrador sin archivo, inmutabilidad, numeración, tenant)."""
from datetime import date
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from documents.models import Document, IssuedDocument, NumberSequence
from documents.tests.factories import ClientFactory, CompanyFactory, DocumentFactory


def make_draft(client, **kw):
    defaults = dict(
        client=client, original_name="borrador", document_type="invoice", source="app", confidence={"emitido": 1},
        status="draft", flow="in", issue_date=date(2026, 3, 1),
        base_amount=Decimal("100.00"), tax_amount=Decimal("21.00"), total_amount=Decimal("121.00"),
    )
    defaults.update(kw)
    return Document.all_objects.create(**defaults)


@pytest.mark.django_db
def test_draft_issued_can_be_saved_without_file():
    doc = make_draft(ClientFactory())
    assert not doc.file and doc.is_issued


@pytest.mark.django_db
def test_file_still_required_for_received_documents():
    with pytest.raises(ValidationError):
        Document.all_objects.create(
            client=ClientFactory(), original_name="x.pdf", document_type="invoice", source="upload"
        )


@pytest.mark.django_db
def test_draft_and_voided_status_only_for_issued():
    doc = DocumentFactory()
    doc.status = "draft"
    with pytest.raises(ValidationError):
        doc.save()


@pytest.mark.django_db
def test_approved_issued_document_is_locked():
    doc = make_draft(ClientFactory())
    doc.status = "approved"
    doc.file = "x/y.pdf"
    doc.save()
    doc.total_amount = Decimal("999.00")
    with pytest.raises(ValidationError):
        doc.save()


@pytest.mark.django_db
def test_issuing_op_allows_locked_change_and_void_is_allowed():
    doc = make_draft(ClientFactory(), status="approved", file="x/y.pdf")
    doc.status = "voided"
    doc.save()  # anular no toca campos bloqueados
    doc.total_amount = Decimal("1.00")
    doc._issuing_op = True
    doc.save()


@pytest.mark.django_db
def test_received_approved_document_is_not_locked():
    doc = DocumentFactory(status="approved")
    doc.total_amount = Decimal("5.00")
    doc.save()


@pytest.mark.django_db
def test_number_sequence_unique_per_client_kind_prefix_year():
    c = ClientFactory()
    NumberSequence.objects.create(client=c, kind="invoice", prefix="F", year=2026)
    with pytest.raises(IntegrityError), transaction.atomic():
        NumberSequence.objects.create(client=c, kind="invoice", prefix="F", year=2026)
    # misma clave en otro cliente o tipo: permitido
    NumberSequence.objects.create(client=ClientFactory(), kind="invoice", prefix="F", year=2026)
    NumberSequence.objects.create(client=c, kind="quote", prefix="F", year=2026)


def test_number_sequence_format():
    seq = NumberSequence(prefix="FAC-", year=2026, padding=4)
    assert seq.format(7) == "FAC-2026-0007"


@pytest.mark.django_db
def test_issued_document_one_to_one_and_token_unique():
    c = ClientFactory()
    a, b = make_draft(c), make_draft(c)
    IssuedDocument.objects.create(document=a, public_token="tok")
    with pytest.raises(IntegrityError), transaction.atomic():
        IssuedDocument.objects.create(document=b, public_token="tok")
    IssuedDocument.objects.create(document=b)  # sin token (None) repetible


@pytest.mark.django_db
def test_company_of_other_client_rejected_on_issued():
    other = CompanyFactory(client=ClientFactory())
    with pytest.raises(ValueError):
        make_draft(ClientFactory(), company=other)
