import json
import pathlib
import unittest
from datetime import date
from decimal import Decimal

from invoice_processing.xml_extractor import extract_xml
from invoice_processing.invoice import Unreadable
from invoice_processing.normalize import format_amount

CAC_NS = "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
CBC_NS = "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
INV_NS = "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"

CUR = ' currencyID="RON"'
UNIT = ' unitCode="C62"'

# (quantity, unit_price, line_amount, vat_rate)
DEFAULT_LINES = [("3", "1234.50", "3703.50", "19")]

def make_xml(*, number="SRV 143867", issue="2026-11-11", due="2026-12-11",
             currency="RON", po="PO512870", delivery=None,
             supplier_cui="RO 95070118", buyer_cui="RO11111111",
             iban="RO49 AAAA 1B31 0075 9384 0000",
             net="3703.50", vat="703.67", gross="4407.17",
             lines=DEFAULT_LINES, cac="cac", cbc="cbc", extras=True,
             supplier_name="Lumina Echipamente SRL", encoding="UTF-8") -> bytes:
    """Invoice XML generator for testing. Returns a bytes object containing the XML representation of an invoice with the specified parameters."""

    def c(tag, value, attrs=""):
        if value is None:
            return ""
        return f"<{cbc}:{tag}{attrs}>{value}</{cbc}:{tag}>"

    def line(index, quantity, unit_price, line_amount, vat_rate):
        return (
            f"<{cac}:InvoiceLine>{c('ID', index)}"
            f"{c('InvoicedQuantity', quantity, UNIT)}"
            f"{c('LineExtensionAmount', line_amount, CUR)}"
            # TaxTotal inside the line, not the one on the invoice
            f"<{cac}:TaxTotal>{c('TaxAmount', '999.99')}</{cac}:TaxTotal>"
            f"<{cac}:Item>{c('Description', 'Articol')}"
            f"<{cac}:ClassifiedTaxCategory>{c('Percent', vat_rate)}</{cac}:ClassifiedTaxCategory>"
            f"</{cac}:Item>"
            f"<{cac}:Price>{c('PriceAmount', unit_price, CUR)}</{cac}:Price>"
            f"</{cac}:InvoiceLine>"
        )

    contact = (f"<{cac}:Contact>{c('ElectronicMail', 'falsa@example.com')}</{cac}:Contact>"
               if extras else "")
    # Note: This is a placeholder note, not the actual invoice note
    note = c("Note", "Total de plata: N/A, scadenta 2026-02-31") if extras else ""
    order = (f"<{cac}:OrderReference>{c('ID', po)}</{cac}:OrderReference>"
             if po is not None else "")
    delivery_xml = (f"<{cac}:Delivery>{c('ActualDeliveryDate', delivery)}</{cac}:Delivery>"
                    if delivery is not None else "")

    supplier = (
        f"<{cac}:AccountingSupplierParty><{cac}:Party>"
        f"<{cac}:PartyName>{c('Name', supplier_name)}</{cac}:PartyName>"
        f"<{cac}:PartyTaxScheme>{c('CompanyID', supplier_cui)}"
        f"<{cac}:TaxScheme>{c('ID', 'VAT')}</{cac}:TaxScheme></{cac}:PartyTaxScheme>"
        f"{contact}</{cac}:Party></{cac}:AccountingSupplierParty>"
    )
    customer = (
        f"<{cac}:AccountingCustomerParty><{cac}:Party>"
        f"<{cac}:PartyTaxScheme>{c('CompanyID', buyer_cui)}</{cac}:PartyTaxScheme>"
        f"</{cac}:Party></{cac}:AccountingCustomerParty>"
    )
    payment = (
        f"<{cac}:PaymentMeans><{cac}:PayeeFinancialAccount>{c('ID', iban)}"
        f"</{cac}:PayeeFinancialAccount></{cac}:PaymentMeans>"
    )
    tax_total = (
        f"<{cac}:TaxTotal>{c('TaxAmount', vat, CUR)}"
        # sum from TaxSubtotal shouldn't be used
        f"<{cac}:TaxSubtotal>{c('TaxAmount', '111.11')}</{cac}:TaxSubtotal>"
        f"</{cac}:TaxTotal>"
    )
    legal = (
        f"<{cac}:LegalMonetaryTotal>{c('TaxExclusiveAmount', net, CUR)}"
        f"{c('TaxInclusiveAmount', gross, CUR)}</{cac}:LegalMonetaryTotal>"
    )

    parts = [
        f'<?xml version="1.0" encoding="{encoding}"?>',
        f'<Invoice xmlns="{INV_NS}" xmlns:{cac}="{CAC_NS}" xmlns:{cbc}="{CBC_NS}">',
        c("ID", number), c("IssueDate", issue), c("DueDate", due),
        c("DocumentCurrencyCode", currency), note,
        order, supplier, customer, delivery_xml, payment, tax_total, legal,
        "".join(line(i, *t) for i, t in enumerate(lines, start=1)),
        "</Invoice>",
    ]
    return "".join(parts).encode(encoding)


