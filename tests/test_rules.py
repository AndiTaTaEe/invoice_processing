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


# ---------------------------------------------------------------------------
class BaselineTests(RulesTestCase):
    def test_clean_invoice_is_approved(self):
        self.assertResult(run(), "APPROVED")

    def test_result_is_an_outcome_and_tuple_pair(self):
        outcome, codes = run()
        self.assertIsInstance(outcome, str)
        self.assertIsInstance(codes, tuple)


class SupplierRulesTests(RulesTestCase):
    def test_unknown_supplier(self):
        self.assertResult(run(supplier=None), "MANUAL_REVIEW", ["SUPPLIER_UNKNOWN"])

    def test_inactive_supplier(self):
        self.assertResult(run(supplier=make_supplier(status="INACTIVE")),
                          "REJECTED", ["SUPPLIER_INACTIVE"])

    def test_blocked_supplier(self):
        self.assertResult(run(supplier=make_supplier(status="BLOCKED")),
                          "REJECTED", ["SUPPLIER_BLOCKED"])

    def test_different_iban(self):
        self.assertResult(run(supplier=make_supplier(iban="RO00AAAA0000000000000000")),
                          "MANUAL_REVIEW", ["IBAN_MISMATCH"])

    def test_unknown_supplier_skips_iban_and_status(self):
        # Without a registry entry, there is no IBAN to compare and no status.
        self.assertResult(run(supplier=None), "MANUAL_REVIEW", ["SUPPLIER_UNKNOWN"])


class PurchaseOrderRulesTests(RulesTestCase):
    def test_missing_order_skips_order_rules(self):
        # The PO is not printed; even if a triggering order is supplied, only PO_MISSING remains.
        bad_po = make_po(status="CLOSED", currency="EUR", supplier_cui="11111111",
                         order_date="2026-12-01")
        result = run(make_invoice(po_number=None), po=bad_po, balance="0.00")
        self.assertResult(result, "MANUAL_REVIEW", ["PO_MISSING"])

    def test_missing_order_skips_balance_check(self):
        self.assertResult(run(po=None, balance="0.00"), "MANUAL_REVIEW", ["PO_NOT_FOUND"])

    def test_order_belongs_to_another_supplier(self):
        self.assertResult(run(po=make_po(supplier_cui="11111111")),
                          "REJECTED", ["PO_SUPPLIER_MISMATCH"])

    def test_closed_order(self):
        self.assertResult(run(po=make_po(status="CLOSED")), "REJECTED", ["PO_CLOSED"])

    def test_different_currency_skips_balance_check(self):
        # A-R12 is skipped when A-R08 produced a code, so a zero balance does not produce PO_BALANCE_EXCEEDED.
        self.assertResult(run(po=make_po(currency="EUR"), balance="0.00"),
                          "REJECTED", ["CURRENCY_MISMATCH"])

    def test_balance_is_checked_for_closed_orders(self):
        self.assertResult(run(po=make_po(status="CLOSED"), balance="100.00"),
                          "REJECTED", ["PO_BALANCE_EXCEEDED", "PO_CLOSED"])

    def test_balance_is_checked_for_another_supplier_order(self):
        self.assertResult(run(po=make_po(supplier_cui="11111111"), balance="100.00"),
                          "REJECTED", ["PO_BALANCE_EXCEEDED", "PO_SUPPLIER_MISMATCH"])


class DateRulesTests(RulesTestCase):
    def test_issue_date_in_the_future(self):
        invoice = make_invoice(issue_date=date(2026, 11, 17), due_date=date(2026, 12, 17))
        self.assertResult(run(invoice), "REJECTED", ["INVOICE_DATE_IN_FUTURE"])

    def test_issue_date_on_business_date_is_accepted(self):
        invoice = make_invoice(issue_date=BUSINESS_DATE, due_date=date(2026, 12, 16))
        self.assertResult(run(invoice), "APPROVED")

    def test_due_date_before_issue_date(self):
        invoice = make_invoice(due_date=date(2026, 11, 10))
        self.assertResult(run(invoice), "REJECTED", ["DUE_DATE_BEFORE_ISSUE"])

    def test_due_date_on_issue_date_is_accepted(self):
        invoice = make_invoice(due_date=date(2026, 11, 11))
        self.assertResult(run(invoice), "APPROVED")

    def test_issue_date_before_order_date(self):
        self.assertResult(run(po=make_po(order_date="2026-11-12")),
                          "MANUAL_REVIEW", ["INVOICE_BEFORE_PO"])

    def test_issue_date_on_order_date_is_accepted(self):
        self.assertResult(run(po=make_po(order_date="2026-11-11")), "APPROVED")


