import logging, sys
import os
import json
from invoice_processing.http_client import HTTPClient, APIError, RunRequestLimit, path_segment
from invoice_processing.invoice import Unreadable
from invoice_processing.pdf_extractor import extract_pdf
from invoice_processing.xml_extractor import extract_xml
from invoice_processing.extract import extract_invoice

# set up logging to log to stderr with level INFO
logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(message)s")

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
        decided = {entry["document_id"]: entry for entry in history} # create a dictionary of document_id to history entry for quick lookup
        for item in inbox:
            document_id = item["document_id"]
            decision = decided.get(document_id)
            if decision is not None:
                logging.info("Document %s has already been processed with decision (%s, %s)", document_id, decision["source"], decision["outcome"])
                # TODO - EARLIER_RUN - raport ALREADY_PROCESSED; THIS_RUN - missing notification + raport RECORDED
                continue
            try:
                data = client.get_bytes(f"/documents/{path_segment(document_id)}")
                invoice = extract_invoice(data, item["content_type"])
                logging.info("Extracted %s: invoice=%s, supplier_cui=%s", document_id, invoice.number, invoice.supplier_cui)
            except Unreadable as err:
                logging.warning("Extraction failed for %s: %s", document_id, err)
                  # TODO: decizie MANUAL_REVIEW / EXTRACTION_FAILED, cu câmpurile de la supplier_cui la gross_amount = null
                  #       (se sare peste A-R15 și peste toate regulile, dar notificarea AP_REVIEWER se trimite)

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
    

