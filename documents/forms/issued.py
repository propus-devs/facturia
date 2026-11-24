"""Formulario de factura/presupuesto emitido: cabecera + líneas (formset)."""
from django import forms
from django.forms import formset_factory

from documents.forms.document_rectification_form import CommaDecimalField
from documents.models import Company, DocumentCategory, IssuedDocument, Tag

def DATE_WIDGET():
    # <input type="date"> solo acepta ISO (aaaa-mm-dd), aunque el idioma sea es (dd/mm/aaaa)
    return forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


TYPE_LABELS = {
    "invoice": "Factura", "receipt": "Ticket", "quote": "Presupuesto", "proforma": "Proforma",
    "corrected_invoice": "Factura rectificativa",
}


class IssuedDocumentForm(forms.Form):
    document_type = forms.ChoiceField(label="Tipo")
    company = forms.ModelChoiceField(queryset=Company.objects.none(), required=False, label="Contacto")
    issue_date = forms.DateField(label="Fecha de emisión", widget=DATE_WIDGET())
    due_date = forms.DateField(label="Vencimiento", required=False, widget=DATE_WIDGET())
    valid_until = forms.DateField(label="Válido hasta", required=False, widget=DATE_WIDGET())
    payment_method = forms.ChoiceField(
        label="Forma de pago", required=False, choices=[("", "—")] + IssuedDocument.PAYMENT_METHOD_CHOICES
    )
    global_discount_pct = CommaDecimalField(label="Descuento global (%)", required=False, min_value=0, max_value=100,
                                            max_digits=5, decimal_places=2)
    withholding_pct = CommaDecimalField(label="Retención IRPF (%)", required=False, min_value=0, max_value=100,
                                        max_digits=5, decimal_places=2)
    apply_surcharge = forms.BooleanField(label="Recargo de equivalencia", required=False)
    category = forms.ModelChoiceField(queryset=DocumentCategory.objects.none(), required=False, label="Cuenta contable")
    tags = forms.ModelMultipleChoiceField(queryset=Tag.objects.none(), required=False, label="Etiquetas")
    notes = forms.CharField(label="Notas", required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, client, types, **kwargs):
        super().__init__(*args, **kwargs)
        self.client = client
        self.fields["document_type"].choices = [(t, TYPE_LABELS[t]) for t in types]
        self.fields["company"].queryset = Company.objects.filter(client=client).order_by("name")
        self.fields["category"].queryset = DocumentCategory.objects.filter(client=client)
        self.fields["tags"].queryset = Tag.objects.filter(client=client)

    def clean_global_discount_pct(self):
        return self.cleaned_data.get("global_discount_pct") or 0

    def clean_withholding_pct(self):
        return self.cleaned_data.get("withholding_pct") or 0

    def service_kwargs(self):
        """Argumentos para `issuing.create_draft/update_draft` (sin client/user/lines)."""
        d = self.cleaned_data
        return {
            "company": d["company"], "issue_date": d["issue_date"], "category": d["category"],
            "tags": list(d["tags"]), "due_date": d["due_date"], "valid_until": d["valid_until"],
            "payment_method": d["payment_method"], "notes": d["notes"],
            "global_discount_pct": d["global_discount_pct"], "withholding_pct": d["withholding_pct"],
            "apply_surcharge": d["apply_surcharge"],
        }


class IssuedLineForm(forms.Form):
    description = forms.CharField(label="Concepto", required=False, max_length=500)
    # Enteros: las unidades facturadas no se parten (el modelo admite decimales por las facturas OCR).
    quantity = forms.IntegerField(
        label="Cantidad", required=False, initial=1, min_value=1, max_value=99999999,
        widget=forms.NumberInput(attrs={"step": "1", "min": "1", "inputmode": "numeric"}),
        error_messages={"invalid": "La cantidad debe ser un número entero."},
    )
    unit_price = CommaDecimalField(label="Precio", required=False, max_digits=14, decimal_places=4)
    tax_rate = CommaDecimalField(label="IVA (%)", required=False, initial=21, min_value=0, max_value=100,
                                 max_digits=5, decimal_places=2)
    discount_pct = CommaDecimalField(label="Dto. (%)", required=False, initial=0, min_value=0, max_value=100,
                                     max_digits=5, decimal_places=2)


IssuedLineFormSet = formset_factory(IssuedLineForm, extra=1)


def lines_from_formset(formset):
    """Líneas limpias (dicts) de un formset válido; las filas vacías se ignoran."""
    return [f.cleaned_data for f in formset.forms if f.cleaned_data]


class ContactQuickForm(forms.Form):
    name = forms.CharField(max_length=255, label="Nombre")
    tax_id = forms.CharField(max_length=50, required=False, label="NIF")
    email = forms.EmailField(required=False, label="Email")
