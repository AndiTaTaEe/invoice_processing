from collections.abc import Mapping
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Any
from .normalize import normalize_cui, normalize_iban

CENT_FACTOR = Decimal("0.01")

REJECTED_CODES = {
    "SUPPLIER_INACTIVE",
    "SUPPLIER_BLOCKED",
    "PO_SUPPLIER_MISMATCH",
    "PO_CLOSED",
    "CURRENCY_MISMATCH",
    "INVOICE_DATE_IN_FUTURE",
    "DUE_DATE_BEFORE_ISSUE",
    "VAT_RATE_INVALID"
}

OUTCOME_APPROVED = "APPROVED"
OUTCOME_REJECTED = "REJECTED"
OUTCOME_MANUAL_REVIEW = "MANUAL_REVIEW"

def _get(value: Any, name: str, default: Any = None) -> Any:
    """
    Get a value from a mapping or an object.
    """
    if value is None:
        return default

    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)

def _decimal(value: Any) -> Decimal:
    return Decimal(str(value))

def _money(value: Any) -> Decimal:
    return _decimal(value).quantize(CENT_FACTOR, rounding=ROUND_HALF_UP)

def _date(value: Any) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))

def _vat_rate_is_valid(rate: Decimal, reference_date: date, vat_rates: list[Mapping[str, Any]]) -> bool:
    """
    Check if the given VAT rate is valid for the reference date based on the provided VAT rates configuration. 
    """     
    for vat in vat_rates:
        configured_rate = _decimal(vat["rate"])
        if configured_rate != rate:
            continue
        valid_from = _date(vat["valid_from"])
        valid_to_raw = vat.get("valid_to")
        valid_to = None if valid_to_raw is None else _date(valid_to_raw)

        if reference_date < valid_from:
            continue
        
        if valid_to is not None and reference_date > valid_to:
            continue
        
        return True
    return False

def _approval_limit(config: Mapping[str, Any], currency: str) -> Decimal:
    """
    Get the approval limit for the given currency from the config. If the config is a mapping, return the limit for the specified currency. If it is a single value, return that value as the limit for all currencies.
    """
    limits = config["auto_approval_limit"]
    if isinstance(limits, Mapping):
        return _decimal(limits[currency])
    return _decimal(limits)

def _calculate_totals(invoice) -> tuple[list[Decimal], Decimal, Decimal, Decimal]:
    """
    returns:
    - recalculated line amounts,
    - recalculated net total,
    - recalculated vat total,
    - recalculated gross total
    """
    line_amounts: list[Decimal] = []
    net_total = Decimal("0.00")
    vat_total = Decimal("0.00")

    for line in invoice.lines:
        quantity = _decimal(line.quantity)
        unit_price = _decimal(line.unit_price)
        vat_rate = _decimal(line.vat_rate)
        line_net = _money(quantity * unit_price)
        line_vat = _money(line_net * vat_rate / Decimal("100"))
        line_amounts.append(line_net)
        net_total += line_net
        vat_total += line_vat
    
    net_total = _money(net_total)
    vat_total = _money(vat_total)
    gross_total = _money(net_total + vat_total)

    return line_amounts, net_total, vat_total, gross_total

def _evaluate_total_mismatch(invoice, tolerance: Decimal) -> bool:
    """
    Evaluate if the declared totals in the invoice match the recalculated totals within the specified tolerance. Returns True if there is a mismatch, False otherwise.
    """
    line_amounts, net_total, vat_total, gross_total = _calculate_totals(invoice)

    for line, recalculated_line_amount in zip(invoice.lines, line_amounts):
        declared_line_amount = _decimal(line.line_amount)
        if abs(declared_line_amount - recalculated_line_amount) > tolerance:
            return True
    
    declared_net = _decimal(invoice.net_total)
    declared_vat = _decimal(invoice.vat_total)
    declared_gross = _decimal(invoice.gross_total)

    return any(
        abs(declared - recalculated) > tolerance
        for declared, recalculated in (
            (declared_net, net_total),
            (declared_vat, vat_total),
            (declared_gross, gross_total)
        )
    )

def _has_unreceived_quantity(invoice, goods_receipts):
    invoiced_by_item = {}
    for line in invoice.lines:
        invoiced_by_item[line.item_code] = (invoiced_by_item.get(line.item_code, Decimal("0")) + line.quantity)

    for item_code, invoiced_quantity in invoiced_by_item.items():
        receipt = goods_receipts.get(item_code, {})

        received_quantity = _decimal(receipt.get("received_quantity", Decimal("0")))
        already_invoiced = _decimal(receipt.get("invoiced_quantity", Decimal("0")))

        available_quantity = (received_quantity - already_invoiced)
        if invoiced_quantity > available_quantity:
            return True

    return False

