from django import template

register = template.Library()

_DAYS_CS = ["Pondělí", "Úterý", "Středa", "Čtvrtek", "Pátek", "Sobota", "Neděle"]


@register.filter
def weather_icon(code):
    """WMO weather code → representative emoji."""
    if code is None:
        return "❓"
    code = int(code)
    if code == 0:
        return "☀️"
    if code <= 3:
        return "🌤"
    if code <= 48:
        return "🌫"
    if code <= 67:
        return "🌧"
    if code <= 77:
        return "❄️"
    if code <= 82:
        return "🌦"
    return "⛈"


@register.filter
def day_cs(d):
    """date → Czech weekday name."""
    return _DAYS_CS[d.weekday()]


@register.filter
def temp_round(value):
    """Float temperature → rounded integer string, or dash."""
    if value is None:
        return "—"
    return str(round(value))


@register.filter
def abs_val(value):
    """Absolute value for template use."""
    if value is None:
        return None
    return abs(value)
