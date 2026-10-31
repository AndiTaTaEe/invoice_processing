from datetime import date
from typing import Callable, Optional

from .invoice import Unreadable
from .normalize import InvalidNumber, parse_decimal, parse_percent

_DIGITS = set("0123456789")

# format: (separator, (lengths), (order))
DATE_FORMATS = {
    "iso": ("-", (4, 2, 2), (0, 1, 2)),  # YYYY-MM-DD, useful for XML + PDF english
    "dmy": (".", (2, 2, 4), (2, 1, 0)),  # DD.MM.YYYY, useful for PDF ro and bilingual

def required_text(raw: Optional[str], name: str) -> str:
    """
    validate that a required text field is present and not empty.
    """
    if raw is None:
        raise Unreadable(f"Missing required field: {name}")
    text = raw.strip()
    if not text:
        raise Unreadable(f"Empty required field: {name}")
    return text

def optional_text(raw: Optional[str]) -> Optional[str]:
    """
    validate that an optional text field is either None or not empty.
    """
    if raw is None:
        return None
    return raw.strip() or None

def _parse_date(text: str, format:str, name: str) -> date:
    """
    parse a date string into a date object, using the specified format. If the string is not a valid date, raise an Unreadable exception.
    """
    sep, lengths, (i_year, i_month, i_day) = DATE_FORMATS[format]
    pieces = text.split(sep)
    # three pieces must be present, and they must have the expected lengths and 0-9 digits
    if len(pieces) != 3 or any(len(piece) != length or not set(piece).issubset(_DIGITS) for piece, length in zip(pieces, lengths)):
        raise Unreadable(f"{name}: invalid date format: {text} (expected {format})")
    # convert the pieces to integers and create a date object, raising an Unreadable exception if the date is invalid
    try:
        return date(int(pieces[i_year]), int(pieces[i_month]), int(pieces[i_day]))
    except ValueError:
        raise Unreadable(f"{name}: unexistent date: {text}") from None

def required_date(raw: Optional[str], format: str, name: str) -> date:
    return _parse_date(required_text(raw, name), format, name)

def optional_date(raw: Optiona[l[str], format: str, name: str) -> Optional[date]:
    text = optional_text(raw)
    return None if text is None else _parse_date(text, format, name)

def required_decimal(raw, name, decimal_sep=".", thousands_sep=""):
    text = required_text(raw, name)
    try:
        return parse_decimal(text, decimal_sep, thousands_sep)
    except InvalidNumber as err:
        raise Unreadable(f"{name}: invalid number: {text}") from err

def required_percent(raw, name, decimal_sep=".", thousands_sep=""):
    text = required_text(raw, name)
    try:
        return parse_percent(text, decimal_sep, thousands_sep)
    except InvalidNumber as err:
        raise Unreadable(f"{name}: invalid percent: {text}") from err

def required_normalized(raw: Optional[str], name: str, normalizer: Callable[[str], str]) -> str:
    """
    validate that a required text field is present and not empty, and normalize it using the specified normalizer function. If the normalized value is empty, raise an Unreadable exception.
    """ 
    text = required_text(raw, name)
    value = normalizer(text)
    if not value:
        raise Unreadable(f"{name}: empty normalized value: {text}")
    return value