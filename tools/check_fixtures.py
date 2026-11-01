import json
import pathlib
import sys

from invoice_processing.pdf_extractor import extract_pdf
from invoice_processing.xml_extractor import extract_xml
from invoice_processing.invoice import Unreadable
from invoice_processing.normalize import format_amount

EXTRACTORS = {".xml": extract_xml, ".pdf": extract_pdf}

def main(folder):
    folder = pathlib.Path(folder)
    expected = json.loads((folder / "expected.json").read_text(encoding="utf-8"))
    ok = 0
    bad = 0
    for path in sorted(folder.iterdir()):
        extractor = EXTRACTORS.get(path.suffix.lower())
        exp = expected.get(path.stem)
        if extractor is None or exp is None:
            continue
        should_fail = exp.get("reason_codes") == ["EXTRACTION_FAILED"] # if the expected reason codes indicate that the extraction should fail (unreadable document), set should_fail to True
        try:
            invoice = extractor(path.read_bytes())
        except Unreadable as err:
            if should_fail:
                ok += 1
            else:
                bad += 1
                print(f"{path.name}: unexpected Unreadable: {err}")
            continue
        if should_fail:
            bad += 1
            print(f"{path.name}: expected Unreadable, but extraction succeeded")
            continue
        # check the extracted invoice against the expected values
        got_values = {
            "supplier_cui": invoice.supplier_cui,
            "invoice_number": invoice.number,
            "po_number": invoice.po_number,
            "currency": invoice.currency,
            "net_amount": format_amount(invoice.net_total),
            "gross_amount": format_amount(invoice.gross_total),
        }
        diff = {}
        for key, expected_value in exp["extracted"].items():
            actual_value = got_values.get(key)
            if actual_value != expected_value:
                diff[key] = (actual_value, expected_value)
        if diff:
            bad += 1
            print(f"{path.name}: extracted values differ from expected: {diff}")
        else:
            ok += 1
    print(f"{ok} OK, {bad} BAD")

if __name__ == "__main__":
    main(sys.argv[1])

