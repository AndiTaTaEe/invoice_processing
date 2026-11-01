import sys

from pypdf import PdfReader

def main():
    """
    Inspect a PDF file and print its text content to the console.
    If the --layout option is provided, the text will be extracted in layout mode, preserving the original layout of the PDF. Otherwise, the text will be extracted in normal mode.

    usage (from root):
    python -m tools.inspect_pdf [--layout] <pdf_file1> <pdf_file2> ...
    """
    layout = "--layout" in sys.argv[1:] # check if the --layout option is present in the command line arguments
    paths = [arg for arg in sys.argv[1:] if arg != "--layout"] # get the paths of the PDF files to inspect, ignoring the --layout option
    for path in paths:
        reader = PdfReader(path)
        print(f"--- {path} ({len(reader.pages)} pages) ---")
        for number, page in enumerate(reader.pages, start=1):
            if layout:
                text = page.extract_text(extraction_mode="layout")
            else:
                text = page.extract_text()
            print(f"--- page {number} ---")
            for i, row in enumerate(text.splitlines()):
                print(f"{i}: {row!r}") # !r - print the row as a repr() to make whitespace visible

if __name__ == "__main__":
    main()
