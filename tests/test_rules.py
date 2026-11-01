import unittest
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from invoice_processing.rules import evaluate


CONFIG = {
    "auto_approval_limit": {
        "RON": "50000.00",
        "EUR": "10000.00",
    },
    "amount_tolerance": "0.01",
    "vat_rates": [
        {
            "category": "standard",
            "rate": "19",
            "valid_from": "2017-01-01",
            "valid_to": "2025-07-31",
        },
        {
            "category": "standard",
            "rate": "21",
            "valid_from": "2025-08-01",
            "valid_to": None,
        },
        {
            "category": "reduced",
            "rate": "9",
            "valid_from": "2017-01-01",
            "valid_to": "2025-07-31",
        },
        {
            "category": "reduced",
            "rate": "5",
            "valid_from": "2017-01-01",
            "valid_to": "2025-07-31",
        },
        {
            "category": "reduced",
            "rate": "11",
            "valid_from": "2025-08-01",
            "valid_to": None,
        },
        {
            "category": "zero",
            "rate": "0",
            "valid_from": "2017-01-01",
            "valid_to": None,
        },
    ],
}

_MISSING = object()  # sentinel value to indicate a missing argument


def make_invoice(**changes):
    vat_rate = Decimal(str(changes.pop("vat_rate", "21")))
    line_amount = Decimal("3703.50")
    vat_total = (line_amount * vat_rate / Decimal("100")).quantize(Decimal("0.01"))
    gross_total = line_amount + vat_total
    line = SimpleNamespace(
        quantity=Decimal("3"),
        unit_price=Decimal("1234.50"),
        line_amount=line_amount,
        vat_rate=vat_rate,
    )

    values = dict(
        number="SRV143867",
        supplier_cui="95070118",
        iban="RO49AAAA1B31007593840000",
        issue_date=date(2026, 11, 11),
        due_date=date(2026, 12, 11),
        delivery_date=None,
        currency="RON",
        po_number="PO512870",
        lines=(line,),
        net_total=line_amount,
        vat_total=vat_total,
        gross_total=gross_total,
    )

    values.update(changes)
    return SimpleNamespace(**values)


def make_supplier(**changes):
    values = dict(
        cui="95070118",
        name="Lumina Echipamente SRL",
        status="ACTIVE",
        iban="RO49AAAA1B31007593840000",
        email="supplier@example.com",
    )

    values.update(changes)
    return values


def make_po(**changes):
    values = dict(
        po_number="PO512870",
        supplier_cui="95070118",
        currency="RON",
        order_date="2026-10-01",
        status="OPEN",
        total_amount="10000.00",
        remaining_balance="10000.00",
    )

    values.update(changes)
    return values


