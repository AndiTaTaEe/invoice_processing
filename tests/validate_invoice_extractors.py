from pathlib import Path
import json
import sys
import traceback

from invoice_processing.invoice import Unreadable
from invoice_processing.pdf_extractor import extract_pdf
from invoice_processing.xml_extractor import extract_xml
from invoice_processing.normalize import format_amount
from invoice_processing.extract import extract_invoice

FIXTURES = Path("tests/fixtures")

def load_content_types(folder: Path) -> dict:
    """
    Load the content types from the inbox.json file in the given folder.
    Returns a dictionary mapping document_id to content_type.
    If the inbox.json file does not exist, returns an empty dictionary.
    """
    inbox_path = folder / "inbox.json"
    if not inbox_path.exists():
        return {}
    inbox = json.loads(inbox_path.read_text(encoding="utf-8"))
    return {item["document_id"]: item["content_type"] for item in inbox}


def invoice_values(invoice):
    return {
        "supplier_cui": invoice.supplier_cui,
        "invoice_number": invoice.number,
        "po_number": invoice.po_number,
        "currency": invoice.currency,
        "net_amount": format_amount(invoice.net_total),
        "gross_amount": format_amount(invoice.gross_total),
    }


def find_document(folder: Path, document_id: str) -> Path:
    for suffix in (".pdf", ".xml"):
        candidate = folder / f"{document_id}{suffix}"
        if candidate.exists():
            return candidate

    return None

def describe(invoice, actual):
    return (
        f"CUI={actual['supplier_cui']}, number={actual['invoice_number']}, "
        f"PO={actual['po_number']}, currency={actual['currency']}, "
        f"net={actual['net_amount']}, gross={actual['gross_amount']}, "
        f"vat={format_amount(invoice.vat_total)}, "
        f"issue={invoice.issue_date}, due={invoice.due_date}, "
        f"delivery={invoice.delivery_date}, lines={len(invoice.lines)}"
    )



def validate_scenario(folder: Path, *, verbose=False):
    expected_path = folder / "expected.json"

    if not expected_path.exists():
        print(f"SKIP {folder}: expected.json not found")
        return 0, 0

    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    content_types = load_content_types(folder)

    passed = 0
    failed = 0

    for document_id, expected_result in sorted(expected.items()):
        label = f"{folder.name}/{document_id}"
        path = find_document(folder, document_id)
        if path is None:
            print(f"FAIL {label}: document file not found")
            failed += 1
            continue

        should_be_unreadable = expected_result.get("reason_codes") == ["EXTRACTION_FAILED"]
        expected_extracted = expected_result.get("extracted", {})

        try:
            invoice = extract_invoice(path.read_bytes(), content_types.get(document_id, ""))
        except Unreadable as error:
            if should_be_unreadable:
                print(f"PASS {label}: Unreadable ({error})")
                passed += 1
            else:
                print(
                    f"FAIL {label}: "
                    f"unexpected Unreadable: {error}"
                )
                failed += 1

            continue

        except Exception:
            print(
                f"FAIL {label}: "
                f"unexpected error: {traceback.format_exc()}"
            )
            failed += 1
            continue

        if should_be_unreadable:
            print(
                f"FAIL {label}: "
                "expected Unreadable, but extraction succeeded"
            )
            failed += 1
            continue

        actual = invoice_values(invoice)

        mismatches = {
            field: (expected_value, actual.get(field))
            for field, expected_value in expected_extracted.items()
            if actual.get(field) != expected_value
        }

        if mismatches:
            print(f"FAIL {label}")
            for field, (expected, actual) in mismatches.items():
                print(
                    f"  {field}: expected={expected!r}, "
                    f"actual={actual!r}"
                )
            failed += 1
            continue
        passed += 1
        if verbose:
            print(f"PASS {label}: {describe(invoice, actual)}")
        
    for path in sorted(folder.iterdir()):
        if path.suffix in (".pdf", ".xml") and path.stem not in expected:
            print(f"INFO {folder.name}/{path.name}: no expected.json entry for this document")

    print(f"{folder.name}: {passed} passed, {failed} failed")
    return passed, failed

def main():
    verbose = "--verbose" in sys.argv[1:]
    args = [arg for arg in sys.argv[1:] if not arg.startswith("--")]
    folders = [Path(arg) for arg in args] or sorted(p for p in FIXTURES.iterdir() if p.is_dir() and (p / "expected.json").exists())

    total_passed = 0
    total_failed = 0
    for folder in folders:
        passed, failed = validate_scenario(folder, verbose=verbose)
        total_passed += passed
        total_failed += failed

    print(f"\nTOTAL: {total_passed} passed, {total_failed} failed")
    if total_failed:
        raise SystemExit(1)

if __name__ == "__main__":
    main()