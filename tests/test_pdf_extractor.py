import json
import pathlib
import unittest
from datetime import date
from decimal import Decimal

from invoice_processing.invoice import Unreadable
from invoice_processing.pdf_extractor import extract_pdf, extract_pdf_lines, pdf_to_lines

# ---------------------------------------------------------------------------
# Test documents. Labels are copied from the specification, INDEPENDENT of the
# extractor code, so a typo in its configuration is caught this way.
# ---------------------------------------------------------------------------
TEMPLATES = ("ro", "en", "bilingual")

TITLES = {"ro": "FACTURA", "en": "INVOICE", "bilingual": "FACTURA / INVOICE"}
NOTES = {"ro": "Mentiuni", "en": "Notes", "bilingual": "Mentiuni / Notes"}

LABELS = {
    "ro": dict(number="Numar factura", issue="Data emiterii", due="Data scadentei",
               delivery="Data livrarii", cui="CUI furnizor", iban="IBAN furnizor",
               po="Comanda", currency="Moneda", net="Total fara TVA", vat="Total TVA",
               gross="Total de plata"),
    "en": dict(number="Invoice number", issue="Issue date", due="Due date",
               delivery="Delivery date", cui="Supplier VAT ID", iban="Supplier IBAN",
               po="Purchase order", currency="Currency", net="Net total", vat="VAT total",
               gross="Total due"),
    "bilingual": dict(number="Numar / Number", issue="Data / Date", due="Scadenta / Due",
                      delivery="Livrare / Delivery", cui="CUI / VAT ID", iban="IBAN",
                      po="Comanda / PO", currency="Moneda / Currency",
                      net="Total net / Net total", vat="TVA / VAT", gross="Total / Total due"),
}

# Header row: 7 cells, with the first one non-numeric (exact forms may differ in real documents).
HEADERS = {
    "ro": "Nr | Cod articol | Descriere | Cantitate | Pret unitar | Valoare | Cota TVA",
    "en": "No | Item code | Description | Quantity | Unit price | Amount | VAT rate",
    "bilingual": "Nr / No | Cod / Code | Descriere / Description | Cant. / Qty | Pret / Price | Valoare / Amount | TVA / VAT",
}

SUPPLIER = {
    "ro": ["Furnizor: Tara Verde Agro SRL"],
    "en": ["Supplier: Tara Verde Agro SRL"],
    "bilingual": ["Furnizor / Supplier: Tara Verde Agro SRL"],
}
BUYER = {
    "ro": ["Cumparator: Industrii Demo BPA SRL", "CUI cumparator: RO12345674"],
    "en": ["Customer: Industrii Demo BPA SRL", "Customer VAT ID: RO12345674"],
    "bilingual": ["Client / Customer: Industrii Demo BPA SRL",
                  "CUI client / Customer VAT ID: RO12345674"],
}

# Values written in each template's format, but with the same canonical result.
VALUES = {
    "ro": dict(number="FCT-558083", issue="11.11.2026", due="11.12.2026", delivery=None,
               cui="RO77568379", iban="RO65BPAX9398850132523193", po="PO-988911",
               currency="RON", net="3.703,50", vat="777,74", gross="4.481,24"),
    "en": dict(number="FCT 558083", issue="2026-11-11", due="2026-12-11", delivery=None,
               cui="ro 77568379", iban="RO65 BPAX 9398 8501 3252 3193", po="PO-988911",
               currency="RON", net="3,703.50", vat="777.74", gross="4,481.24"),
    "bilingual": dict(number="fct-0558083", issue="11.11.2026", due="11.12.2026", delivery=None,
                      cui="77568379", iban="ro65bpax9398850132523193", po="PO-988911",
                      currency="RON", net="3,703.50", vat="777.74", gross="4,481.24"),
}
LINES = {
    "ro": ["1 | SKU-198698 | Materiale de birou | 3 | 1234,50 | 3.703,50 | 21%"],
    "en": ["1 | SKU-198698 | Office supplies | 3 | 1234.50 | 3,703.50 | 21%"],
    "bilingual": ["1 | SKU-198698 | Materiale / Supplies | 3 | 1234.50 | 3,703.50 | 21%"],
}
DELIVERY_OK = {"ro": "10.11.2026", "en": "2026-11-10", "bilingual": "10.11.2026"}
DELIVERY_BAD = {"ro": "31.02.2026", "en": "2026-02-31", "bilingual": "31.02.2026"}
REQUIRED = ["number", "issue", "due", "cui", "iban", "currency", "net", "vat", "gross"]


