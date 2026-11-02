import logging
from .invoice import Unreadable
from .pdf_extractor import extract_pdf
from .xml_extractor import extract_xml

EXTRACTORS = {
    "pdf": extract_pdf,
    "xml": extract_xml,
}

DECLARED = {
    "application/pdf": "pdf",
    "application/xml": "xml",
    "text/xml": "xml",
}

def detect_document_type(data: bytes) -> str:
    if data.startswith(b"%PDF"):
        return "pdf"
    if data.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"<"):
        return "xml"
    return "unknown"

def extract_invoice(data: bytes, content_type: str):
    """
    extract an invoice from the given data, using the appropriate extractor based on the declared content type. If the declared content type is not supported, raise an Unreadable exception. If the detected document type does not match the expected type, log a warning message.
    """
    mime = content_type.split(";", 1)[0].strip().lower() # get the declared content type from the content_type header, ignoring any parameters

    declared = DECLARED.get(mime)
    detected = detect_document_type(data)

    if declared and detected != "unknown" and declared != detected:
        logging.warning("Declared content type %s does not match detected document type %s", declared, detected)
    
    kind = detected if detected != "unknown" else declared 
    if kind is None:
        raise Unreadable(f"Unsupported content type: {mime}")
    return EXTRACTORS[kind](data)

