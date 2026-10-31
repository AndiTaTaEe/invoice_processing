import json
import sys

FIELDS = ("extracted", "outcome", "reason_codes")

def main(report_path, out_path):
    """
    extracts the expected values for each document from a report and writes them to a JSON file.
    """
    with open(report_path, encoding="utf-8") as f:
        report = json.load(f)

    expected = {}
    for scenario in report["scenarios"]:
        for check in scenario["checks"]:
            doc_id, sep, _ = (check.get("message") or "").partition(":") # get the document id from the message 
            if not sep:
                continue # skip checks without a doc_id
            field = check["name"].rsplit(".", 1)[-1] # get the field name from the check name
            if field in FIELDS:
                expected.setdefault(doc_id.strip(), {})[field] = check["expected"] # store the expected value for the field in the expected dict

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(expected, f, indent=2, ensure_ascii=False)

    unreadable = [doc_id for doc_id, values in expected.items() if values.get("reason_codes") == ["EXTRACTION_FAILED"]] # get the list of doc_ids that have reason_codes indicating extraction failed
    print(f"{len(expected)} documents expected; {len(unreadable)} documents expected to be unreadable: {unreadable}")

if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])