class TotalMismatchTests(RulesTestCase):
    def test_difference_equal_to_tolerance_is_accepted(self):
        invoice = make_invoice(gross="4481.25")          # recalculated: 4481.24
        self.assertResult(run(invoice), "APPROVED")

    def test_difference_above_tolerance(self):
        invoice = make_invoice(gross="4481.26")
        self.assertResult(run(invoice), "MANUAL_REVIEW", ["TOTAL_MISMATCH"])

    def test_incorrect_declared_line_amount(self):
        invoice = make_invoice(line_amounts=["3703.52"])
        self.assertResult(run(invoice), "MANUAL_REVIEW", ["TOTAL_MISMATCH"])

    def test_incorrect_declared_net_or_vat(self):
        for kwargs in ({"net": "3703.55"}, {"vat": "777.80"}):
            with self.subTest(kwargs=kwargs):
                self.assertResult(run(make_invoice(**kwargs)), "MANUAL_REVIEW", ["TOTAL_MISMATCH"])

    def test_multiple_mismatches_produce_one_code(self):
        invoice = make_invoice(net="9999.00", vat="1.00", gross="1.00")
        self.assertResult(run(invoice), "MANUAL_REVIEW", ["TOTAL_MISMATCH"])

    def test_tolerance_comes_from_configuration(self):
        invoice = make_invoice(gross="4481.25")          # differs by 0.01
        strict = make_config(amount_tolerance="0.00")
        loose = make_config(amount_tolerance="0.50")
        self.assertResult(run(invoice, config=strict), "MANUAL_REVIEW", ["TOTAL_MISMATCH"])
        self.assertResult(run(make_invoice(gross="4481.30"), config=loose), "APPROVED")

    # Rounding tests use a 0.00 tolerance; otherwise a one-cent difference would be hidden.
    def test_vat_703_665_is_rounded_half_up(self):
        # Example from the specification: 3 x 1234.50 at 19% -> VAT 703.665 -> 703.67 (with float: 703.66)
        invoice = make_invoice(
            lines=(("3", "1234.50", "19"),), net="3703.50", vat="703.67", gross="4407.17",
            issue_date=date(2025, 7, 1), due_date=date(2025, 8, 1),
        )
        result = run(invoice, po=make_po(order_date="2025-06-01"),
                     config=make_config(amount_tolerance="0.00"))
        self.assertResult(result, "APPROVED")

    def test_total_is_the_sum_of_rounded_lines(self):
        # Three lines of 0.005: each line is 0.01, total is 0.03 (rounding at the end would give 0.02).
        invoice = make_invoice(lines=(("1", "0.005", "0"),) * 3,
                               net="0.03", vat="0.00", gross="0.03")
        self.assertResult(run(invoice, config=make_config(amount_tolerance="0.00")), "APPROVED")

    def test_vat_is_calculated_from_rounded_net(self):
        # Net 0.115 -> 0.12; VAT 21% of 0.12 = 0.0252 -> 0.03 (from unrounded 0.115 it would be 0.02).
        invoice = make_invoice(lines=(("1", "0.115", "21"),),
                               net="0.12", vat="0.03", gross="0.15")
        self.assertResult(run(invoice, config=make_config(amount_tolerance="0.00")), "APPROVED")