class ExtractXmlValidTests(unittest.TestCase):
    def test_clean_invoice(self):
        inv = extract_xml(make_xml())
        self.assertEqual(inv.number, "SRV143867")                   # normalized
        self.assertEqual(inv.supplier_cui, "95070118")              # normalized, RO prefix removed
        self.assertEqual(inv.iban, "RO49AAAA1B31007593840000")      # normalized
        self.assertEqual(inv.issue_date, date(2026, 11, 11))
        self.assertEqual(inv.due_date, date(2026, 12, 11))
        self.assertIsNone(inv.delivery_date)
        self.assertEqual(inv.currency, "RON")
        self.assertEqual(inv.po_number, "PO512870")
        self.assertEqual(inv.net_total, Decimal("3703.50"))
        self.assertEqual(inv.gross_total, Decimal("4407.17"))

        self.assertIsInstance(inv.lines, tuple)
        self.assertEqual(len(inv.lines), 1)
        line = inv.lines[0]
        self.assertEqual(line.quantity, Decimal("3"))
        self.assertEqual(line.unit_price, Decimal("1234.50"))
        self.assertEqual(line.line_amount, Decimal("3703.50"))
        self.assertEqual(line.vat_rate, Decimal("19"))

    def test_vat_total_is_the_invoice_total(self):
        # Decoys: 111.11 in TaxSubtotal and 999.99 in the line TaxTotal.
        self.assertEqual(extract_xml(make_xml(vat="703.67")).vat_total, Decimal("703.67"))

    def test_supplier_tax_id_is_not_the_buyer_tax_id(self):
        inv = extract_xml(make_xml(supplier_cui="RO 95070118", buyer_cui="RO11111111"))
        self.assertEqual(inv.supplier_cui, "95070118")

    def test_missing_order_and_delivery_are_none(self):
        inv = extract_xml(make_xml(po=None, delivery=None))
        self.assertIsNone(inv.po_number)
        self.assertIsNone(inv.delivery_date)

    def test_present_but_empty_order_is_none(self):
        for po in ("", "   "):
            with self.subTest(po=po):
                self.assertIsNone(extract_xml(make_xml(po=po)).po_number)

    def test_order_is_preserved_exactly_as_printed(self):
        # Only surrounding whitespace is stripped; nothing is normalized.
        inv = extract_xml(make_xml(po=" PO 512/870 "))
        self.assertEqual(inv.po_number, "PO 512/870")

    def test_present_delivery_date(self):
        inv = extract_xml(make_xml(delivery="2026-11-10"))
        self.assertEqual(inv.delivery_date, date(2026, 11, 10))

    def test_multiple_lines_in_document_order(self):
        lines = [
            ("2.125", "0.0500", "0.11", "21.00"),   # quantity with 3 decimals, price with 4, rate 21.00
            ("10", "99.9900", "999.90", "9"),
            ("1", "5", "5.00", "0"),
        ]
        inv = extract_xml(make_xml(lines=lines))
        self.assertEqual([l.line_amount for l in inv.lines],
                         [Decimal("0.11"), Decimal("999.90"), Decimal("5.00")])
        self.assertEqual(inv.lines[0].quantity, Decimal("2.125"))
        self.assertEqual(inv.lines[0].unit_price, Decimal("0.0500"))
        self.assertEqual(inv.lines[0].vat_rate, Decimal("21"))   # 21.00 == 21
        self.assertEqual([l.vat_rate for l in inv.lines], [Decimal("21"), Decimal("9"), Decimal("0")])
        for l in inv.lines:
            self.assertIsInstance(l.quantity, Decimal)
            self.assertIsInstance(l.unit_price, Decimal)

    def test_different_prefixes_for_namespace_aliases(self):
        self.assertEqual(
            extract_xml(make_xml()),
            extract_xml(make_xml(cac="c", cbc="b")),
        )

    def test_note_and_contact_are_ignored(self):
        self.assertEqual(
            extract_xml(make_xml(extras=True)),
            extract_xml(make_xml(extras=False)),
        )

    def test_bytes_with_declared_encoding(self):
        # encoding="ISO-8859-1" declaration and accented name: parsing must use bytes.
        data = make_xml(encoding="ISO-8859-1", supplier_name="Müller SRL")
        self.assertEqual(extract_xml(data).number, "SRV143867")

    def test_currency_is_converted_to_uppercase(self):
        self.assertEqual(extract_xml(make_xml(currency="eur")).currency, "EUR")


