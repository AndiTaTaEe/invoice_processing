import logging, sys
import os
import json
from datetime import date
from decimal import Decimal

from invoice_processing.http_client import HTTPClient, APIError, RunRequestLimit, path_segment
from invoice_processing.invoice import Unreadable
from invoice_processing.extract import extract_invoice
from invoice_processing.rules import evaluate
from invoice_processing.normalize import format_amount, normalize_cui, normalize_invoice_number

# set up logging to log to stderr with level INFO
logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(message)s")

def decision_fields(invoice):
    if invoice is None:
        return {
            "supplier_cui": None,
            "invoice_number": None,
            "po_number": None,
            "currency": None,
            "net_amount": None,
            "gross_amount": None,
        }
    return {
        "supplier_cui": invoice.supplier_cui,
        "invoice_number": invoice.number,
        "po_number": invoice.po_number,
        "currency": invoice.currency,
        "net_amount": format_amount(invoice.net_total),
        "gross_amount": format_amount(invoice.gross_total),
    }

def decision_body(document_id, outcome, codes, invoice):
    return {
        "document_id": document_id,
        "outcome": outcome,
        "reason_codes": list(codes),
        **decision_fields(invoice),
    }

def report_item(document_id, status, outcome, codes):
    return {
        "document_id": document_id,
        "status": status,
        "outcome": outcome,
        "reason_codes": list(codes),
    }

def history_decision(entries):
    """
    returns the last decision for a document from the history entries.

    the hub normally returns at most one decision per document, but selecting the last one is a safe fallback in case of duplicates or future changes.
    """
    if not entries:
        return None

    return entries[-1]

def history_has_duplicate(entries, current_document_id, inbox):
    """
    returns True if the history entries contain a duplicate of the current document. 

    """
    positions = {
        item["document_id"]: index for index, item in enumerate(inbox)
    } # map document IDs to their positions in the inbox
    current_position = positions[current_document_id] # get the position of the current document in the inbox

    for entry in entries:
        other_id = entry.get("document_id") # get the document ID of the history entry

        if other_id == current_document_id:
            continue
        source = entry.get("source") # get the source of the history entry (e.g., "EARLIER_RUN" or "THIS_RUN")
        outcome = entry.get("outcome") # get the outcome of the history entry (e.g., "APPROVED", "REJECTED", etc.)

        if source == "EARLIER_RUN" and outcome != "REJECTED":
            return True

        if (source == "THIS_RUN" and outcome == "APPROVED" and positions.get(other_id, -1) < current_position):
            return True

    return False

def get_supplier(client, cui):
    return client.get_optional(f"/suppliers/{path_segment(normalize_cui(cui))}")

def get_po(client, po_number):
    return client.get_optional(f"/purchase-orders/{path_segment(po_number)}")

def get_document_history(client, document_id):
    return client.get("/history", params={"document_id": document_id})

def get_duplicate_history(client, invoice):
    return client.get("/history", params={"supplier_cui": normalize_cui(invoice.supplier_cui), "invoice_number": normalize_invoice_number(invoice.number)})

def notification_recipient(config, role, supplier):
    """
    Returns the email address of the recipient for a given role.
    If the role is "SUPPLIER", it returns the supplier's email address.
    If the role is "AP_REVIEWER" or "AP_TEAM", it returns the email address from the config.
    If the role is not recognized, it returns None.
    """
    if role == "SUPPLIER":
        if supplier is None:
            return None

        return supplier["email"]

    return config["recipients"][role]

def send_missing_notifications(client, config, document_id, outcome, codes, supplier, existing_notifications):
    """
    Sends notifications for a given document based on its outcome and reason codes.
    """
    already_sent = {
        notification["recipient_role"] for notification in existing_notifications
    }
    matrix = config["notification_matrix"].get(outcome, [])

    for row in matrix:
        role = row["recipient_role"]
    
        if role in already_sent:
            continue
     
        recipient = notification_recipient(config, role, supplier)
     
        if recipient is None:
            logging.info("%s notification skipped for %s: supplier unavailable", role, document_id)
            continue
     
        body = {
            "document_id": document_id,
            "recipient_role": role,
            "recipient": recipient,
            "template": row["template"],
            "reason_codes": list(codes),
        }
        key = f"{document_id}:notification:{role}"

        client.post("/notifications", body, key)
        logging.info("%s notification sent for %s to %s", role, document_id, recipient)

def submit_decision(client, document_id, outcome, codes, invoice):
    """
    submits a decision for a given document to the API. Returns True if the decision was recorded successfully, False if it failed permanently after retries.
    """
    body = decision_body(document_id, outcome, codes, invoice)
    key = f"{document_id}:decision"
    try:
        client.post("/decisions", body, key)
        logging.info("%s: decision=%s, reason_codes=%s", document_id, outcome, ",".join(codes) or "-")
        return True
    except APIError as err:
        if err.exhausted:
            logging.error("%s: decision failed permanently after retries: %s", document_id, err)
            return False
        raise

