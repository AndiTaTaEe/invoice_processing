import unittest
from decimal import Decimal
from invoice_processing.normalize import FORMATS, InvalidNumber, normalize_cui, normalize_invoice_number, normalize_iban, parse_decimal, parse_percent, round_half_up, format_amount

class NormalizeCuiTests(unittest.TestCase):
    def test_normalize_cui(self):
        test_cases = [
            ("RO59686643", "59686643"),
            ("RO 59686643", "59686643"),
            ("ro59686643", "59686643"),
            ("59686643", "59686643"),
            (" RO 5968 6643 ", "59686643"),
            ("AB12345678", "AB12345678"),
            ("RORO123", "RO123"),
            ("12RO345", "12RO345"),
            ("R 0123", "R0123"),
            ("", ""),
        ] # test cases for normalize_cui function, with various input formats and expected normalized outputs
        for input_cui, expected in test_cases:
            with self.subTest(input_cui=input_cui):
                self.assertEqual(normalize_cui(input_cui), expected)

class NormalizeInvoiceNumberTests(unittest.TestCase):
    def test_normalize_invoice_number(self):
        test_cases = [
            ("FX-108636", "FX108636"),
            ("fx 0108636", "FX108636"),
            ("FX/108636", "FX108636"),
            ("FX-00108636", "FX108636"),
            ("BPA-2026-0042", "BPA20260042"),
            ("A-007-B", "A7B"),
            ("A-000-B", "A0B")
        ]

        for input_invoice_number, expected in test_cases:
            with self.subTest(input_invoice_number=input_invoice_number):
                self.assertEqual(normalize_invoice_number(input_invoice_number), expected)


class NormalizeIbanTests(unittest.TestCase):
    def test_normalize_iban(self):
        compact_iban = "RO49AAAA1B31007593840000"
        test_cases = [
            ("RO49 AAAA 1B31 0075 9384 0000", compact_iban),
            (compact_iban, compact_iban),
            ("ro49aaaa1b31007593840000", compact_iban),
            ("  RO49 AAAA 1B31 0075 9384 0000  ", compact_iban),
        ]
        for input_iban, expected in test_cases:
            with self.subTest(input_iban=input_iban):
                self.assertEqual(normalize_iban(input_iban), expected)

class ParseDecimalTests(unittest.TestCase):
    def test_format_ro(self):
       self.assertEqual(parse_decimal("3.703,50", *FORMATS["pdf_ro"]), Decimal("3703.50"))
       self.assertEqual(parse_decimal("1.234.567,89", *FORMATS["pdf_ro"]), Decimal("1234567.89"))
    def test_format_en(self):
        self.assertEqual(parse_decimal("3,703.50", *FORMATS["pdf_en"]), Decimal("3703.50"))
        self.assertEqual(parse_decimal("1,234,567.89", *FORMATS["pdf_en"]), Decimal("1234567.89"))
    def test_format_bilingual(self):
        self.assertEqual(parse_decimal("3,703.50", *FORMATS["pdf_bilingual"]), Decimal("3703.50"))
        self.assertEqual(parse_decimal("3", *FORMATS["pdf_bilingual"]), Decimal("3"))
    def test_format_xml(self):
        self.assertEqual(parse_decimal("3703.50", *FORMATS["xml"]), Decimal("3703.50"))
        self.assertEqual(parse_decimal("0.0500", *FORMATS["xml"]), Decimal("0.0500"))
    def test_text_not_a_number(self):
        with self.assertRaises(InvalidNumber):
            parse_decimal("N/A")
    def test_nan_is_rejected(self):
        # Decimal("NaN") is a valid Decimal object, but we want to reject it as an invalid number in our context
        with self.assertRaises(InvalidNumber):
            parse_decimal("NaN")

class ParsePercentTests(unittest.TestCase):
    def test_parse_percent(self):
        test_cases = [
            ("21%", Decimal("21")),
            (" 21 % ", Decimal("21")),
            ("21.00", Decimal("21"))
        ]
        for input_percent, expected in test_cases:
            with self.subTest(input_percent=input_percent):
                self.assertEqual(parse_percent(input_percent), expected)

    def test_parse_percent_invalid(self):
        with self.assertRaises(InvalidNumber):
            parse_percent("-5%")


class RoundHalfUpTests(unittest.TestCase):
    def test_round_half_up(self):
        test_cases = [
           ("0.005", "0.01"),
           ("703.665", "703.67"),
           ("0.125", "0.13"),
           ("1.004", "1.00"),
           ("5", "5.00")
        ] # comparing as strings to avoid floating point representation issues

        for input_value, expected in test_cases:
            with self.subTest(input_value=input_value):
                self.assertEqual(round_half_up(Decimal(input_value)), Decimal(expected))

class FormatAmountTests(unittest.TestCase):
    def test_format_amount(self):
        test_cases = [
            (Decimal("1700"), "1700.00"),
            (Decimal("1E+3"), "1000.00"),
            (Decimal("703.665"), "703.67")
        ]

        for input_value, expected in test_cases:
            with self.subTest(input_value=input_value):
                self.assertEqual(format_amount(input_value), expected)

    def test_number_too_large(self):
        with self.assertRaises(ValueError):
            format_amount(Decimal("10000000000000")) # 14 digits in integer part, should raise ValueError

if __name__ == "__main__":
    unittest.main()