class VatRulesTests(RulesTestCase):
    def check(self, rate, issue, valid, delivery=None):
        result = run(invoice_for_vat(rate, issue, delivery), po=make_po(order_date="2025-01-01"))
        if valid:
            self.assertResult(result, "APPROVED")
        else:
            self.assertResult(result, "REJECTED", ["VAT_RATE_INVALID"])

    def test_rate_period_boundaries(self):
        cases = [
            ("19", date(2025, 7, 31), True),    # last day of the old rate
            ("19", date(2025, 8, 1), False),
            ("21", date(2025, 7, 31), False),
            ("21", date(2025, 8, 1), True),     # first day of the new rate
            ("9", date(2025, 7, 31), True),
            ("11", date(2025, 8, 1), True),
            ("11", date(2025, 7, 31), False),
            ("5", date(2025, 8, 1), False),
        ]
        for rate, issue, valid in cases:
            with self.subTest(rate=rate, issue=issue):
                self.check(rate, issue, valid)

    def test_zero_and_unknown_rates(self):
        self.check("0", date(2026, 11, 11), True)
        self.check("7", date(2026, 11, 11), False)

    def test_delivery_date_takes_priority_over_issue_date(self):
        # Recent issue date with 19%, but delivery during the period when 19% was valid.
        self.check("19", date(2026, 11, 11), True, delivery=date(2025, 7, 31))
        # Issue date in the old period, but delivery after 2025-08-01.
        self.check("19", date(2025, 7, 30), False, delivery=date(2025, 8, 5))

    def test_multiple_invalid_lines_produce_one_code(self):
        invoice = make_invoice(lines=(("1", "100.00", "19"), ("2", "50.00", "19")))
        self.assertResult(run(invoice), "REJECTED", ["VAT_RATE_INVALID"])

    def test_rate_is_compared_numerically(self):
        config = make_config(vat_rates=[
            {"rate": "21.00", "valid_from": "2025-08-01", "valid_to": None},
        ])
        self.assertResult(run(config=config), "APPROVED")        # the line has rate 21


class BalanceAndLimitTests(RulesTestCase):
    def net_only(self, amount, **fields):
        return make_invoice(lines=(("1", amount, "0"),), **fields)    # rate 0: gross = net

    def test_net_equal_to_balance_is_accepted(self):
        self.assertResult(run(self.net_only("3000.00"), balance="3000.00"), "APPROVED")

    def test_net_above_balance(self):
        self.assertResult(run(self.net_only("3000.00"), balance="2999.99"),
                          "MANUAL_REVIEW", ["PO_BALANCE_EXCEEDED"])

    def test_balance_is_consumed_only_by_approved_invoices(self):
        # Example from the specification: balance 5000, nets 3000, 2500, 2000, 100.
        balance = Decimal("5000.00")
        results = []
        for amount in ("3000.00", "2500.00", "2000.00", "100.00"):
            result = run(self.net_only(amount), balance=str(balance))
            results.append(result)
            if result[0] == "APPROVED":
                balance -= Decimal(amount)
        self.assertEqual(results, [
            ("APPROVED", ()),
            ("MANUAL_REVIEW", ("PO_BALANCE_EXCEEDED",)),
            ("APPROVED", ()),
            ("MANUAL_REVIEW", ("PO_BALANCE_EXCEEDED",)),
        ])

    def test_gross_equal_to_limit_is_accepted(self):
        big_po = make_po(remaining_balance="100000.00")
        self.assertResult(run(self.net_only("50000.00"), po=big_po, balance="100000.00"), "APPROVED")

    def test_gross_above_limit(self):
        big_po = make_po(remaining_balance="100000.00")
        self.assertResult(run(self.net_only("50000.01"), po=big_po, balance="100000.00"),
                          "MANUAL_REVIEW", ["ABOVE_APPROVAL_LIMIT"])

    def test_limit_depends_on_invoice_currency(self):
        po_eur = make_po(currency="EUR", remaining_balance="100000.00")
        ron_big = self.net_only("10000.01")                       # 10000.01 RON < 50000 RON
        self.assertResult(run(ron_big, balance="100000.00"), "APPROVED")
        eur_exact = self.net_only("10000.00", currency="EUR")
        eur_over = self.net_only("10000.01", currency="EUR")
        self.assertResult(run(eur_exact, po=po_eur, balance="100000.00"), "APPROVED")
        self.assertResult(run(eur_over, po=po_eur, balance="100000.00"),
                          "MANUAL_REVIEW", ["ABOVE_APPROVAL_LIMIT"])

    def test_limit_comes_from_configuration(self):
        config = make_config(auto_approval_limit={"RON": "1000.00", "EUR": "500.00"})
        self.assertResult(run(config=config), "MANUAL_REVIEW", ["ABOVE_APPROVAL_LIMIT"])


