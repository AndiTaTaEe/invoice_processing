import xml.etree.ElementTree as ET
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
from .normalize import (
    FORMATS,
    normalize_cui,
    normalize_invoice_number,
    normalize_iban
)

# namespaces used in the XML files, to be used when searching for elements with find() and findall() methods - defined in the documentation
NAMESPACES = {
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
}

# relative paths to the Invoice Root, fara ".//"
P_NUMBER = "cbc:ID"
P_ISSUE = "cbc:IssueDate"
P_DUE = "cbc:DueDate"
P_CURRENCY = "cbc:DocumentCurrencyCode"
P_ORDER = "cac:OrderReference/cbc:ID"
P_DELIVERY = "cac:Delivery/cbc:ActualDeliveryDate"
P_CUI = "cac:AccountingSupplierParty/cac:Party/cac:PartyTaxScheme/cbc:CompanyID"
P_IBAN = "cac:PaymentMeans/cac:PayeeFinancialAccount/cbc:ID"
P_TOTAL_VAT = "cac:TaxTotal/cbc:TaxAmount"
P_TOTAL_NET = "cac:LegalMonetaryTotal/cbc:TaxExclusiveAmount"
P_TOTAL_GROSS = "cac:LegalMonetaryTotal/cbc:TaxInclusiveAmount"
P_LINE = "cac:InvoiceLine"

# relative paths to the InvoiceLine, fara ".//"
P_QUANTITY = "cbc:InvoicedQuantity"
P_UNIT_PRICE = "cac:Price/cbc:PriceAmount"
P_LINE_AMOUNT = "cbc:LineExtensionAmount"
P_VAT_RATE = "cac:Item/cac:ClassifiedTaxCategory/cbc:Percent"

def _text(parent, path):
    """ 
    get the text of an element for a given path, or None if the element isn't present
    """
    element = parent.find(path, NAMESPACES)
    return None if element is None else element.text

def _decimal(parent, path, name):
    return required_decimal(_text(parent, path), name, *FORMATS["xml"])

def _percent(parent, path, name):
    return required_percent(_text(parent, path), name, *FORMATS["xml"])

def _read_line(line, index):
    label = f"line {index}"
    return InvoiceLine(
        quantity=_decimal(line, P_QUANTITY, f"{label}: quantity"),
        unit_price=_decimal(line, P_UNIT_PRICE, f"{label}: unit price"),
        line_amount=_decimal(line, P_LINE_AMOUNT, f"{label}: line amount"),
        vat_rate=_percent(line, P_VAT_RATE, f"{label}: VAT rate"),
    )

def extract_xml(data: bytes) -> Invoice:
    # parse the bytes into an XML tree, raising an Unreadable exception if the XML is invalid or unreadable
    try:
        root = ET.fromstring(data)
    except ET.ParseError as err:
        raise Unreadable(f"Invalid XML: {err}") from err

    # extract the identifiers 
    number = required_normalized(_text(root, P_NUMBER), "invoice number", normalize_invoice_number)
    supplier_cui = required_normalized(_text(root, P_CUI), "supplier CUI", normalize_cui)
    iban = required_normalized(_text(root, P_IBAN), "IBAN", normalize_iban)
    currency = required_text(_text(root, P_CURRENCY), "currency").upper()

    # dates
    issue_date = required_date(_text(root, P_ISSUE), "iso", "issue date")
    due_date = required_date(_text(root, P_DUE), "iso", "due date")

    # optional fields - empty treated as None
    po_number = optional_text(_text(root, P_ORDER))
    delivery_date = optional_date(_text(root, P_DELIVERY), "iso", "delivery date")

    # lines - each line is a direct copy of the root
    line_elements = root.findall(P_LINE, NAMESPACES)
    if not line_elements: # the invoice must have at least one line, otherwise it is considered unreadable
        raise Unreadable("No invoice lines found")
    lines = tuple(_read_line(line, index) for index, line in enumerate(line_elements, start=1))

    # totals declared
    net_total = _decimal(root, P_TOTAL_NET, "net total")
    vat_total = _decimal(root, P_TOTAL_VAT, "VAT total")
    gross_total = _decimal(root, P_TOTAL_GROSS, "gross total")

    # returning the invoice
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