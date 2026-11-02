import copy
import unittest
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from invoice_processing.invoice import Invoice, InvoiceLine
from invoice_processing.rules import evaluate

BUSINESS_DATE = date(2026, 11, 16)
UNSET = object()

PUBLIC_VAT_RATES = [
    {"rate": "19", "valid_from": "2017-01-01", "valid_to": "2025-07-31"},
    {"rate": "9", "valid_from": "2017-01-01", "valid_to": "2025-07-31"},
    {"rate": "5", "valid_from": "2017-01-01", "valid_to": "2025-07-31"},
    {"rate": "21", "valid_from": "2025-08-01", "valid_to": None},
    {"rate": "11", "valid_from": "2025-08-01", "valid_to": None},
    {"rate": "0", "valid_from": "2017-01-01", "valid_to": None},
]


# ---------------------------------------------------------------------------
# Helpers for building inputs
# ---------------------------------------------------------------------------
def cent(value):
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def make_config(**changes):
    config = {
        "auto_approval_limit": {"RON": "50000.00", "EUR": "10000.00"},
        "amount_tolerance": "0.01",
        "vat_rates": copy.deepcopy(PUBLIC_VAT_RATES),
    }
    config.update(changes)
    return config


def make_supplier(**changes):
    supplier = {
        "cui": "77568379", "name": "Tara Verde Agro SRL", "status": "ACTIVE",
        "iban": "RO65BPAX9398850132523193", "email": "contabilitate@taraverde.example",
    }
    supplier.update(changes)
    return supplier


def make_po(**changes):
    po = {
        "po_number": "PO-988911", "supplier_cui": "77568379", "currency": "RON",
        "order_date": "2026-11-01", "status": "OPEN",
        "total_amount": "100000.00", "remaining_balance": "50000.00",
    }
    po.update(changes)
    return po

def make_receipts(*items):
    return {
        item_code: {
            "received_quantity": Decimal(str(received)),
            "invoiced_quantity": Decimal(str(invoiced))
        }
        for item_code, received, invoiced in items
    }



def make_invoice(lines=(("S", "3", "1234.50", "21"),), *, line_amounts=None,
                 net=None, vat=None, gross=None, **fields):
    """Invoice with one default line (3 x 1234.50, 21%) and consistent totals.

    lines: tuples of (item_code, quantity, unit price, rate). Totals are calculated from the lines,
    with half-up rounding per line when they are not provided explicitly.
    """
    built, net_sum, vat_sum = [], Decimal("0"), Decimal("0")
    for i, (item_code,q, p, r) in enumerate(lines):
        item_code = str(item_code)
        qty, price, rate = Decimal(q), Decimal(p), Decimal(r)
        line_net = cent(qty * price)
        line_vat = cent(line_net * rate / Decimal(100))
        declared = line_net if line_amounts is None else Decimal(line_amounts[i])
        built.append(InvoiceLine(item_code=item_code, quantity=qty, unit_price=price, line_amount=declared, vat_rate=rate))
        net_sum += line_net
        vat_sum += line_vat

    values = dict(
        number="FCT558083", supplier_cui="77568379", iban="RO65BPAX9398850132523193",
        issue_date=date(2026, 11, 11), due_date=date(2026, 12, 11), delivery_date=None,
        currency="RON", po_number="PO-988911", lines=tuple(built),
        net_total=Decimal(net) if net is not None else net_sum,
        vat_total=Decimal(vat) if vat is not None else vat_sum,
        gross_total=Decimal(gross) if gross is not None else net_sum + vat_sum,
    )
    values.update(fields)
    return Invoice(**values)


def run(invoice=None, supplier=UNSET, po=UNSET, config=None,
        business_date=BUSINESS_DATE, balance="50000.00"):
    """Call evaluate with clean defaults. supplier=None / po=None are real values (404)."""
    return evaluate(
        invoice if invoice is not None else make_invoice(),
        make_supplier() if supplier is UNSET else supplier,
        make_po() if po is UNSET else po,
        config if config is not None else make_config(),
        business_date,
        Decimal(balance),
    )


