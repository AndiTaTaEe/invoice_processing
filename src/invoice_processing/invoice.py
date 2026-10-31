from decimal import Decimal
from datetime import date
from typing import Optional
from dataclasses import dataclass


class Unreadable(Exception):
    """raised by the extractor when a document is unreadable (A-R01)
    the message only goes to log, the decision has EXTRACTION_FAILED reason code, and the document is not processed further
    """

@dataclass(frozen=True)
class InvoiceLine:
    """a line in an invoice, with the values extracted from the document"""
    quantity: Decimal
    unit_price: Decimal
    total: Decimal
    vat_rate: Decimal

@dataclass(frozen=True)
class Invoice:
    number: str # normalized
    supplier_cui: str # normalized
    iban: str # normalized
    issue_date: date
    due_date: date
    delivery_date: Optional[date]
    currency: str # RON/Eur
    po_number: Optional[str] 
    lines: tuple[InvoiceLine, ...]
    net_total: Decimal
    vat_total: Decimal
    gross_total: Decimal
