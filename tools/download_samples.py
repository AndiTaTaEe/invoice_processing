import json
import logging
import os
import sys
from invoice_processing.http_client import HTTPClient, APIError, RunRequestLimit, path_segment

logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(message)s")

def determine_file_type(data: bytes) -> str:
    """
    Determine the file type of the given data based on its content.
    Returns "PDF" if the data starts with the PDF magic number, "XML" if it starts with an XML declaration (after stripping any BOM and whitespace), and "UNKNOWN" otherwise.
    """
    if data.startswith(b"%PDF"):
        return "PDF" 
    # check for XML files that may have a byte order mark at the start, and may have whitespace before the XML declaration
    if data.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"<"):
        return "XML" 

    return "UNKNOWN"

def main():
    base_url = sys.argv[1]
    label = sys.argv[2] if len(sys.argv) > 2 else "samples"
    out_dir = os.path.join("tests", "fixtures", label) # create a directory under tests/fixtures with the given label
    os.makedirs(out_dir, exist_ok=True)
    client = HTTPClient(base_url)

    try:
        inbox = client.get("/inbox")
        with open(os.path.join(out_dir, "inbox.json"), "w", encoding="utf-8") as f:
            json.dump(inbox, f, indent=2, ensure_ascii=False) # write the inbox response to a JSON file in the output directory
        for item in inbox:
            data = client.get_bytes(f"/documents/{path_segment(item['document_id'])}") 
            name = os.path.basename(item["filename"]) # get the base name of the file from the filename field
            with open(os.path.join(out_dir, name), "wb") as f:
                f.write(data) # write the document data to a file in the output directory
            logging.info(f"%s declared=%s, detected=%s %d bytes", name, item["content_type"], determine_file_type(data), len(data)) # log the filename, declared content type, detected file type, and size in bytes
    except (RunRequestLimit, APIError) as err:
        logging.error("Error: %s", err)
        sys.exit(1)

if __name__ == "__main__":
    main()