def evaluate(invoice, supplier, po, config: Mapping[str, Any], business_date: date, effective_balance, goods_receipts=None) -> tuple[str, tuple[str, ...]]:
    """
    Evaluate an already extracted invoice.

    This function is pure:
    - it performs no HTTP calls;
    - it does not mutate its arguments;
    - it returns (outcome, sorted_unique_reason_codes).

    `supplier` and `po` are either dictionaries/objects or None.

    Duplicate detection and extraction failure are handled before this
    function because they require history or document parsing.
    """
    codes: set[str] = set()
    tolerance = _decimal(config["amount_tolerance"])
    business_date = _date(business_date)    

    # cases handled - A-R02, A-R03, A-R13
    supplier_missing = supplier is None
    if supplier_missing:
        codes.add("SUPPLIER_UNKNOWN") # A-R02 - supplier not found in the system
    else:
        supplier_status = _get(supplier, "status")
        if supplier_status == "INACTIVE":
            codes.add("SUPPLIER_INACTIVE")
        elif supplier_status == "BLOCKED":
            codes.add("SUPPLIER_BLOCKED") # A-R03 - supplier is blocked or inactive, with their respective codes

        registry_iban = normalize_iban(str(_get(supplier, "iban") or ""))
        if registry_iban != invoice.iban:
            codes.add("IBAN_MISMATCH") # A-R13 - supplier IBAN does not match the one in the invoice

    # cases handled - A-R04, A-R05
    po_missing = invoice.po_number is None
    if po_missing:
        codes.add("PO_MISSING") # A-R04 - invoice does not have a PO number
    elif po is None:
        codes.add("PO_NOT_FOUND") # A-R05 - PO number is provided but not found in the system

    if not po_missing and po is not None:
        # A-R06 - PO supplier mismatch
        po_supplier_cui = normalize_cui(str(_get(po, "supplier_cui") or ""))
        if not supplier_missing and po_supplier_cui != invoice.supplier_cui:
            codes.add("PO_SUPPLIER_MISMATCH")
        
        # A-R07 - PO closed
        if _get(po, "status") == "CLOSED":
            codes.add("PO_CLOSED")
        
        # A-R08 - currency mismatch
        po_currency = _get(po, "currency")
        if po_currency != invoice.currency:
            codes.add("CURRENCY_MISMATCH")

        # A-R09 - invoice before PO
        order_date = _date(_get(po, "order_date"))
        if invoice.issue_date < order_date:
            codes.add("INVOICE_BEFORE_PO")
        
        # if the PO is present, check the three way match
        three_way_match = (config.get("features", {}).get("three_way_match", False))
        if (three_way_match and po is not None and _has_unreceived_quantity(invoice, goods_receipts or {})):
            codes.add("QTY_NOT_RECEIVED") # A-R19 - quantity not received 

    # A-R09 - invoice date in the future
    if invoice.issue_date > business_date:
        codes.add("INVOICE_DATE_IN_FUTURE")

    if invoice.due_date < invoice.issue_date:
        codes.add("DUE_DATE_BEFORE_ISSUE") # A-R09 - due date before issue date

    # A-R10 - declared sum is not equal to the sum of line amounts
    if _evaluate_total_mismatch(invoice, tolerance):
        codes.add("TOTAL_MISMATCH")

    # A-R11 - VAT rate invalid
    reference_date = (
        invoice.delivery_date if invoice.delivery_date is not None else invoice.issue_date
    )
    vat_rates = config["vat_rates"]
    if any(
        not _vat_rate_is_valid(_decimal(line.vat_rate), reference_date, vat_rates) for line in invoice.lines
    ): 
        codes.add("VAT_RATE_INVALID")

    # A-R12 is omitted only where there s no PO or the currency mismatches. it is intentionally still checked for CLOSED or wrong-supplier POs
    if (not po_missing and po is not None and "CURRENCY_MISMATCH" not in codes and _decimal(invoice.net_total) > _decimal(effective_balance)):
        codes.add("PO_BALANCE_EXCEEDED") # A-R12 - invoice net total exceeds the effective balance of the PO

    # A-r14 - gross > auto_approval_limit
    approval_limit = _approval_limit(config, invoice.currency)
    if _decimal(invoice.gross_total) > approval_limit:
        codes.add("ABOVE_APPROVAL_LIMIT")

    # Determine the outcome based on the codes
    if any(code in REJECTED_CODES for code in codes):
        outcome = OUTCOME_REJECTED
    elif codes:
        outcome = OUTCOME_MANUAL_REVIEW
    else:
        outcome = OUTCOME_APPROVED

    return outcome, tuple(sorted(codes))