class RulesTests(unittest.TestCase):
    def evaluate(
        self,
        invoice=_MISSING,
        supplier=_MISSING,
        po=_MISSING,
        *,
        business_date=date(2026, 11, 16),
        effective_balance="10000.00",
        config=None,
    ):
        if invoice is _MISSING:
            invoice = make_invoice()
        if supplier is _MISSING:
            supplier = make_supplier()
        if po is _MISSING:
            po = make_po()

        return evaluate(
            invoice,
            supplier,
            po,
            config or CONFIG,
            business_date,
            Decimal(effective_balance),
        )

    def assert_result(self, expected_outcome, expected_codes, **kwargs):
        outcome, codes = self.evaluate(**kwargs)
        self.assertEqual(outcome, expected_outcome)
        self.assertEqual(codes, tuple(sorted(set(expected_codes))))

    def test_clean_invoice_is_approved(self):
        self.assert_result("APPROVED", [])

    def test_srv143867_example(self):
        supplier = make_supplier(
            status="BLOCKED",
            iban="RO00WRONGIBAN",
        )


        invoice = make_invoice(
            vat_rate="19",
        )

        self.assert_result(
            "REJECTED",
            [
                "IBAN_MISMATCH",
                "SUPPLIER_BLOCKED",
                "VAT_RATE_INVALID",
            ],
            invoice=invoice,
            supplier=supplier,
        )

    def test_inactive_supplier_rejects(self):
        self.assert_result(
            "REJECTED",
            ["SUPPLIER_INACTIVE"],
            supplier=make_supplier(status="INACTIVE"),
        )

    def test_blocked_supplier_rejects(self):
        self.assert_result(
            "REJECTED",
            ["SUPPLIER_BLOCKED"],
            supplier=make_supplier(status="BLOCKED"),
        )

    def test_supplier_iban_mismatch_requires_manual_review(self):
        self.assert_result(
            "MANUAL_REVIEW",
            ["IBAN_MISMATCH"],
            supplier=make_supplier(iban="RO00WRONGIBAN"),
        )

    def test_missing_supplier(self):
        self.assert_result(
            "MANUAL_REVIEW",
            ["SUPPLIER_UNKNOWN"],
            supplier=None,
        )

    def test_missing_supplier_skips_iban_check(self):
        self.assert_result(
            "MANUAL_REVIEW",
            ["SUPPLIER_UNKNOWN"],
            supplier=None,
        )

    def test_missing_po(self):
        self.assert_result(
            "MANUAL_REVIEW",
            ["PO_MISSING"],
            invoice=make_invoice(po_number=None),
        )

    def test_unknown_po(self):
        self.assert_result(
            "MANUAL_REVIEW",
            ["PO_NOT_FOUND"],
            po=None,
        )

    def test_po_supplier_mismatch_rejects(self):
        self.assert_result(
            "REJECTED",
            ["PO_SUPPLIER_MISMATCH"],
            po=make_po(supplier_cui="11111111"),
        )

    def test_closed_po_rejects(self):
        self.assert_result(
            "REJECTED",
            ["PO_CLOSED"],
            po=make_po(status="CLOSED"),
        )

    def test_currency_mismatch_rejects(self):
        self.assert_result(
            "REJECTED",
            ["CURRENCY_MISMATCH"],
            po=make_po(currency="EUR"),
        )

    def test_future_issue_date_rejects(self):
        self.assert_result(
            "REJECTED",
            ["INVOICE_DATE_IN_FUTURE"],
            invoice=make_invoice(issue_date=date(2026, 11, 17)),
        )

    def test_issue_date_equal_to_business_date_is_allowed(self):
        self.assert_result(
            "APPROVED",
            [],
            invoice=make_invoice(issue_date=date(2026, 11, 16)),
        )

    def test_due_date_before_issue_date_rejects(self):
        self.assert_result(
            "REJECTED",
            ["DUE_DATE_BEFORE_ISSUE"],
            invoice=make_invoice(
                issue_date=date(2026, 11, 11),
                due_date=date(2026, 11, 10),
            ),
        )

    def test_due_date_equal_to_issue_date_is_allowed(self):
        self.assert_result(
            "APPROVED",
            [],
            invoice=make_invoice(
                issue_date=date(2026, 11, 11),
                due_date=date(2026, 11, 11),
            ),
        )

    def test_invoice_before_po_requires_manual_review(self):
        self.assert_result(
            "MANUAL_REVIEW",
            ["INVOICE_BEFORE_PO"],
            po=make_po(order_date="2026-11-12"),
        )

    def test_issue_date_equal_to_po_date_is_allowed(self):
        self.assert_result(
            "APPROVED",
            [],
            invoice=make_invoice(issue_date=date(2026, 11, 11)),
            po=make_po(order_date="2026-11-11"),
        )

    def test_valid_vat_rate_21(self):
        invoice = make_invoice(
            lines=(
                SimpleNamespace(
                    quantity=Decimal("3"),
                    unit_price=Decimal("1234.50"),
                    line_amount=Decimal("3703.50"),
                    vat_rate=Decimal("21.00"),
                ),
            )
        )

        self.assert_result("APPROVED", [], invoice=invoice)

    def test_old_vat_rate_is_invalid_after_change(self):
        invoice = make_invoice(
            vat_rate="19",
        )

        self.assert_result(
            "REJECTED",
            ["VAT_RATE_INVALID"],
            invoice=invoice,
        )

    def test_vat_rate_is_valid_on_last_day_of_old_period(self):
        invoice = make_invoice(
            issue_date=date(2025, 7, 31),
            due_date=date(2025, 8, 1),
            vat_rate="19",
        )
        po = make_po(order_date="2025-01-01")

        self.assert_result(
            "APPROVED",
            [],
            invoice=invoice,
            po=po,
        )

    def test_delivery_date_is_used_for_vat_reference_date(self):
        invoice = make_invoice(
            issue_date=date(2025, 7, 31),
            delivery_date=date(2025, 8, 1),
            vat_rate="19",
        )
        po = make_po(order_date="2025-01-01")

        self.assert_result(
            "REJECTED",
            ["VAT_RATE_INVALID"],
            invoice=invoice,
            po=po,
        )

    def test_total_mismatch(self):
        self.assert_result(
            "MANUAL_REVIEW",
            ["TOTAL_MISMATCH"],
            invoice=make_invoice(gross_total=Decimal("4407.18")),
        )

    def test_total_difference_equal_to_tolerance_is_allowed(self):
        invoice = make_invoice(
            gross_total=Decimal("4481.25"),
        )

        config = {
            **CONFIG,
            "amount_tolerance": "0.01",
        }

        # Difference is exactly 0.01, therefore it must not trigger.
        self.assert_result(
            "APPROVED",
            [],
            invoice=invoice,
            config=config,
        )

    def test_line_total_mismatch(self):
        line = SimpleNamespace(
            quantity=Decimal("3"),
            unit_price=Decimal("1234.50"),
            line_amount=Decimal("3703.52"),
            vat_rate=Decimal("21"),
        )

        self.assert_result(
            "MANUAL_REVIEW",
            ["TOTAL_MISMATCH"],
            invoice=make_invoice(lines=(line,)),
        )

    def test_balance_equal_to_net_is_allowed(self):
        self.assert_result(
            "APPROVED",
            [],
            effective_balance="3703.50",
        )

    def test_balance_below_net_requires_manual_review(self):
        self.assert_result(
            "MANUAL_REVIEW",
            ["PO_BALANCE_EXCEEDED"],
            effective_balance="3703.49",
        )

    def test_approval_limit_equal_to_gross_is_allowed(self):
        invoice = make_invoice(
            lines=(
                SimpleNamespace(
                    quantity=Decimal("1"),
                    unit_price=Decimal("8264.46"),
                    line_amount=Decimal("8264.46"),
                    vat_rate=Decimal("21"),
                ),
            ),
            net_total=Decimal("8264.46"),
            vat_total=Decimal("1735.54"),
            gross_total=Decimal("10000.00"),
        )

        config = {
            **CONFIG,
            "auto_approval_limit": {
                "RON": "10000.00",
                "EUR": "10000.00",
            },
        }

        self.assert_result(
            "APPROVED",
            [],
            invoice=invoice,
            config=config,
        )

    def test_approval_limit_exceeded_requires_manual_review(self):
        invoice = make_invoice(
        lines=(
            SimpleNamespace(
                quantity=Decimal("1"),
                unit_price=Decimal("8264.47"),
                line_amount=Decimal("8264.47"),
                vat_rate=Decimal("21"),
            ),
        ),
        net_total=Decimal("8264.47"),
        vat_total=Decimal("1735.54"),
        gross_total=Decimal("10000.01"),
    )

        config = {
            **CONFIG,
            "auto_approval_limit": {
                "RON": "10000.00",
                "EUR": "10000.00",
            },
        }

        self.assert_result(
            "MANUAL_REVIEW",
            ["ABOVE_APPROVAL_LIMIT"],
            invoice=invoice,
            config=config,
        )

    def test_rejected_outcome_keeps_manual_review_codes(self):
        supplier = make_supplier(status="BLOCKED")
        invoice = make_invoice(po_number=None)

        self.assert_result(
            "REJECTED",
            [
                "PO_MISSING",
                "SUPPLIER_BLOCKED",
            ],
            invoice=invoice,
            supplier=supplier,
        )

    def test_reason_codes_are_unique_and_sorted(self):
        supplier = make_supplier(
            status="BLOCKED",
            iban="RO00WRONGIBAN",
        )
        invoice = make_invoice(
            vat_rate="21",
        )

        outcome, codes = self.evaluate(supplier=supplier, invoice=invoice)

        self.assertEqual(outcome, "REJECTED")
        self.assertEqual(
            codes,
            (
                "IBAN_MISMATCH",
                "SUPPLIER_BLOCKED",
            ),
        )

    def test_closed_po_does_not_skip_balance_rule(self):
        self.assert_result(
            "REJECTED",
            [
                "PO_BALANCE_EXCEEDED",
                "PO_CLOSED",
            ],
            po=make_po(status="CLOSED"),
            effective_balance="100.00",
        )

    def test_currency_mismatch_skips_balance_rule(self):
        self.assert_result(
            "REJECTED",
            ["CURRENCY_MISMATCH"],
            po=make_po(currency="EUR"),
            effective_balance="100.00",
        )

    def test_balance_sequence(self):
        invoice_amounts = [Decimal("3000"), Decimal("2500"), Decimal("2000"), Decimal("100")]
        balance = Decimal("5000")
        outcomes = []

        for index, amount in enumerate(invoice_amounts, start=1):
            invoice = make_invoice(
                number=f"INV{index}",
                net_total=amount,
                gross_total=amount,
                vat_total=Decimal("0"),
                lines=(
                    SimpleNamespace(
                        quantity=Decimal("1"),
                        unit_price=amount,
                        line_amount=amount,
                        vat_rate=Decimal("0"),
                    ),
                ),
            )

            outcome, codes = self.evaluate(
                invoice=invoice,
                effective_balance=balance,
            )
            outcomes.append((outcome, codes))

            if outcome == "APPROVED":
                balance -= amount

        self.assertEqual(
            outcomes,
            [
                ("APPROVED", ()),
                ("MANUAL_REVIEW", ("PO_BALANCE_EXCEEDED",)),
                ("APPROVED", ()),
                ("MANUAL_REVIEW", ("PO_BALANCE_EXCEEDED",)),
            ],
        )

        self.assertEqual(balance, Decimal("0"))


if __name__ == "__main__":
    unittest.main()