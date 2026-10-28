"""
This is a module for normalizing invoice data. 
Its responsibility is to take raw invoice data that needs to be normalized (CUI, invoice_number, IBAN - described in the documentation) and transform it into standardized formats, described in the documentation.
"""

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