class CombinedTests(RulesTestCase):
    def test_srv_143867_example_from_specification(self):
        invoice = Invoice(
            number="SRV143867", supplier_cui="95070118", iban="RO49AAAA1B31007593840000",
            issue_date=date(2026, 11, 11), due_date=date(2026, 12, 11), delivery_date=None,
            currency="RON", po_number="PO512870",
            lines=(InvoiceLine(Decimal("3"), Decimal("1234.50"), Decimal("3703.50"), Decimal("19")),),
            net_total=Decimal("3703.50"), vat_total=Decimal("703.67"), gross_total=Decimal("4407.17"),
        )
        supplier = make_supplier(cui="95070118", status="BLOCKED", iban="RO11BBBB2C42008604950000")
        po = make_po(po_number="PO512870", supplier_cui="95070118", order_date="2026-10-01")
        self.assertResult(run(invoice, supplier=supplier, po=po),
                          "REJECTED", ["IBAN_MISMATCH", "SUPPLIER_BLOCKED", "VAT_RATE_INVALID"])

    def test_rejected_preserves_manual_review_codes(self):
        invoice = make_invoice(lines=(("1", "60000.00", "0"),))
        supplier = make_supplier(status="BLOCKED", iban="RO00AAAA0000000000000000")
        result = run(invoice, supplier=supplier, po=make_po(remaining_balance="100000.00"),
                     balance="100000.00")
        self.assertResult(result, "REJECTED",
                          ["ABOVE_APPROVAL_LIMIT", "IBAN_MISMATCH", "SUPPLIER_BLOCKED"])

    def test_unknown_supplier_and_invalid_rate(self):
        invoice = invoice_for_vat("19", date(2026, 11, 11))
        self.assertResult(run(invoice, supplier=None),
                          "REJECTED", ["SUPPLIER_UNKNOWN", "VAT_RATE_INVALID"])

    def test_missing_order_and_invalid_due_date(self):
        invoice = make_invoice(po_number=None, due_date=date(2026, 11, 10))
        self.assertResult(run(invoice, po=None),
                          "REJECTED", ["DUE_DATE_BEFORE_ISSUE", "PO_MISSING"])

    def test_only_manual_review_codes(self):
        invoice = make_invoice(gross="4481.50")
        self.assertResult(run(invoice, po=make_po(order_date="2026-11-12")),
                          "MANUAL_REVIEW", ["INVOICE_BEFORE_PO", "TOTAL_MISMATCH"])

    def test_codes_are_sorted_and_unique(self):
        invoice = make_invoice(
            lines=(("1", "60000.00", "19"), ("1", "10.00", "19")),
            gross="1.00", issue_date=date(2026, 11, 17), due_date=date(2026, 11, 1),
        )
        supplier = make_supplier(status="INACTIVE", iban="RO00AAAA0000000000000000")
        po = make_po(status="CLOSED", currency="EUR", order_date="2026-12-01")
        outcome, codes = run(invoice, supplier=supplier, po=po, balance="1.00")
        self.assertEqual(outcome, "REJECTED")
        self.assertEqual(list(codes), sorted(set(codes)))


class PurityTests(RulesTestCase):
    def test_does_not_modify_arguments(self):
        invoice = make_invoice(gross="9.00")
        supplier = make_supplier(status="BLOCKED")
        po = make_po(status="CLOSED")
        config = make_config()
        before = copy.deepcopy((supplier, po, config))
        run(invoice, supplier=supplier, po=po, config=config)
        self.assertEqual((supplier, po, config), before)

    def test_same_call_produces_same_result(self):
        invoice = make_invoice(gross="9.00")
        self.assertEqual(run(invoice), run(invoice))


class RemedieriTests(RulesTestCase):
    """The three fixes from the engine review. Fails if they are not applied."""

    def test_a_r06_is_skipped_when_supplier_is_unknown(self):
        # The order belongs to another tax ID, but the invoice supplier is not in the registry.
        result = run(supplier=None, po=make_po(supplier_cui="11111111"))
        self.assertResult(result, "MANUAL_REVIEW", ["SUPPLIER_UNKNOWN"])

    def test_registry_iban_is_normalized(self):
        supplier = make_supplier(iban="ro65 bpax 9398 8501 3252 3193")
        self.assertResult(run(supplier=supplier), "APPROVED")

    def test_business_date_can_be_text(self):
        self.assertResult(run(business_date="2026-11-16"), "APPROVED")
        invoice = make_invoice(issue_date=date(2026, 11, 17), due_date=date(2026, 12, 17))
        self.assertResult(run(invoice, business_date="2026-11-16"),
                          "REJECTED", ["INVOICE_DATE_IN_FUTURE"])


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