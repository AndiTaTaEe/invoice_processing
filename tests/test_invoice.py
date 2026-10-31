import unittest
from dataclasses import FrozenInstanceError
from datetime import date
from decimal import Decimal

from invoice_processing.invoice import Invoice, InvoiceLine, Unreadable

def create_invoice(**changes):
    """
    test invoice with default values, can be overridden by passing keyword arguments
    """
    line = InvoiceLine(
        quantity=Decimal("3"),
        unit_price=Decimal("1234.50"),
        line_amount=Decimal("3703.50"),
        vat_rate=Decimal("19"),
    )
    fields = dict(
        number="SRV143867",
        supplier_cui="95070118",
        iban="RO49AAAA1B31007593840000",
        issue_date=date(2026, 11, 11),
        due_date=date(2026, 12, 11),
        delivery_date=None,
        currency="RON",
        po_number="PO512870",
        lines=(line,),
        net_total=Decimal("3703.50"),
        vat_total=Decimal("703.67"),
        gross_total=Decimal("4407.17"),
    )
    fields.update(changes)
    return Invoice(**fields)

class InvoiceModelTests(unittest.TestCase):
    def test_fields_are_read_back(self):
        inv = create_invoice()
        self.assertEqual(inv.number, "SRV143867")
        self.assertEqual(inv.supplier_cui, "95070118")
        self.assertEqual(inv.iban, "RO49AAAA1B31007593840000")
        self.assertEqual(inv.issue_date, date(2026, 11, 11))
        self.assertEqual(inv.due_date, date(2026, 12, 11))
        self.assertIsNone(inv.delivery_date)
        self.assertEqual(inv.currency, "RON")
        self.assertEqual(inv.po_number, "PO512870")
        self.assertEqual(inv.net_total, Decimal("3703.50"))
        self.assertEqual(inv.vat_total, Decimal("703.67"))
        self.assertEqual(inv.gross_total, Decimal("4407.17"))

        self.assertEqual(len(inv.lines), 1)
        line = inv.lines[0]
        self.assertEqual(line.quantity, Decimal("3"))
        self.assertEqual(line.unit_price, Decimal("1234.50"))
        self.assertEqual(line.line_amount, Decimal("3703.50"))
        self.assertEqual(line.vat_rate, Decimal("19"))

    def test_objects_are_immutable(self):
        inv = create_invoice()
        with self.assertRaises(FrozenInstanceError):
            inv.number = "OTHER"
        with self.assertRaises(FrozenInstanceError):
            inv.lines[0].vat_rate = Decimal("21")

    def test_lines_are_tuple(self):
        inv = create_invoice()
        self.assertIsInstance(inv.lines, tuple)
        # a tuple cannot be modified, so trying to append to it should raise an AttributeError
        with self.assertRaises(AttributeError):
            inv.lines.append(inv.lines[0])


class UnreadableTests(unittest.TestCase):
    def test_preserves_message_and_is_not_value_error(self):
        message = "Document is unreadable"
        with self.assertRaises(Unreadable) as context:
            raise Unreadable(message)

        self.assertEqual(str(context.exception), message)
        self.assertIsInstance(context.exception, Exception)
        self.assertNotIsInstance(context.exception, ValueError)


if __name__ == "__main__":
    unittest.main()