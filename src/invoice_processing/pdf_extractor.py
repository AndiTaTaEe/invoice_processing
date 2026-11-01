import io
from dataclasses import dataclass
from pypdf import PdfReader
from pypdf.errors import PyPdfError

from .extract_common import (
    optional_date,
    optional_text,
    required_date,
    required_decimal,
    required_normalized,
    required_percent,
    required_text
)
from .invoice import Invoice, InvoiceLine, Unreadable
from .normalize import normalize_cui, normalize_iban, normalize_invoice_number

_DIGITS = set("0123456789")
TABLE_SEP = " | "
LINE_CELLS = 7

# template configuration
@dataclass(frozen=True)
class Template:
    name: str
    title: str # the title of the invoice, e.g., "FACTURA" or "INVOICE"
    decimal: str # the decimal separator used in the invoice, e.g., "." or ","
    thousands: str # the thousands separator used in the invoice, e.g., "," or "."
    date_format: str # key from DATE_FORMATS - dmy or iso
    notes_label: str # the label used for the notes section, e.g., "Mentiuni" or "Notes"
    two_columns: bool # whether the invoice has two columns or not (only in english invoices)
    labels: dict # the labels used in the invoice, e.g., {"cui": "CUI", "iban": "IBAN", ...}

TEMPLATES = {
    "ro": Template(
        name="ro", title="FACTURA", decimal=",", thousands=".", date_format="dmy", notes_label="Mentiuni", two_columns=False,
        labels={
            "number": "Numar factura",
            "issue_date": "Data emiterii",
            "due_date": "Data scadentei",
            "delivery_date": "Data livrarii",
            "supplier_cui": "CUI furnizor",
            "iban": "IBAN furnizor",
            "po_number": "Comanda",
            "currency": "Moneda",
            "net_total": "Total fara TVA",
            "vat_total": "Total TVA",
            "gross_total": "Total de plata",
        },
    ),
    "en": Template(
        name="en", title="INVOICE", decimal=".", thousands=",", date_format="iso", notes_label="Notes", two_columns=True,
        labels={
            "number": "Invoice number",
            "issue_date": "Issue date",
            "due_date": "Due date",
            "delivery_date": "Delivery date",
            "supplier_cui": "Supplier VAT ID",
            "iban": "Supplier IBAN",
            "po_number": "Purchase order",
            "currency": "Currency",
            "net_total": "Net total",
            "vat_total": "VAT total",
            "gross_total": "Total due",
        },
    ),
    "bilingual": Template(
        name="bilingual", title="FACTURA / INVOICE", decimal=".", thousands=",", date_format="dmy", notes_label="Mentiuni / Notes", two_columns=False,
        labels={
            "number": "Numar / Number",
            "issue_date": "Data / Date",
            "due_date": "Scadenta / Due",
            "delivery_date": "Livrare / Delivery",
            "supplier_cui": "CUI / VAT ID",
            "iban": "IBAN",
            "po_number": "Comanda / PO",
            "currency": "Moneda / Currency",
            "net_total": "Total net / Net total",
            "vat_total": "TVA / VAT",
            "gross_total": "Total / Total due",
        },
    ),
}

# key values for the right column of the invoice (only for en invoices) 
# in case of the whitespaces being compressed, the key values are used to identify the right column values
COLUMN_MARKERS = ("Supplier: ", "Supplier VAT ID: ", "Supplier IBAN: ", "Customer: ", "Customer VAT ID: ")

# function for extracting the text from a PDF file line by line, using the pypdf library
def pdf_to_lines(data: bytes) -> list:
    """
    Extract the text from a PDF file and return it as a list of lines.
    If the PDF is unreadable or empty, an Unreadable exception is raised.
    """
    try:
        reader = PdfReader(io.BytesIO(data))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    except PyPdfError as err:
        raise Unreadable(f"PDF is unreadable: {err}") from err
    lines = [row for row in text.splitlines() if row.strip()] # remove empty lines
    if not lines:
        raise Unreadable("PDF is empty")
    return lines

def detect_template(first_row: str) -> Template:
    """
    detect the template of the invoice based on the first row of the PDF file, which is the title of the invoice. If the template is not recognized, an Unreadable exception is raised.
    """
    title = first_row.strip() # get the title of the invoice from the first row of the PDF
    for template in TEMPLATES.values():
        if title == template.title:
            return template 
    raise Unreadable(f"Unknown invoice title: {title}")

def split_label(row: str):
    """
    function for splittting the labels in key-value pairs, using the ":" separator, returns None if the row doesn't contain a label, or if the row is empty. The label and value are stripped of leading and trailing whitespace.
    """
    row = row.strip()
    label, sep, value = row.partition(": ") # split the row into label and value using the ":" separator
    if sep:
        return label.strip(), value.strip() # return the label and value, stripping any leading or trailing whitespace
    if row.endswith(":"):
        return row[:-1].strip(), "" # if the row ends with ":", return the label and an empty value
    return None

def _cut_points(piece: str) -> list:
    points = set()
    for marker in COLUMN_MARKERS:
        start = piece.find(marker, 1) # find the marker in the piece, starting from index 1 to avoid matching the first character
    while start != -1:
        points.add(start) # add the index of the marker to the set of points
        start = piece.find(marker, start + 1) # find the next occurrence of the marker in the piece, starting from the index after the last found marker
    return sorted(points) # return the sorted list of points where the markers were found

