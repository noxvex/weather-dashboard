from datetime import date


def parse_dm(s):
    """
    Parse a Czech day.month string ("12.7", "15.11.") into a non-leap
    day-of-year int, or None when the input is empty/invalid. Shared by the
    Historie view (GET params) and HistoriePinForm (stored pin params) so
    both sides accept exactly the same format.
    """
    try:
        d, m = s.strip().rstrip(".").split(".")[:2]
        return date(2001, int(m), int(d)).timetuple().tm_yday  # non-leap day-of-year
    except (ValueError, AttributeError):
        return None