def process_already_decided(client, config, item, decision, report_items):
    """
    Processes a document that has already been decided in a previous run. It checks if the decision was made in an earlier run or the current run and handles notifications accordingly.
    """
    document_id = item["document_id"]
    outcome = decision["outcome"]
    codes = tuple(decision.get("reason_codes", []))

    if decision.get("source") == "EARLIER_RUN":
        report_items.append(report_item(document_id, "ALREADY_PROCESSED", outcome, codes))
        logging.info("%s: already processed, decision=%s, reason_codes=%s", document_id, outcome, ",".join(codes) or "-")
        return
    
    if decision.get("source") != "THIS_RUN":
        raise RuntimeError(f"Unknown decision source for {document_id}: "
        f"{decision.get('source')!r}"
        )

    notifications = client.get("/notifications", params={"document_id": document_id})

    supplier = None
    if any(row["recipient_role"] == "SUPPLIER" for row in config["notification_matrix"].get(outcome, [])):
        supplier_cui = decision.get("supplier_cui")
    
        if supplier_cui is not None:
            supplier = get_supplier(client, supplier_cui)
    
    send_missing_notifications(client, config, document_id, outcome, codes, supplier, notifications)
    report_items.append(report_item(document_id, "RECORDED", outcome, codes))
    logging.info("%s: replayed decision=%s codes=%s", document_id, outcome, ",".join(codes) or "-")

def process_normal_document(client, config, context, item, inbox, all_history, report_items):
    """
    processes a document that has not been decided yet. It extracts the invoice, checks for duplicates, evaluates the invoice, submits the decision, and sends notifications as needed.
    """
    document_id = item["document_id"]
    try:
        data = client.get_bytes(f"/documents/{path_segment(document_id)}")
        invoice = extract_invoice(data, item["content_type"])
    except Unreadable as err:
        logging.warning("%s: extraction failed: %s", document_id, err)
        outcome = "MANUAL_REVIEW"
        codes = ("EXTRACTION_FAILED",)
        recorded = submit_decision(client, document_id, outcome, codes, None)

        if recorded:
            supplier = None
            notifications = []
            send_missing_notifications(client, config, document_id, outcome, codes, supplier, notifications)
            status = "RECORDED"
        else:
            status = "FAILED"
        report_items.append(report_item(document_id, status, outcome, codes))
        return

    duplicate_history = get_duplicate_history(client, invoice)

    if history_has_duplicate(duplicate_history, document_id, inbox):
        outcome = "DUPLICATE"
        codes = ("DUPLICATE_INVOICE",)
        recorded = submit_decision(client, document_id, outcome, codes, invoice)
    
        if recorded:
            supplier = get_supplier(client, invoice.supplier_cui)
            send_missing_notifications(client, config, document_id, outcome, codes, supplier, [])
            status = "RECORDED"
        else:
            status = "FAILED"
        report_items.append(report_item(document_id, status, outcome, codes))
        return
    
    supplier = get_supplier(client, invoice.supplier_cui)
    po = None
    if invoice.po_number is not None:
        po = get_po(client, invoice.po_number)
    
    effective_balance = Decimal("0")

    if po is not None:
        effective_balance = effective_balance_for_restart(po=po, current_document_id=document_id, inbox=inbox, all_history=all_history)
    
    business_date = date.fromisoformat(context["business_date"]) 

    outcome, codes = evaluate(
        invoice=invoice,
        supplier=supplier,
        po=po,
        config=config,
        business_date=business_date,
        effective_balance=effective_balance
    )

    recorded = submit_decision(client, document_id, outcome, codes, invoice)
    
    if recorded:
        notifications = []
        send_missing_notifications(client, config, document_id, outcome, codes, supplier, notifications)
        status = "RECORDED"
    else:
        status = "FAILED"
    
    report_items.append(report_item(document_id, status, outcome, codes))


def submit_report(client, report_items):
    body = {"items": report_items}
    report_number = 1
    
    while True:
        key = "report" if report_number == 1 else f"report-{report_number}"
        try:
            client.post("/report", body, key)
            logging.info("Report submitted successfully with key: %s", key)
            return
        except APIError as err:
            if(err.status == 422 and err.code == "IDEMPOTENCY_KEY_REUSED"):
                report_number += 1
                logging.warning("Report key %s was already used; trying %s", key, ("report" if report_number == 1 else f"report-{report_number}"))
                continue
            raise

def effective_balance_for_restart(po, current_document_id, inbox, all_history):
    """
    re-add the net amounts of later THIS_RUN approved documents using the same PO
    it's needed when the current document had FAILED previously and later documents were approved, so the effective balance is lower than it was when the current document was first processed.
    """
    if po is None:
        return Decimal("0")

    balance = Decimal(str(po["remaining_balance"]))
    positions = {
        item["document_id"]: index for index, item in enumerate(inbox)
    }
    current_position = positions[current_document_id]
    
    for decision in all_history:
        if decision.get("source") != "THIS_RUN":
            continue
        if decision.get("outcome") != "APPROVED":
            continue
        document_id = decision.get("document_id")
        later_position = positions.get(document_id)
        if later_position is None:
            continue
        if later_position <= current_position:
            continue
        if decision.get("po_number") != po["po_number"]:
            continue
        net_amount = decision.get("net_amount")
        if net_amount is None:
            continue
        balance += Decimal(str(net_amount))
    return balance


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
        all_history = client.get("/history")
        report_items = []

        for item in inbox:
            document_id = item["document_id"]
            document_history = get_document_history(client, document_id)
            decision = history_decision(document_history)
            if decision is not None:
                process_already_decided(client, config, item, decision, report_items)
                continue
            process_normal_document(client, config, context, item, inbox, all_history, report_items)
        
        submit_report(client, report_items)
        return 0
    except RunRequestLimit as err:
        logging.error("Request limit reached: %s", err)
        return 1
    except APIError as err:
        logging.error("API error: %s", err)
        return 1
    except Exception:
        logging.exception("Unexpected error occurred")
        return 1


if __name__ == "__main__":
    sys.exit(main())
    

