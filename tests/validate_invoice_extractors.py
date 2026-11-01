from pathlib import Path
import json
import sys

from invoice_processing.invoice import Unreadable
from invoice_processing.pdf_extractor import extract_pdf
from invoice_processing.xml_extractor import extract_xml
from invoice_processing.normalize import format_amount


FIXTURES = Path("tests/fixtures")


def detect_type(data: bytes) -> str:
    if data.startswith(b"%PDF"):
        return "pdf"

    if data.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"<"):
        return "xml"

    return "unknown"


def extract_document(path: Path):
    data = path.read_bytes()
    document_type = detect_type(data)

    if document_type == "pdf":
        return extract_pdf(data)

    if document_type == "xml":
        return extract_xml(data)

    raise Unreadable(f"Unknown document type: {path}")


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

    raise FileNotFoundError(
        f"No PDF/XML file found for {document_id} in {folder}"
    )


def validate_scenario(folder: Path, *, display_values=False):
    expected_path = folder / "expected.json"

    if not expected_path.exists():
        print(f"SKIP {folder}: expected.json not found")
        return

    expected = json.loads(expected_path.read_text(encoding="utf-8"))

    passed = 0
    failed = 0

    for document_id, expected_result in sorted(expected.items()):
        document_path = find_document(folder, document_id)
        expected_reason_codes = expected_result.get("reason_codes", [])
        expected_extracted = expected_result.get("extracted", {})

        should_be_unreadable = (
            expected_reason_codes == ["EXTRACTION_FAILED"]
        )

        try:
            invoice = extract_document(document_path)

        except Unreadable as error:
            if should_be_unreadable:
                print(f"PASS {folder.name}/{document_path.name}: Unreadable")
                passed += 1
            else:
                print(
                    f"FAIL {folder.name}/{document_path.name}: "
                    f"unexpected Unreadable: {error}"
                )
                failed += 1

            continue

        except Exception as error:
            print(
                f"FAIL {folder.name}/{document_path.name}: "
                f"unexpected error: {type(error).__name__}: {error}"
            )
            failed += 1
            continue

        if should_be_unreadable:
            print(
                f"FAIL {folder.name}/{document_path.name}: "
                "expected Unreadable, but extraction succeeded"
            )
            failed += 1
            continue

        actual = invoice_values(invoice)

        mismatches = {
            field: {
                "expected": expected_value,
                "actual": actual.get(field),
            }
            for field, expected_value in expected_extracted.items()
            if actual.get(field) != expected_value
        }

        if mismatches:
            print(f"FAIL {folder.name}/{document_path.name}")
            for field, values in mismatches.items():
                print(
                    f"  {field}: expected={values['expected']!r}, "
                    f"actual={values['actual']!r}"
                )
            failed += 1
            continue

        if display_values:
            print(
                f"PASS {folder.name}/{document_path.name}: "
                f"CUI={actual['supplier_cui']}, "
                f"number={actual['invoice_number']}, "
                f"PO={actual['po_number']}, "
                f"currency={actual['currency']}, "
                f"net={actual['net_amount']}, "
                f"gross={actual['gross_amount']}"
            )
        else:
            print(f"PASS {folder.name}/{document_path.name}")

        passed += 1

    print(
        f"{folder.name}: {passed} passed, {failed} failed"
    )

    if failed:
        raise AssertionError(f"Validation failed for {folder.name}")


def validate_clean_a_p02():
    """
    Explicitly verifies the clean document from A-P02.

    If the report uses DOC-... as its key, this finds the first non-failing
    document automatically. If the key is literally inv3, it also works.
    """
    folder = FIXTURES / "A-P02"
    expected = json.loads(
        (folder / "expected.json").read_text(encoding="utf-8")
    )

    clean_documents = [
        document_id
        for document_id, result in expected.items()
        if result.get("reason_codes", []) != ["EXTRACTION_FAILED"]
    ]

    if not clean_documents:
        raise AssertionError("A-P02 has no document expected to be readable")

    for document_id in clean_documents:
        path = find_document(folder, document_id)

        try:
            invoice = extract_document(path)
        except Unreadable as error:
            raise AssertionError(
                f"A-P02 clean document {document_id} raised Unreadable: {error}"
            ) from error

        print(
            f"A-P02 clean document PASS: {document_id}, "
            f"invoice={invoice.number}"
        )


def main():
    scenarios = [
        "A-P01",
        "A-P02",
        "sandbox-1seed",
        "sandbox-2seed",
        "sandbox-7seed",
    ]

    # A-P01: print all extracted fields required by the hub.
    validate_scenario(FIXTURES / "A-P01", display_values=True)

    # A-P02: unreadable documents must fail, clean document must succeed.
    validate_scenario(FIXTURES / "A-P02")
    validate_clean_a_p02()

    # Sandbox runs: validate every available layout fixture.
    for scenario in scenarios[2:]:
        folder = FIXTURES / scenario
        if folder.exists():
            validate_scenario(folder)


if __name__ == "__main__":
    try:
        main()
    except (AssertionError, FileNotFoundError) as error:
        print(f"\nVALIDATION FAILED: {error}", file=sys.stderr)
        raise SystemExit(1)