def split_segments(row: str) -> list:
    """
    split a row into segments, using double spaces as separators, and then further split each segment into parts using the cut points of the markers. The resulting segments are stripped of leading and trailing whitespace, and empty segments are ignored. Used for english written invoices, where there might be 2 columns of text in the same row 
    """
    segments = []
    for piece in row.split("  "):
        piece = piece.strip()
        if not piece:
            continue
        cuts = [0] + _cut_points(piece) + [len(piece)] # get the cut points for the piece, adding 0 and the length of the piece to the list of cuts
        for start, end in zip(cuts, cuts[1:]):
            part = piece[start:end].strip()
            if part:
                segments.append(part) # add the segment to the list of segments if it's not empty
    return segments

def classify_rows(rows: list, template: Template):
    """
    classify the rows of the PDF file into key-value pairs and table rows, using the template to identify the labels. The function returns a dictionary of fields and a list of table rows. The fields dictionary contains the labels as keys and the corresponding values as values. The table rows are returned as a list of strings, where each string is a row in the table.
    """
    fields = {}
    table_rows = []
    for row in rows:
        if TABLE_SEP in row:
            table_rows.append(row) # if the row contains the table separator, it's a table row
            continue
        first = split_label(row) # try to split the row into a label and value
        if first is None:
            continue
        if first[0] == template.notes_label:
            continue # ignore the notes section, as it doesn't contain any useful information for the invoice
        if template.two_columns:
            segments = split_segments(row)
        else:
            segments = [row] # if the template doesn't have two columns, treat the whole row as a single segment
        for segment in segments:
            parsed = split_label(segment)
            if parsed is None:
                continue
            label, value = parsed
            fields.setdefault(label, value) # add the label and value to the fields dictionary, using setdefault to avoid overwriting existing values
    return fields, table_rows

def parse_table_row(row: str, template: Template):
    """
    function for parsing a table row into an InvoiceLine object, using the template to identify the decimal and thousands separators. The function returns an InvoiceLine object with the parsed values. If the row is not a valid table row, None is returned.
    """
    cells = [cell.strip() for cell in row.split(TABLE_SEP)] # split the row into cells using the table separator, and strip leading and trailing whitespace from each cell
    if len(cells) != LINE_CELLS:
        raise Unreadable(f"Invalid table row with {len(cells)} cells instead of {LINE_CELLS}: {row}") # if the number of cells is not equal to the expected number of cells, raise an Unreadable exception
    if not cells[0] or not set(cells[0]).issubset(_DIGITS):
        return None # if the first cell is empty or contains non-digit characters, return None to indicate that the row is not a valid table row
    label = f"line {cells[0]}" # create a label for the line using the first cell, which is expected to be a line number
    dec, thousands = template.decimal, template.thousands
    return InvoiceLine(
        quantity=required_decimal(cells[3], f"{label}: quantity", dec, ""),
        unit_price=required_decimal(cells[4], f"{label}: unit price", dec, ""),
        line_amount=required_decimal(cells[5], f"{label}: line amount", dec, thousands),
        vat_rate=required_percent(cells[6], f"{label}: vat rate", dec, ""),
    )

def extract_pdf_lines(rows: list) -> Invoice:
    """
    extract the invoice data from the rows of the PDF file, using the template to identify the labels and parse the values. The function returns an Invoice object with the extracted values. If any required field is missing or invalid, an Unreadable exception is raised.
    """
    if not rows:
        raise Unreadable("PDF is empty")
    template = detect_template(rows[0])
    fields, table_rows = classify_rows(rows[1:], template)

    def get(key):
        return fields.get(template.labels[key])

    number = required_normalized(get("number"), "invoice number", normalize_invoice_number)
    supplier_cui = required_normalized(get("supplier_cui"), "supplier CUI", normalize_cui)
    iban = required_normalized(get("iban"), "supplier IBAN", normalize_iban)
    currency = required_text(get("currency"), "currency").upper()

    issue_date = required_date(get("issue_date"), template.date_format, "issue date")
    due_date = required_date(get("due_date"), template.date_format, "due date")

    po_number = optional_text(get("po_number"))
    delivery_date = optional_date(get("delivery_date"), template.date_format, "delivery date")

    parsed_lines = [parse_table_row(row, template) for row in table_rows]
    lines = tuple(line for line in parsed_lines if line is not None) # filter out any None values from the parsed lines, which indicate invalid table rows
    if not lines:
        raise Unreadable("No valid table rows found in PDF")
    dec, thousands = template.decimal, template.thousands
    net_total = required_decimal(get("net_total"), "net total", dec, thousands)
    vat_total = required_decimal(get("vat_total"), "vat total", dec, thousands)
    gross_total = required_decimal(get("gross_total"), "gross total", dec, thousands)
    return Invoice(
        number=number,
        supplier_cui=supplier_cui,
        iban=iban,
        issue_date=issue_date,
        due_date=due_date,
        delivery_date=delivery_date,
        currency=currency,
        po_number=po_number,
        lines=lines,
        net_total=net_total,
        vat_total=vat_total,
        gross_total=gross_total
    )

def extract_pdf(data: bytes) -> Invoice:
    return extract_pdf_lines(pdf_to_lines(data))