def invoice_for_vat(rate, issue, delivery=None):
    return make_invoice(
        lines=(("3", "1234.50", rate),),
        issue_date=issue, due_date=issue + timedelta(days=30), delivery_date=delivery,
    )


class RulesTestCase(unittest.TestCase):
    def assertResult(self, result, outcome, codes=()):
        self.assertEqual(result, (outcome, tuple(codes)))


class ChangeRequestRulesTests(RulesTestCase):
    def three_way_config(self, enabled=True):
        config = make_config()
        config["features"] = {
            "three_way_match": enabled,
        }
        return config

    def invoice_with_lines(self, *lines, **fields):
        
        return make_invoice(
            lines=tuple(lines),
            **fields
        )

    def evaluate_change_request(
        self,
        invoice,
        receipts,
        *,
        po=UNSET,
        config=None,
    ):
        if po is UNSET:
            po = make_po()
        
        if config is None:
            config = self.three_way_config()

        return evaluate(
            invoice=invoice,
            supplier=make_supplier(),
            po=po,
            config=config,
            business_date=BUSINESS_DATE,
            effective_balance=Decimal("50000"),
            goods_receipts=receipts,
        )

    def test_feature_disabled_does_not_evaluate_receipts(self):
        invoice = self.invoice_with_lines(
            ("K", "11", "10", "21"),
        )

        outcome, codes = self.evaluate_change_request(
            invoice,
            make_receipts(("K", "15", "5")),
            config=self.three_way_config(enabled=False),
        )

        self.assertResult((outcome, codes), "APPROVED", [])

    def test_exact_available_quantity_is_accepted(self):
        # 15 received - 5 already invoiced = 10 available.
        invoice = self.invoice_with_lines(
            ("K", "10", "10", "21"),
        )

        self.assertResult(
            self.evaluate_change_request(
                invoice,
                make_receipts(("K", "15", "5")),
            ),
            "APPROVED",
            [],
        )

    def test_quantity_above_available_requires_manual_review(self):
        # 15 received - 5 already invoiced = 10 available.
        invoice = self.invoice_with_lines(
            ("K", "10.001", "10", "21"),
        )

        self.assertResult(
            self.evaluate_change_request(
                invoice,
                make_receipts(("K", "15", "5")),
            ),
            "MANUAL_REVIEW",
            ["QTY_NOT_RECEIVED"],
        )

    def test_absent_item_has_zero_available_quantity(self):
        invoice = self.invoice_with_lines(
            ("UNKNOWN", "1", "10", "21"),
        )

        self.assertResult(
            self.evaluate_change_request(
                invoice,
                make_receipts(("K", "15", "5")),
            ),
            "MANUAL_REVIEW",
            ["QTY_NOT_RECEIVED"],
        )

    def test_invoiced_quantity_is_subtracted_from_received_quantity(self):
        invoice = self.invoice_with_lines(
            ("K", "11", "10", "21"),
        )

        self.assertResult(
            self.evaluate_change_request(
                invoice,
                make_receipts(("K", "15", "5")),
            ),
            "MANUAL_REVIEW",
            ["QTY_NOT_RECEIVED"],
        )

    def test_quantities_from_same_item_are_aggregated(self):
        invoice = self.invoice_with_lines(
            ("K", "4", "10", "21"),
            ("K", "6", "10", "21"),
        )

        self.assertResult(
            self.evaluate_change_request(
                invoice,
                make_receipts(("K", "15", "5")),
            ),
            "APPROVED",
            [],
        )

    def test_aggregated_quantity_above_available_fails_once(self):
        invoice = self.invoice_with_lines(
            ("K", "6", "10", "21"),
            ("K", "5", "10", "21"),
        )

        self.assertResult(
            self.evaluate_change_request(
                invoice,
                make_receipts(("K", "15", "5")),
            ),
            "MANUAL_REVIEW",
            ["QTY_NOT_RECEIVED"],
        )

    def test_multiple_items_fail_with_one_reason_code(self):
        invoice = self.invoice_with_lines(
            ("K", "11", "10", "21"),
            ("L", "5", "10", "21"),
        )

        self.assertResult(
            self.evaluate_change_request(
                invoice,
                make_receipts(
                    ("K", "15", "5"),
                    ("L", "2", "0"),
                ),
            ),
            "MANUAL_REVIEW",
            ["QTY_NOT_RECEIVED"],
        )

    def test_rule_applies_to_closed_po(self):
        invoice = self.invoice_with_lines(
            ("K", "11", "10", "21"),
        )

        self.assertResult(
            self.evaluate_change_request(
                invoice,
                make_receipts(("K", "15", "5")),
                po=make_po(status="CLOSED"),
            ),
            "REJECTED",
            ["PO_CLOSED", "QTY_NOT_RECEIVED"],
        )

    def test_rule_applies_to_currency_mismatch(self):
        invoice = self.invoice_with_lines(
            ("K", "11", "10", "21"),
        )

        self.assertResult(
            self.evaluate_change_request(
                invoice,
                make_receipts(("K", "15", "5")),
                po=make_po(currency="EUR"),
            ),
            "REJECTED",
            ["CURRENCY_MISMATCH", "QTY_NOT_RECEIVED"],
        )

    def test_rule_applies_to_supplier_mismatch(self):
        invoice = self.invoice_with_lines(
            ("K", "11", "10", "21"),
        )

        self.assertResult(
            self.evaluate_change_request(
                invoice,
                make_receipts(("K", "15", "5")),
                po=make_po(supplier_cui="11111111"),
            ),
            "REJECTED",
            ["PO_SUPPLIER_MISMATCH", "QTY_NOT_RECEIVED"],
        )

    def test_missing_po_skips_three_way_match(self):
        invoice = self.invoice_with_lines(
            ("K", "11", "10", "21"),
            po_number=None,
        )

        self.assertResult(
            self.evaluate_change_request(
                invoice,
                make_receipts(("K", "15", "5")),
            ),
            "MANUAL_REVIEW",
            ["PO_MISSING"],
        )

    def test_unknown_po_skips_three_way_match(self):
        invoice = self.invoice_with_lines(
            ("K", "11", "10", "21"),
        )

        self.assertResult(
            self.evaluate_change_request(
                invoice,
                make_receipts(("K", "15", "5")),
                po=None,
            ),
            "MANUAL_REVIEW",
            ["PO_NOT_FOUND"],
        )

    def test_decimal_quantities_are_compared_exactly(self):
        invoice = self.invoice_with_lines(
            ("K", "2.125", "10", "21"),
        )

        self.assertResult(
            self.evaluate_change_request(
                invoice,
                make_receipts(("K", "2.125", "0")),
            ),
            "APPROVED",
            [],
        )

    def test_decimal_quantity_above_available_fails(self):
        invoice = self.invoice_with_lines(
            ("K", "2.126", "10", "21"),
        )

        self.assertResult(
            self.evaluate_change_request(
                invoice,
                make_receipts(("K", "2.125", "0")),
            ),
            "MANUAL_REVIEW",
            ["QTY_NOT_RECEIVED"],
        )

    def test_other_manual_reason_codes_are_preserved(self):
        invoice = self.invoice_with_lines(
            ("K", "11", "10", "21"),
        )

        supplier = make_supplier(
            iban="RO00WRONGIBAN",
        )

        result = evaluate(
            invoice=invoice,
            supplier=supplier,
            po=make_po(),
            config=self.three_way_config(),
            business_date=BUSINESS_DATE,
            effective_balance=Decimal("50000"),
            goods_receipts=make_receipts(("K", "15", "5")),
        )

        self.assertResult(
            result,
            "MANUAL_REVIEW",
            ["IBAN_MISMATCH", "QTY_NOT_RECEIVED"],
        )

if __name__ == "__main__":
    unittest.main()