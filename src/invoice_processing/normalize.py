"""
This is a module for normalizing invoice data. 
Its responsibility is to take raw invoice data that needs to be normalized (CUI, invoice_number, IBAN - described in the documentation) and transform it into standardized formats, described in the documentation.
"""

from itertools import groupby

# define sets of characters that are considered valid for normalization (used in the normalize_invoice_number function)
_ALPHANUMERIC_CHARS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
_DIGIT_CHARS = set("0123456789")

def normalize_cui(cui: str) -> str:
    """
    Normalize the CUI of a company.
    The CUI is expected to be a string of digits, possibly with a prefix of the country code (e.g.: "RO"), and possibly with whitespaces in between. The normalized CUI should be a string of digits without any prefix or whitespaces.
    
    Args:
        cui (str): The raw CUI string to be normalized.
    """
    
    # remove whitespaces that may be present within the CUI string 
    # after that, make it uppercase to ensure consistency, as CUI may contain letters in some cases (e.g., "RO12345678")
    cui = cui.replace(" ", "").upper()

    # the normalized CUI shouldn't have the prefix of the country code (e.g.: "RO"), so we remove it if it's present
    # the prefix is usually the first two letters of the CUI, so we check if the first two characters are letters and remove them
    if cui.startswith("RO"):
        cui = cui[2:]  # remove the "RO" prefix
    
    return cui

def normalize_invoice_number(invoice_number: str) -> str:
    """
    normalize the invoice number.
    the invoice number is expected to be a string that may contain alphanumeric characters and other symbols. The normalized invoice number should be a string of alphanumeric characters only, with leading zeros removed from digit groups.

    Args:
        invoice_number (str): The raw invoice number string to be normalized.
    """
    # make the invoice number uppercase, then keep only the alphanumeric characters
    chars = [c for c in invoice_number.upper() if c in _ALPHANUMERIC_CHARS] # e.g.: "A-007-B" -> ["A", "0", "0", "7", "B"]

    # group the characters by whether they are digits or not, and join the groups together
    pieces = []
    for is_digit, group in groupby(chars, key=lambda c: c in _DIGIT_CHARS):
        text = "".join(group) # e.g.: ["0", "0", "7"] -> "007"
        if is_digit:
            text = text.lstrip("0") or "0" # remove leading zeros from digit groups, but keep at least one zero if the group is all zeros, e.g.: "007" -> "7", "000" -> "0"
        pieces.append(text) # pieces will contain the normalized groups of characters, e.g.: ["A", "007", "B"] -> ["A", "7", "B"]

    # join the pieces together to form the normalized invoice number
    return "".join(pieces)