class ExtractXmlUnreadableTests(unittest.TestCase):
    def assertUnreadable(self, data):
        with self.assertRaises(Unreadable):
            extract_xml(data)

    def test_malformed_xml(self):
        valid = make_xml()
        cases = {
            "truncated halfway": valid[: len(valid) // 2],
            "missing closing tag": valid[: -len(b"</Invoice>")],
            "empty file": b"",
            "not XML (a PDF)": b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n",
        }
        for name, data in cases.items():
            with self.subTest(case=name):
                self.assertUnreadable(data)

    def test_missing_or_empty_required_fields(self):
        cases = [
            {"number": None},                      # element absent
            {"number": ""},                        # present but empty
            {"number": "   "},                     # whitespace only
            {"number": "---"},                     # empty after normalization
            {"supplier_cui": None},                # missing, but the buyer has a tax ID
            {"supplier_cui": "RO"},                # empty after normalization
            {"iban": None},
            {"iban": "   "},
            {"currency": None},
            {"issue": None},
            {"due": None},
            {"net": None},
            {"gross": None},                       # payment total missing
            {"vat": None},                         # only the TaxSubtotal amount exists
            {"lines": []},                         # no lines
        ]
        for kwargs in cases:
            with self.subTest(kwargs=kwargs):
                self.assertUnreadable(make_xml(**kwargs))

    def test_invalid_or_malformed_dates(self):
        cases = [
            {"issue": "2026-02-31"},               # February 31
            {"due": "2026-02-30"},
            {"issue": "2026-13-01"},               # month 13
            {"issue": "20261116"},                 # Python 3.11+'s fromisoformat would accept it
            {"issue": "2026-2-3"},                 # missing zero padding
            {"issue": "31.12.2026"},               # PDF format, not XML format
        ]
        for kwargs in cases:
            with self.subTest(kwargs=kwargs):
                self.assertUnreadable(make_xml(**kwargs))

    def test_non_numeric_amounts(self):
        cases = [
            {"net": "N/A"},                        # non-numeric net total
            {"gross": "see annex"},
            {"vat": "NaN"},                        # Decimal would accept it
            {"net": "3,703.50"},                   # thousands separator: not allowed in XML
            {"lines": [("N/A", "1", "1", "19")]},  # quantity
            {"lines": [("1", "abc", "1", "19")]},  # unit price
            {"lines": [("1", "1", "", "19")]},     # empty line amount
            {"lines": [("1", "1", "1", "N/A")]},   # VAT rate
            {"lines": [("1", "1", "1", None)]},    # missing VAT rate
            {"lines": [(None, "1", "1", "19")]},   # missing quantity
        ]
        for kwargs in cases:
            with self.subTest(kwargs=kwargs):
                self.assertUnreadable(make_xml(**kwargs))


FIXTURES = pathlib.Path(__file__).parent / "fixtures"


class RealFixtureTests(unittest.TestCase):
    """XML files downloaded from the hub, compared with expected values from the --json report.

    Uses tests/fixtures/<label>/expected.json (generated with expected_from_report)
    and the DOC-*.xml files in the same folder. The test is skipped if they do not exist.
    """

    def test_xml_files_downloaded_from_hub(self):
        verified = 0
        for expected_path in sorted(FIXTURES.glob("*/expected.json")):
            expected = json.loads(expected_path.read_text(encoding="utf-8"))
            for xml_path in sorted(expected_path.parent.glob("*.xml")):
                exp = expected.get(xml_path.stem)
                if exp is None:
                    continue
                verified += 1
                with self.subTest(file=f"{expected_path.parent.name}/{xml_path.name}"):
                    data = xml_path.read_bytes()
                    if exp.get("reason_codes") == ["EXTRACTION_FAILED"]:
                        with self.assertRaises(Unreadable):
                            extract_xml(data)
                        continue
                    inv = extract_xml(data)
                    got = {
                        "supplier_cui": inv.supplier_cui,
                        "invoice_number": inv.number,
                        "po_number": inv.po_number,
                        "currency": inv.currency,
                        "net_amount": format_amount(inv.net_total),
                        "gross_amount": format_amount(inv.gross_total),
                    }
                    self.assertEqual(
                        {k: got.get(k) for k in exp["extracted"]}, exp["extracted"]
                    )
        if verified == 0:
            self.skipTest("no XML files with expected.json exist in tests/fixtures/")


if __name__ == "__main__":
    unittest.main()