def note_row(tpl):
    # No consecutive spaces or " | ", as in the specification; contains labels, amounts, and fake dates.
    return (f"{NOTES[tpl]}: Total de plata: 1,00 RON; Numar factura: FAKE-1; "
            "Supplier VAT ID: 99999999; Data emiterii: 01.01.2020; Total / Total due: 1.00")


def make_rows(tpl, *, notes=None, lines=None, header=True, **over):
    """Invoice rows. A None value omits the row; "" leaves the label without a value."""
    unknown = set(over) - set(VALUES[tpl])
    if unknown:
        raise KeyError(f"unknown test fields: {unknown}")
    values = {**VALUES[tpl], **over}
    labels = LABELS[tpl]

    def row(key):
        value = values[key]
        if value is None:
            return []
        return [f"{labels[key]}:" if value == "" else f"{labels[key]}: {value}"]

    rows = [TITLES[tpl]]
    if notes == "top":
        rows.append(note_row(tpl))
    for key in ("number", "issue", "due", "delivery"):
        rows += row(key)
    rows += SUPPLIER[tpl] + row("cui") + row("iban") + BUYER[tpl]
    rows += row("po") + row("currency")
    if header:
        rows.append(HEADERS[tpl])
    rows += LINES[tpl] if lines is None else lines
    if notes == "bottom":
        rows.append(note_row(tpl))
    for key in ("net", "vat", "gross"):
        rows += row(key)
    return rows


def en_two_column_rows(sep):
    return [
        "INVOICE",
        f"Invoice number: SRV 143867{sep}Supplier: Lumina Echipamente SRL",
        f"Issue date: 2026-11-11{sep}Supplier VAT ID: RO95070118",
        f"Due date: 2026-12-11{sep}Supplier IBAN: RO49 AAAA 1B31 0075 9384 0000",
        f"Currency: RON{sep}Customer: Industrii Demo BPA SRL",
        f"Purchase order: PO512870{sep}Customer VAT ID: RO12345674",
        HEADERS["en"],
        "1 | SKU-1 | Cabluri | 3 | 1234.50 | 3,703.50 | 19%",
        "Net total: 3,703.50",
        "VAT total: 703.67",
        "Total due: 4,407.17",
    ]


