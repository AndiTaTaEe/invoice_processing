import unittest
from datetime import date
from decimal import Decimal

from invoice_processing.extract_common import (
    optional_date,
    optional_text,
    required_date,
    required_decimal,
    required_normalized,
    required_percent,
    required_text
)

from invoice_processing.invoice import Unreadable
from invoice_processing.normalize import FORMATS, normalize_cui, normalize_invoice_number

class RequiredTextTests(unittest.TestCase):
    def test_empty_and_none_is_unreadable(self):
        for raw in (None, "", "   ", "\n\t "):
            with self.subTest(raw=raw):
                with self.assertRaises(Unreadable):
                    required_text(raw, "invoice number")

    def test_strips_whitespaces(self):
        self.assertEqual(required_text("  FX-1 \n", "invoice number"), "FX-1")

    def test_internal_whitespaces_are_preserved(self):
        # invoice numbers can have spaces inside - SRV 143867
        self.assertEqual(required_text("SRV 143867", "invoice number"), "SRV 143867")

    def test_message_containes_field_name(self):
        with self.assertRaises(Unreadable) as context:
            required_text(None, "invoice number")
        self.assertIn("invoice number", str(context.exception))

class OptionalTextTests(unittest.TestCase):
    def test_cases(self):
        cases = [
            (None, None),
            ("  ", None),
            (" PO512870 ", "PO512870"),
            ("PO 512/870", "PO 512/870")
        ]
        for raw, expected in cases:
            with self.subTest(raw=raw):
                self.assertEqual(optional_text(raw), expected)

class RequiredDateTests(unittest.TestCase):
    def test_valid_dates(self):
        self.assertEqual(required_date("2026-11-16", "iso", "invoice date"), date(2026, 11, 16))
        self.assertEqual(required_date("16.11.2026", "dmy", "invoice date"), date(2026, 11, 16))

    def test_invalid_day(self):
        with self.assertRaises(Unreadable):
            required_date("31.02.2026", "dmy", "invoice date")

    def test_unexpected_format_is_denied(self):
        with self.assertRaises(Unreadable):
            required_date("20261116", "iso", "invoice date")

class OptionalDateTests(unittest.TestCase):
    def test_absent_or_empty_is_none(self):
        self.assertIsNone(optional_date(None, "iso", "delivery date"))
        self.assertIsNone(optional_date("  ", "iso", "delivery date"))

    def test_present_is_parsing(self):
        self.assertEqual(optional_date("2026-11-16", "iso", "delivery date"), date(2026, 11, 16))

    def test_present_but_invalid_is_unreadable(self):
        with self.assertRaises(Unreadable):
            optional_date("2026-02-31", "iso", "delivery date")  

class RequiredDecimalTests(unittest.TestCase):
    def test_format_ro(self):
        self.assertEqual(required_decimal("3.703,50", "total net", *FORMATS["pdf_ro"]), Decimal("3703.50"))
    
    def test_xml_implicit_format(self):
        self.assertEqual(required_decimal("3703.50", "total net"), Decimal("3703.50"))

    def test_text_instead_of_number_is_unreadable(self):
        for raw in ("N/A", "see annex", "NaN"):
            with self.subTest(raw=raw):
                with self.assertRaises(Unreadable):
                    required_decimal(raw, "total net")

    def test_none_is_unreadable(self):
        with self.assertRaises(Unreadable):
            required_decimal(None, "total net")

class RequiredPercentTests(unittest.TestCase):
    def test_valid_cases(self):
        cases = [
            ("21%", Decimal("21")),
            ("  21 %  ", Decimal("21")),
            ("21.00", Decimal("21")),
        ]
        for raw, expected in cases:
            with self.subTest(raw=raw):
                self.assertEqual(required_percent(raw, "VAT"), expected)

    def test_text_is_unreadable(self):
        with self.assertRaises(Unreadable):
            required_percent("N/A", "VAT")

class RequiredNormalizedTests(unittest.TestCase):
    def test_cui(self):
        self.assertEqual(required_normalized("RO 59686643", "CUI", normalize_cui), "59686643")

    def test_invoice_number(self):
        self.assertEqual(required_normalized("fx 0108636", "invoice number", normalize_invoice_number), "FX108636")

    def test_empty_after_normalize_cui(self):
        with self.assertRaises(Unreadable):
            required_normalized("RO", "CUI", normalize_cui)

    def test_empty_after_normalize_invoice_number(self):
        with self.assertRaises(Unreadable):
            required_normalized("---", "invoice number", normalize_invoice_number)

if __name__ == "__main__":
    unittest.main()