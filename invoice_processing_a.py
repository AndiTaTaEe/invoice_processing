import logging, sys
import os
import json
from invoice_processing.http_client import HTTPClient, APIError, RunRequestLimit, path_segment
from invoice_processing.invoice import Unreadable
from invoice_processing.pdf_extractor import extract_pdf
from invoice_processing.xml_extractor import extract_xml

# set up logging to log to stderr with level INFO
logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(message)s")

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
    declared_type = content_type.split(";", 1)[0].strip().lower() # get the declared content type from the content_type header, ignoring any parameters

    extractors = {
        "application/pdf": ("pdf", extract_pdf),
        "application/xml": ("xml", extract_xml),
    }
    try:
        expected_type, extractor = extractors[declared_type]
    except KeyError:
        raise Unreadable(f"Unsupported content type: {declared_type}") from None
    detected_type = detect_document_type(data)
    if detected_type != "unknown" and detected_type != expected_type:
        logging.warning("Content type mismatch: declared=%s, detected=%s", declared_type, detected_type)
    return extractor(data)



def main():
    # read BASE_URL as first cli argument, otherwise read from BPA_BASE_URL
    if len(sys.argv) > 1:
        base_url  = sys.argv[1] # read base_url from first command line argument
    else:
        base_url = os.environ.get("BPA_BASE_URL") 
    if not base_url:
        logging.error("BASE_URL not provided. Please provide it as a command line argument or set BPA_BASE_URL environment variable.")
        sys.exit(2) 

    # create an HTTPClient with the base_url
    client = HTTPClient(base_url)

    # run a test request to /context, /config and /inbox endpoints to verify the client is working
    try:
        context = client.get("/context")
        config = client.get("/config")
        inbox = client.get("/inbox")
        history = client.get("/history")
        processed_document_ids = {
            entry["document_id"] for entry in history
        }
        for item in inbox:
            document_id = item["document_id"]

            if document_id in processed_document_ids:
                logging.info("Document %s already processed, skipping.", document_id)
                continue
            try:
                data = client.get_bytes(f"/documents/{path_segment(document_id)}")
                invoice = extract_invoice(data, item["content_type"])
                logging.info("Extracted %s: invoice=%s, supplier_cui=%s", document_id, invoice.number, invoice.supplier_cui)
            except Unreadable as err:
                logging.error("Extraction failed for %s: %s", document_id, err)
    except APIError as err:
        # if an APIError occurs, log the error details and exit with status code 1
        logging.error("APIError: %s", err)
        sys.exit(1)
    except RunRequestLimit as err:
        # if a RunRequestLimit error occurs, log the error details and exit with status code 1
        logging.error("RunRequestLimit: %s", err)
        sys.exit(1)
    # log the successful responses from the test requests
    logging.info("Context:\n%s", json.dumps(context, indent=2, ensure_ascii=False))
    logging.info("Config:\n%s", json.dumps(config, indent=2, ensure_ascii=False))
    logging.info("Inbox (%d documents):\n%s", len(inbox), json.dumps(inbox, indent=2, ensure_ascii=False))

    # if the test request is successful, exit with status code 0
    sys.exit(0)

if __name__ == "__main__":
    main()
    