def make_pdf(rows):
    """Minimal PDF with one text line per row (ASCII only, without parentheses or backslashes)."""
    stream = "BT /F1 10 Tf 12 TL 40 800 Td\n" + "".join(f"({r}) Tj T*\n" for r in rows) + "ET"
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
        "/Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = "%PDF-1.4\n"
    offsets = []
    for i, obj in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{obj}\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n"
    out += "".join(f"{off:010d} 00000 n \n" for off in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    return out.encode("latin-1")


class PdfTestCase(unittest.TestCase):
    def assertCanonical(self, inv):
        """Expected result for invoices from make_rows, regardless of the template."""
        self.assertEqual(inv.number, "FCT558083")
        self.assertEqual(inv.supplier_cui, "77568379")          # not the buyer's
        self.assertEqual(inv.iban, "RO65BPAX9398850132523193")
        self.assertEqual(inv.issue_date, date(2026, 11, 11))
        self.assertEqual(inv.due_date, date(2026, 12, 11))
        self.assertIsNone(inv.delivery_date)
        self.assertEqual(inv.currency, "RON")
        self.assertEqual(inv.po_number, "PO-988911")
        self.assertEqual(inv.net_total, Decimal("3703.50"))
        self.assertEqual(inv.vat_total, Decimal("777.74"))
        self.assertEqual(inv.gross_total, Decimal("4481.24"))
        self.assertIsInstance(inv.lines, tuple)
        self.assertEqual(len(inv.lines), 1)
        line = inv.lines[0]
        self.assertEqual(line.quantity, Decimal("3"))
        self.assertEqual(line.unit_price, Decimal("1234.50"))
        self.assertEqual(line.line_amount, Decimal("3703.50"))
        self.assertEqual(line.vat_rate, Decimal("21"))

    def assertUnreadable(self, rows):
        with self.assertRaises(Unreadable):
            extract_pdf_lines(rows)


# ---------------------------------------------------------------------------
class TemplateTests(PdfTestCase):
    def test_each_template_is_read_correctly(self):
        for tpl in TEMPLATES:
            with self.subTest(template=tpl):
                self.assertCanonical(extract_pdf_lines(make_rows(tpl)))

    def test_title_must_match_the_entire_row(self):
        # Romanian labels under the bilingual title: if the title were read as "starts with FACTURA",
        # the document would be treated as Romanian and pass; with equality, the labels do not match.
        rows = make_rows("ro")
        rows[0] = "FACTURA / INVOICE"
        self.assertUnreadable(rows)

    def test_unknown_title_or_missing_rows(self):
        for title in ("FACTURA PROFORMA", "INVOICE 2", "Pagina 1"):
            with self.subTest(title=title):
                rows = make_rows("ro")
                rows[0] = title
                self.assertUnreadable(rows)
        self.assertUnreadable([])


class NotesTests(PdfTestCase):
    def test_notes_are_ignored_wherever_they_appear(self):
        for tpl in TEMPLATES:
            for position in ("top", "bottom"):
                with self.subTest(template=tpl, position=position):
                    self.assertCanonical(extract_pdf_lines(make_rows(tpl, notes=position)))

    def test_notes_do_not_fill_missing_fields(self):
        # The total due is missing; "Total de plata: 1,00" in the notes cannot replace it.
        for tpl in TEMPLATES:
            with self.subTest(template=tpl):
                self.assertUnreadable(make_rows(tpl, notes="bottom", gross=None))


class TableTests(PdfTestCase):
    def test_header_is_not_a_line(self):
        for tpl in TEMPLATES:
            with self.subTest(template=tpl):
                self.assertEqual(len(extract_pdf_lines(make_rows(tpl)).lines), 1)

    def test_no_lines(self):
        for tpl in TEMPLATES:
            with self.subTest(template=tpl):
                self.assertUnreadable(make_rows(tpl, lines=[]))

    def test_table_row_with_wrong_number_of_cells(self):
        cases = {
            "6 cells": "1 | SKU | Descriere | 3 | 1234,50 | 3.703,50",
            "8 cells": "1 | SKU | Descriere | 3 | 1234,50 | 3.703,50 | 21% | extra",
        }
        for name, row in cases.items():
            with self.subTest(case=name):
                self.assertUnreadable(make_rows("ro", lines=[row]))

    def test_multiple_lines_and_numeric_formats(self):
        lines = [
            "1 | A-1 | Primul | 2,125 | 0,0500 | 0,11 | 21%",           # quantity with 3 decimals, price with 4
            "2 | A-2 | Al doilea | 10 | 1234,5678 | 12.345,68 | 9%",    # thousands separator in amount
            "10 | A-3 | Al zecelea | 1 | 5,00 | 5,00 | 0%",
        ]
        inv = extract_pdf_lines(make_rows("ro", lines=lines))
        self.assertEqual([l.line_amount for l in inv.lines],
                         [Decimal("0.11"), Decimal("12345.68"), Decimal("5.00")])
        self.assertEqual(inv.lines[0].quantity, Decimal("2.125"))
        self.assertEqual(str(inv.lines[0].unit_price), "0.0500")
        self.assertEqual([l.vat_rate for l in inv.lines], [Decimal("21"), Decimal("9"), Decimal("0")])

    def test_non_numeric_quantity_or_price(self):
        for cell in ("N/A", "see annex", ""):
            with self.subTest(cell=cell):
                row = f"1 | SKU | Descriere | {cell} | 1234,50 | 3.703,50 | 21%"
                self.assertUnreadable(make_rows("ro", lines=[row]))


class FieldTests(PdfTestCase):
    def test_missing_or_empty_required_fields(self):
        for tpl in TEMPLATES:
            for key in REQUIRED:
                for value in (None, ""):
                    with self.subTest(template=tpl, field=key, value=value):
                        self.assertUnreadable(make_rows(tpl, **{key: value}))

    def test_non_numeric_amounts(self):
        for tpl in TEMPLATES:
            for key in ("net", "vat", "gross"):
                with self.subTest(template=tpl, field=key):
                    self.assertUnreadable(make_rows(tpl, **{key: "N/A"}))

    def test_empty_after_normalization(self):
        for tpl in TEMPLATES:
            with self.subTest(template=tpl):
                self.assertUnreadable(make_rows(tpl, number="---"))
                self.assertUnreadable(make_rows(tpl, cui="RO"))

    def test_thousands_separator_in_totals(self):
        inv = extract_pdf_lines(make_rows("ro", gross="1.234.567,89"))
        self.assertEqual(inv.gross_total, Decimal("1234567.89"))
        inv = extract_pdf_lines(make_rows("en", gross="1,234,567.89"))
        self.assertEqual(inv.gross_total, Decimal("1234567.89"))

    def test_invalid_or_wrongly_formatted_dates(self):
        for tpl in TEMPLATES:
            with self.subTest(template=tpl):
                self.assertUnreadable(make_rows(tpl, issue=DELIVERY_BAD[tpl]))   # February 31
        self.assertUnreadable(make_rows("ro", issue="2026-11-11"))               # English format in a Romanian template
        self.assertUnreadable(make_rows("en", issue="11.11.2026"))               # Romanian format in an English template

    def test_delivery_date(self):
        for tpl in TEMPLATES:
            with self.subTest(template=tpl):
                inv = extract_pdf_lines(make_rows(tpl, delivery=DELIVERY_OK[tpl]))
                self.assertEqual(inv.delivery_date, date(2026, 11, 10))
                self.assertUnreadable(make_rows(tpl, delivery=DELIVERY_BAD[tpl]))

    def test_order_is_preserved_exactly_as_printed(self):
        for tpl in TEMPLATES:
            with self.subTest(template=tpl):
                inv = extract_pdf_lines(make_rows(tpl, po="PO 512/870"))
                self.assertEqual(inv.po_number, "PO 512/870")

    def test_missing_or_empty_order_is_none(self):
        for tpl in TEMPLATES:
            for value in (None, ""):
                with self.subTest(template=tpl, value=value):
                    self.assertIsNone(extract_pdf_lines(make_rows(tpl, po=value)).po_number)

    def test_number_with_spaces_remains_intact(self):
        for tpl in TEMPLATES:
            with self.subTest(template=tpl):
                inv = extract_pdf_lines(make_rows(tpl, number="SRV 143867"))
                self.assertEqual(inv.number, "SRV143867")

    def test_currency_is_uppercase(self):
        self.assertEqual(extract_pdf_lines(make_rows("ro", currency="eur")).currency, "EUR")


class LabelTrapTests(PdfTestCase):
    def test_buyer_tax_id_does_not_replace_supplier_tax_id(self):
        # The buyer tax ID row is in the document; the supplier tax ID is missing.
        for tpl in TEMPLATES:
            with self.subTest(template=tpl):
                self.assertUnreadable(make_rows(tpl, cui=None))

    def test_similar_labels_are_not_confused(self):
        for tpl in TEMPLATES:
            with self.subTest(template=tpl):
                labels = LABELS[tpl]
                extra = [f"{labels['po']} veche: PO-VECHI",
                         f"{labels['gross']} estimat: 1.00",
                         f"{labels['cui']} vechi: 11111111"]
                rows = make_rows(tpl)
                at = rows.index(HEADERS[tpl])
                self.assertCanonical(extract_pdf_lines(rows[:at] + extra + rows[at:]))

    def test_first_occurrence_of_repeated_label_is_kept(self):
        # This is the extractor's selected policy; update this test if it changes.
        rows = make_rows("ro") + [f"{LABELS['ro']['currency']}: EUR"]
        self.assertEqual(extract_pdf_lines(rows).currency, "RON")


class TwoColumnTests(PdfTestCase):
    def check(self, inv):
        self.assertEqual(inv.number, "SRV143867")
        self.assertEqual(inv.supplier_cui, "95070118")           # not 12345674 (Customer VAT ID)
        self.assertEqual(inv.iban, "RO49AAAA1B31007593840000")
        self.assertEqual(inv.po_number, "PO512870")
        self.assertEqual(inv.currency, "RON")
        self.assertEqual(inv.issue_date, date(2026, 11, 11))
        self.assertEqual(inv.gross_total, Decimal("4407.17"))

    def test_multiple_spaces_between_columns(self):
        for sep in ("  ", "   ", "     "):
            with self.subTest(spaces=len(sep)):
                self.check(extract_pdf_lines(en_two_column_rows(sep)))

    def test_spaces_are_collapsed_by_extractor(self):
        self.check(extract_pdf_lines(en_two_column_rows(" ")))

    def test_both_variants_produce_the_same_invoice(self):
        self.assertEqual(extract_pdf_lines(en_two_column_rows("   ")),
                         extract_pdf_lines(en_two_column_rows(" ")))


class PdfLayerTests(PdfTestCase):
    # These depend on pypdf's behavior with the minimal PDF generated by make_pdf.
    def test_rows_are_preserved(self):
        rows = ["FACTURA", "Numar factura: FCT-1", "Data emiterii: 11.11.2026",
                "1 | A | B | 3 | 1,00 | 3,00 | 21%"]
        self.assertEqual(pdf_to_lines(make_pdf(rows)), rows)

    def test_complete_pdf_to_invoice_pipeline(self):
        for tpl in TEMPLATES:
            with self.subTest(template=tpl):
                self.assertEqual(extract_pdf(make_pdf(make_rows(tpl))),
                                 extract_pdf_lines(make_rows(tpl)))

    def test_files_that_are_not_pdf(self):
        for data in (b"", b"acesta nu este un PDF", b"<?xml version='1.0'?><Invoice/>"):
            with self.subTest(bytes=data[:12]):
                with self.assertRaises(Unreadable):
                    extract_pdf(data)

    def test_pdf_without_text(self):
        with self.assertRaises(Unreadable):
            extract_pdf(make_pdf([]))


FIXTURES = pathlib.Path(__file__).parent / "fixtures"


class RealFixtureTests(unittest.TestCase):
    """PDF files downloaded from the hub, compared with values from expected.json (the --json report)."""

    def test_pdf_files_downloaded_from_hub(self):
        from invoice_processing.normalize import format_amount

        verified = 0
        for expected_path in sorted(FIXTURES.glob("*/expected.json")):
            expected = json.loads(expected_path.read_text(encoding="utf-8"))
            for pdf_path in sorted(expected_path.parent.glob("*.pdf")):
                exp = expected.get(pdf_path.stem)
                if exp is None:
                    continue
                verified += 1
                with self.subTest(file=f"{expected_path.parent.name}/{pdf_path.name}"):
                    data = pdf_path.read_bytes()
                    if exp.get("reason_codes") == ["EXTRACTION_FAILED"]:
                        with self.assertRaises(Unreadable):
                            extract_pdf(data)
                        continue
                    inv = extract_pdf(data)
                    got = {
                        "supplier_cui": inv.supplier_cui,
                        "invoice_number": inv.number,      # hub key, not the field name
                        "po_number": inv.po_number,
                        "currency": inv.currency,
                        "net_amount": format_amount(inv.net_total),
                        "gross_amount": format_amount(inv.gross_total),
                    }
                    self.assertEqual({k: got[k] for k in exp["extracted"]}, exp["extracted"])
        if verified == 0:
            self.skipTest("no PDF files with expected.json exist in tests/fixtures/")


if __name__ == "__main__":
    unittest.main()