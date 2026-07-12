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


@register.filter
def get_item(d, key):
    """Dict lookup in templates: {{ dict|get_item:key }}"""
    return d.get(key)


# Fixed reference scale for the .fc-fill temperature bar — covers the realistic
# CZ/SK range so a given temp always lands in the same visual spot across rows.
_FC_SCALE_MIN = -10
_FC_SCALE_MAX = 40
_FC_SCALE_SPAN = _FC_SCALE_MAX - _FC_SCALE_MIN


@register.simple_tag
def fc_fill_style(t_min, t_max):
    """
    left/width % for the .fc-fill bar, positioning [t_min, t_max] against the
    fixed -10..40°C scale so the bar's position reflects actual temperature,
    not a hardcoded placeholder.
    """
    if t_min is None or t_max is None:
        return "left:0%; width:0%;"
    lo = max(0.0, min(1.0, (t_min - _FC_SCALE_MIN) / _FC_SCALE_SPAN))
    hi = max(0.0, min(1.0, (t_max - _FC_SCALE_MIN) / _FC_SCALE_SPAN))
    if hi < lo:
        lo, hi = hi, lo
    left_pct = lo * 100
    width_pct = max(hi - lo, 0.03) * 100  # keep a sliver visible even for 0-range
    return f"left:{left_pct:.1f}%; width:{width_pct:.1f}%;"
