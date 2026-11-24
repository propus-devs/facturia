"""Totales de un documento emitido (sin acceso a BD). Todo `Decimal`, nunca `float`.

Por línea:   importe = cantidad × precio × (1 − dto línea)
Global:      base de cada línea × (1 − dto global); se agrupa por tipo de IVA
Impuestos:   IVA por tipo sobre su base; recargo de equivalencia opcional; retención sobre la base total
Total:       base + IVA + recargo − retención   (services/taxes.expected_total)
"""
from dataclasses import dataclass, field
from decimal import Decimal

from documents.services.taxes import expected_total, percentage_of, quantize, to_decimal

HUNDRED = Decimal("100")
# IVA → recargo de equivalencia habitual
SURCHARGE_BY_VAT = {Decimal("21"): Decimal("5.2"), Decimal("10"): Decimal("1.4"), Decimal("4"): Decimal("0.5")}


@dataclass
class TotalsResult:
    line_amounts: list = field(default_factory=list)   # importe de cada línea (tras su dto, antes del global)
    base: Decimal = Decimal("0.00")
    vat: dict = field(default_factory=dict)            # tipo → (base, importe)
    surcharge: dict = field(default_factory=dict)      # tipo de recargo → (base, importe)
    withholding: tuple | None = None                   # (tipo, base, importe)
    vat_total: Decimal = Decimal("0.00")
    surcharge_total: Decimal = Decimal("0.00")
    withholding_total: Decimal = Decimal("0.00")
    total: Decimal = Decimal("0.00")


def line_amount(quantity, unit_price, discount_pct=0) -> Decimal:
    gross = to_decimal(quantity or 0) * to_decimal(unit_price or 0)
    return quantize(gross * (HUNDRED - to_decimal(discount_pct or 0)) / HUNDRED)


def compute_totals(lines, *, global_discount_pct=0, withholding_pct=0, apply_surcharge=False) -> TotalsResult:
    """`lines`: iterable de dicts con quantity, unit_price, tax_rate y discount_pct."""
    res = TotalsResult()
    global_factor = (HUNDRED - to_decimal(global_discount_pct or 0)) / HUNDRED
    base_by_vat: dict[Decimal, Decimal] = {}

    for ln in lines:
        amount = line_amount(ln.get("quantity"), ln.get("unit_price"), ln.get("discount_pct"))
        res.line_amounts.append(amount)
        rate = to_decimal(ln.get("tax_rate") or 0)
        base_by_vat[rate] = base_by_vat.get(rate, Decimal("0")) + quantize(amount * global_factor)

    res.base = quantize(sum(base_by_vat.values(), Decimal("0")))
    for rate in sorted(base_by_vat, reverse=True):
        base = quantize(base_by_vat[rate])
        res.vat[rate] = (base, percentage_of(base, rate))
        s_rate = SURCHARGE_BY_VAT.get(rate) if apply_surcharge else None
        if s_rate:
            res.surcharge[s_rate] = (base, percentage_of(base, s_rate))

    res.vat_total = quantize(sum((a for _, a in res.vat.values()), Decimal("0")))
    res.surcharge_total = quantize(sum((a for _, a in res.surcharge.values()), Decimal("0")))
    w = to_decimal(withholding_pct or 0)
    if w > 0:
        res.withholding = (w, res.base, percentage_of(res.base, w))
        res.withholding_total = res.withholding[2]
    res.total = expected_total(res.base, res.vat_total, res.surcharge_total, res.withholding_total)
    return res
