from collections import defaultdict
from datetime import date, timedelta
import urllib.parse

from django.utils import timezone

from django.db.models import Avg, Count, ExpressionWrapper, F, FloatField, Func, IntegerField, Sum
from django.db.models.functions import ExtractIsoYear, ExtractWeek, ExtractYear

from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.management import call_command
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render

from ingest.models import DailyForecast, HistoricalActual, MediumLongRangeForecast, WeatherPoint
from .forms import HistoriePinForm, NoteForm, PinEditForm
from .models import HistoriePin, Note
from .utils import parse_dm

User = get_user_model()


# ── Permission helpers ──────────────────────────────────────────────────────

def _can_modify(user, note):
    return note.author == user or user.role in ("leader", "admin")


def _can_pin(user):
    return user.role in ("leader", "admin")


# ── Weather sidebar ─────────────────────────────────────────────────────────

def _avg(rows, field):
    vals = [getattr(r, field) for r in rows if getattr(r, field) is not None]
    return round(sum(vals) / len(vals), 1) if vals else None


def _temp_pct(delta, prev):
    """% change relative to |prev|; None when base too small to be meaningful."""
    if prev is None or delta is None or abs(prev) < 3:
        return None
    return round(delta / abs(prev) * 100)


# ── Bod selection (country → macro region → city) ───────────────────────────

_REGION_LABELS = dict(WeatherPoint.MACRO_REGION_CHOICES)


def _parse_bod(sel, points=None):
    """
    Parse a ?bod= value shared by the hierarchical selectors (Historie,
    Revize, Aktuality) into (scope, label, short_label). scope holds exactly
    one of country/macro_region/point_id; label is the full Czech UI name,
    short_label the compact one for table chips and chart legends. Unknown
    values return (None, None, None) so each caller keeps its own default.
    Pass pre-fetched `points` to resolve city pks without a query.
    """
    if sel == "cz":
        return {"country": "CZ"}, "ČR (národní průměr)", "ČR"
    if sel == "sk":
        return {"country": "SK"}, "SR (národní průměr)", "SR"
    if sel in _REGION_LABELS:
        label = _REGION_LABELS[sel]
        return {"macro_region": sel}, f"{label} (regionální průměr)", label
    if sel and sel.isdigit():
        pk = int(sel)
        if points is not None:
            point = next((p for p in points if p.pk == pk), None)
        else:
            point = WeatherPoint.objects.filter(pk=pk).first()
        if point is not None:
            return {"point_id": point.pk}, f"{point.name} ({point.country})", point.name
    return None, None, None


def _point_in_scope(point, scope):
    """Whether a WeatherPoint belongs to the parsed selection scope."""
    if "point_id" in scope:
        return point.pk == scope["point_id"]
    if "macro_region" in scope:
        return point.macro_region == scope["macro_region"]
    return point.country == scope["country"]


def _scope_daily_avg(rows, scope, date_attr, fields):
    """
    Plain arithmetic mean across all points in scope, per date:
    {date: {field: avg}}. Works for any numeric field the rows carry
    (temperature, precipitation, temp_mean, ...); rows need `point`
    pre-joined so scoping costs no extra queries.
    """
    by_date = defaultdict(list)
    for r in rows:
        if _point_in_scope(r.point, scope):
            by_date[getattr(r, date_attr)].append(r)
    return {
        d: {f: _avg(day_rows, f) for f in fields}
        for d, day_rows in sorted(by_date.items())
    }


def _selector_context(points):
    """Template context bits every page with the hierarchical selector needs."""
    return {
        "points_cz": [p for p in points if p.country == "CZ"],
        "points_sk": [p for p in points if p.country == "SK"],
        "regions_cz": [(slug, label) for slug, label in WeatherPoint.MACRO_REGION_CHOICES
                       if WeatherPoint.MACRO_REGION_COUNTRY[slug] == "CZ"],
        "regions_sk": [(slug, label) for slug, label in WeatherPoint.MACRO_REGION_CHOICES
                       if WeatherPoint.MACRO_REGION_COUNTRY[slug] == "SK"],
    }


def _get_latest_short_rows(batches=1):
    """
    Returns (rows, batch_dates) for the most recent `batches` short-range
    snapshots, with point pre-joined. Two queries total regardless of batches
    count — one to find distinct dates, one to fetch all matching rows.

    batch_dates is a list of date objects newest-first (length ≤ batches).
    When batches=2, rows from both snapshots are mixed; callers that only need
    the latest should filter by issued_at.date() == batch_dates[0].
    """
    batch_dates = list(
        DailyForecast.objects
        .filter(horizon=DailyForecast.HORIZON_SHORT, issued_at__isnull=False)
        .order_by("-issued_at__date")
        .values_list("issued_at__date", flat=True)
        .distinct()[:batches]
    )
    if not batch_dates:
        return [], []
    rows = list(
        DailyForecast.objects
        .filter(horizon=DailyForecast.HORIZON_SHORT, issued_at__date__in=batch_dates)
        .select_related("point")
        .order_by("point__name", "forecast_date")
    )
    return rows, batch_dates


def _get_weather_panel(batch_rows):
    """Sidebar data derived from pre-fetched batch rows — zero extra queries."""
    if not batch_rows:
        return None, None, None, [], []

    today = date.today()
    future_dates = sorted({r.forecast_date for r in batch_rows if r.forecast_date >= today})
    if not future_dates:
        return None, None, None, [], []
    nearest_date = future_dates[0]

    day_rows = [r for r in batch_rows if r.forecast_date == nearest_date]
    cz = [r for r in day_rows if r.point.country == "CZ"]
    sk = [r for r in day_rows if r.point.country == "SK"]

    cz_avg = {"temperature_max": _avg(cz, "temperature_max"), "temperature_min": _avg(cz, "temperature_min"), "precipitation_sum": _avg(cz, "precipitation_sum")}
    sk_avg = {"temperature_max": _avg(sk, "temperature_max"), "temperature_min": _avg(sk, "temperature_min"), "precipitation_sum": _avg(sk, "precipitation_sum")}

    return nearest_date, cz_avg, sk_avg, cz, sk


# ── Since-last-login change summary ────────────────────────────────────────

def _get_since_login(last_login):
    """
    Count how many new ingest batches have run since the user last logged in,
    and return a simple delta for today's national CZ avg temp_max if available.
    """
    if not last_login:
        return None

    today = date.today()

    # Distinct batch dates issued after last login
    new_batches = list(
        DailyForecast.objects
        .filter(
            horizon=DailyForecast.HORIZON_SHORT,
            issued_at__gte=last_login,
            issued_at__isnull=False,
        )
        .order_by("issued_at__date")
        .values_list("issued_at__date", flat=True)
        .distinct()
    )
    if len(new_batches) < 2:
        return None  # need at least two snapshots to show a delta

    first_batch = new_batches[0]
    last_batch = new_batches[-1]

    def nat_avg_temp(batch_date, country):
        rows = list(
            DailyForecast.objects.filter(
                horizon=DailyForecast.HORIZON_SHORT,
                issued_at__date=batch_date,
                forecast_date=today,
                point__country=country,
            )
        )
        return _avg(rows, "temperature_max")

    cz_first = nat_avg_temp(first_batch, "CZ")
    cz_last = nat_avg_temp(last_batch, "CZ")
    sk_first = nat_avg_temp(first_batch, "SK")
    sk_last = nat_avg_temp(last_batch, "SK")

    if cz_first is None and sk_first is None:
        return None

    return {
        "batch_count": len(new_batches),
        "cz_delta": round(cz_last - cz_first, 1) if cz_first is not None and cz_last is not None else None,
        "sk_delta": round(sk_last - sk_first, 1) if sk_first is not None and sk_last is not None else None,
    }


# ── Chart data (16-day national averages for Plotly) ───────────────────────

def _get_chart_data(batch_rows):
    """16-day national avg series derived from pre-fetched batch rows — zero extra queries."""
    by_country_date = {"CZ": defaultdict(list), "SK": defaultdict(list)}
    for r in batch_rows:
        if r.point.country in by_country_date and r.temperature_max is not None:
            by_country_date[r.point.country][r.forecast_date.isoformat()].append(r.temperature_max)

    result = {}
    for country, by_date in by_country_date.items():
        series = [
            {"date": d, "temp": round(sum(vals) / len(vals), 1)}
            for d, vals in sorted(by_date.items())
        ]
        result[country.lower()] = series
    return result


_SEASONAL_CACHE_KEY = "aktuality_seasonal_chart"
_HISTORICAL_EXISTS_KEY = "aktuality_has_historical"
_CACHE_TTL = 3600  # 1 hour — seasonal data only changes once per day via cron


def _get_seasonal_chart_data():
    """
    Returns (mid, long) chart dicts of SEAS5 seasonal mean temp per target_date
    (national average) from the latest issued snapshot:
      mid  = střednědobá, target_date within 1–4 months from today
      long = dlouhodobá,  target_date beyond 4 months (to ~7 months)
    Each is {cz: [{date, temp}, ...], sk: [...]}; empty lists if no data yet.
    Result is cached for 1 hour (LocMemCache by default, no extra infra needed).
    """
    cached = cache.get(_SEASONAL_CACHE_KEY)
    if cached is not None:
        return cached

    empty = {"cz": [], "sk": []}
    latest = (
        MediumLongRangeForecast.objects
        .filter(horizon=MediumLongRangeForecast.HORIZON_SEAS5)
        .order_by("-issued_at")
        .values_list("issued_at", flat=True)
        .first()
    )
    if not latest:
        result = dict(empty), dict(empty)
        cache.set(_SEASONAL_CACHE_KEY, result, _CACHE_TTL)
        return result

    rows = list(
        MediumLongRangeForecast.objects
        .filter(horizon=MediumLongRangeForecast.HORIZON_SEAS5, issued_at=latest)
        .select_related("point")
        .order_by("target_date")
    )

    mid_cutoff = date.today() + timedelta(days=120)  # ~4 months
    buckets = {
        "mid": {"CZ": defaultdict(list), "SK": defaultdict(list)},
        "long": {"CZ": defaultdict(list), "SK": defaultdict(list)},
    }
    for r in rows:
        if r.point.country not in ("CZ", "SK") or r.temp_mean is None:
            continue
        key = "mid" if r.target_date <= mid_cutoff else "long"
        buckets[key][r.point.country][r.target_date.isoformat()].append(r.temp_mean)

    def to_chart(by_country):
        return {
            country.lower(): [
                {"date": d, "temp": round(sum(vals) / len(vals), 1)}
                for d, vals in sorted(by_date.items())
            ]
            for country, by_date in by_country.items()
        }

    result = to_chart(buckets["mid"]), to_chart(buckets["long"])
    cache.set(_SEASONAL_CACHE_KEY, result, _CACHE_TTL)
    return result


def _get_revision_summary(all_rows, batch_dates, limit=5):
    """
    Revision summary for the main page: the two most recent short-range
    snapshots compared (same target_date, different issued_at), largest
    national temp deltas first. limit=None returns the full list (feeds the
    scrollable sidebar column). Derived entirely from pre-fetched rows — zero
    extra queries. Returns None if batch_dates has fewer than 2 entries.
    """
    if len(batch_dates) < 2:
        return None

    today = date.today()
    latest, previous = batch_dates[0], batch_dates[1]

    # Split pre-fetched rows by batch date into nested dicts for fast lookup
    by_batch: dict[date, dict[str, dict]] = {latest: defaultdict(lambda: defaultdict(list)),
                                              previous: defaultdict(lambda: defaultdict(list))}
    for r in all_rows:
        if r.issued_at is None or r.forecast_date < today:
            continue
        bd = r.issued_at.date()
        if bd in by_batch:
            by_batch[bd][r.point.country][r.forecast_date].append(r)

    deltas = []
    for country, label in [("CZ", "ČR"), ("SK", "SR")]:
        latest_s = {fd: _avg(rows, "temperature_max") for fd, rows in by_batch[latest][country].items()}
        prev_s = {fd: _avg(rows, "temperature_max") for fd, rows in by_batch[previous][country].items()}
        for fd in latest_s:
            lt, pt = latest_s[fd], prev_s.get(fd)
            if lt is not None and pt is not None:
                d = round(lt - pt, 1)
                if abs(d) >= 0.5:
                    deltas.append({"country": label, "date": fd, "delta": d})

    deltas.sort(key=lambda x: abs(x["delta"]), reverse=True)
    return {
        "latest_batch": latest,
        "previous_batch": previous,
        "total": len(deltas),
        "top": deltas if limit is None else deltas[:limit],
    }


# ── Author filter chips ─────────────────────────────────────────────────────

FILTER_ALL = "vse"
FILTER_SYSTEM = "system"


def _apply_filter(qs, filter_param):
    if filter_param == FILTER_SYSTEM:
        return qs.exclude(note_type=Note.TYPE_HUMAN)
    if filter_param and filter_param != FILTER_ALL:
        # treat as username
        return qs.filter(author__username=filter_param)
    return qs  # default: all


def _build_filter_chips(notes_qs):
    """Return chip data: list of (label, value, active_class)."""
    authors = (
        User.objects.filter(notes__isnull=False)
        .distinct()
        .order_by("username")
        .values_list("username", flat=True)
    )
    chips = [(FILTER_ALL, "Vše"), (FILTER_SYSTEM, "Systém")]
    for username in authors:
        chips.append((username, username))
    return chips


# ── Feed filter helpers ──────────────────────────────────────────────────────

def _apply_time_filter(qs, rozsah):
    """Pinned notes are always shown regardless of time window."""
    deltas = {"24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30)}
    if rozsah not in deltas:  # "vse" or unrecognised
        return qs
    since = timezone.now() - deltas[rozsah]
    return qs.filter(Q(is_pinned=True) | Q(created_at__gte=since))


def _apply_horizon_filter(qs, horizont):
    """Default (empty string) = short+mid; 'vse' = all three."""
    if horizont == Note.HORIZON_SHORT:
        return qs.filter(horizon=Note.HORIZON_SHORT)
    if horizont == Note.HORIZON_MID:
        return qs.filter(horizon=Note.HORIZON_MID)
    if horizont == Note.HORIZON_LONG:
        return qs.filter(horizon=Note.HORIZON_LONG)
    if horizont == "vse":
        return qs
    # default: short + mid (long-range notes hidden until explicitly requested)
    return qs.filter(horizon__in=[Note.HORIZON_SHORT, Note.HORIZON_MID])


def _apply_country_filter(qs, zeme):
    """Notes tagged 'both' always pass through any country filter."""
    if zeme == Note.COUNTRY_CZ:
        return qs.filter(country__in=[Note.COUNTRY_CZ, Note.COUNTRY_BOTH])
    if zeme == Note.COUNTRY_SK:
        return qs.filter(country__in=[Note.COUNTRY_SK, Note.COUNTRY_BOTH])
    return qs  # "both" or unrecognised: no filter


def _filter_qs_string(**kwargs):
    """Build a URL query string from non-empty filter params (for pagination links)."""
    return urllib.parse.urlencode({k: v for k, v in kwargs.items() if v})


# ── Views ────────────────────────────────────────────────────────────────────

@login_required
def aktuality(request):
    autor = request.GET.get("autor", FILTER_ALL)
    rozsah = request.GET.get("rozsah", "7d")     # 24h / 7d / 30d / vse
    horizont = request.GET.get("horizont", "")   # "" = short+mid (default), short/mid/long/vse
    zeme = request.GET.get("zeme", "")           # "" = both, cz, sk

    # historie_pin: reverse OneToOne of cross-posted pin cards — select_related
    # so the card's summary/deep link doesn't cost a query per note
    notes_qs = Note.objects.select_related("author", "historie_pin").filter(is_hidden=False)
    filtered = _apply_filter(notes_qs, autor)
    filtered = _apply_time_filter(filtered, rozsah)
    filtered = _apply_horizon_filter(filtered, horizont)
    filtered = _apply_country_filter(filtered, zeme)

    # Paginate the feed (10 per page) so the page never grows unbounded
    paginator = Paginator(filtered, 10)
    page_obj = paginator.get_page(request.GET.get("strana"))
    notes = list(page_obj.object_list)
    for note in notes:
        note.user_can_modify = _can_modify(request.user, note)
        # Pin cards carry their mini-table right in the feed, so the values
        # are readable without opening the graph
        try:
            pin = note.historie_pin
        except HistoriePin.DoesNotExist:
            pin = None
        if pin is not None:
            note.pin_stats = _pin_stats_for(pin)

    # Fetch last two short-range batches in one shared query pair.
    # latest_rows (newest batch only) feeds the weather panel and chart.
    # all_rows (both batches) feeds the revision summary — no separate queries.
    all_rows, batch_dates = _get_latest_short_rows(batches=2)
    latest_date = batch_dates[0] if batch_dates else None
    latest_rows = [r for r in all_rows if r.issued_at and r.issued_at.date() == latest_date] if latest_date else []

    since_login = _get_since_login(request.user.last_login)
    filter_chips = _build_filter_chips(notes_qs)
    # Pass the raw dict — the json_script template filter handles serialization.
    # Pre-dumping here double-encodes: JSON.parse in the browser then yields a
    # string instead of an object and the chart silently renders nothing.
    chart_json = _get_chart_data(latest_rows)
    mid_json, long_json = _get_seasonal_chart_data()
    # Full list, not top-5 — the sidebar renders it as a long scrollable column
    revision_summary = _get_revision_summary(all_rows, batch_dates, limit=None)

    has_historical = cache.get(_HISTORICAL_EXISTS_KEY)
    if has_historical is None:
        has_historical = HistoricalActual.objects.exists()
        cache.set(_HISTORICAL_EXISTS_KEY, has_historical, _CACHE_TTL)
    has_mid = bool(mid_json.get("cz") or mid_json.get("sk"))
    has_long = bool(long_json.get("cz") or long_json.get("sk"))

    # Query string for pagination links (all active filters, no strana)
    pagination_qs = _filter_qs_string(autor=autor, rozsah=rozsah, horizont=horizont, zeme=zeme)

    return render(request, "notes/aktuality.html", {
        "notes": notes,
        "user_can_pin": _can_pin(request.user),
        "form": NoteForm(),
        "since_login": since_login,
        "filter_chips": filter_chips,
        "active_filter": autor,
        "active_rozsah": rozsah,
        "active_horizont": horizont,
        "active_zeme": zeme,
        "pagination_qs": pagination_qs,
        "chart_json": chart_json,
        "mid_json": mid_json,
        "long_json": long_json,
        "has_mid": has_mid,
        "has_long": has_long,
        "revision_summary": revision_summary,
        "has_historical": has_historical,
        "page_obj": page_obj,
    })


@login_required
def note_create(request):
    if request.method == "POST":
        form = NoteForm(request.POST)
        if form.is_valid():
            note = form.save(commit=False)
            note.author = request.user
            note.note_type = Note.TYPE_HUMAN
            note.save()
            return redirect("notes:aktuality")
    else:
        form = NoteForm()
    return render(request, "notes/note_form.html", {"form": form, "action": "Přidat poznámku"})


@login_required
def note_edit(request, pk):
    note = get_object_or_404(Note, pk=pk)
    if not _can_modify(request.user, note):
        raise Http404

    if request.method == "POST":
        form = NoteForm(request.POST, instance=note)
        if form.is_valid():
            form.save()
            return redirect("notes:aktuality")
    else:
        form = NoteForm(instance=note)
    return render(request, "notes/note_form.html", {"form": form, "action": "Upravit poznámku"})


@login_required
def note_delete(request, pk):
    note = get_object_or_404(Note, pk=pk)
    if not _can_modify(request.user, note):
        raise Http404
    if request.method == "POST":
        note.delete()
    return redirect("notes:aktuality")


@login_required
def note_pin(request, pk):
    if not _can_pin(request.user):
        raise Http404
    note = get_object_or_404(Note, pk=pk)
    if request.method == "POST":
        note.is_pinned = not note.is_pinned
        note.save(update_fields=["is_pinned"])
    return redirect("notes:aktuality")


# ── Historie pins ───────────────────────────────────────────────────────────

def _pin_series_target(sel):
    """Map a pin's sel ("cz"/"sk"/region slug/point pk) to _historical_series kwargs."""
    if sel == "cz":
        return {"country": "CZ"}
    if sel == "sk":
        return {"country": "SK"}
    if sel in _REGION_LABELS:
        return {"macro_region": sel}
    return {"point_id": int(sel) if sel.isdigit() else -1}


def _stats_from_daily(daily, doy_from, doy_to, roky):
    """
    min/max/avg/std over daily values inside [doy_from, doy_to] across the
    last `roky` years — the same year-window rule the comparison view uses.
    Returns None when the slice has no data.
    """
    current_year = max((r["year"] for r in daily), default=None)
    vals = [
        r["value"] for r in daily
        if r["value"] is not None and doy_from <= int(r["x"]) <= doy_to
        and (current_year is None or r["year"] > current_year - roky)
    ]
    if not vals:
        return None
    avg = sum(vals) / len(vals)
    std = (sum((v - avg) ** 2 for v in vals) / len(vals)) ** 0.5
    return {
        "min": round(min(vals), 1), "max": round(max(vals), 1),
        "avg": round(avg, 1), "std": round(std, 1),
    }


def _pins_context(user, sel, metric, gran, country, point_id, rows, macro_region=None):
    """
    Pins matching the currently displayed bod+metric, newest first, no count
    limit. Each gets marker/shading x positions in the chart's current x
    units (doy or week) and a min/max/avg/std mini-table computed from the
    same daily series + year window rules the comparison view itself uses
    (rows is reused when the chart is already daily, so plna-weekly is the
    only mode paying an extra query — and only when pins exist).
    """
    pin_rows = list(
        HistoriePin.objects.select_related("author")
        .filter(is_hidden=False, sel=sel, metric=metric)
        .order_by("-created_at")
    )
    if not pin_rows:
        return []

    daily = rows if gran == "d" else _historical_series(
        country=country, point_id=point_id, macro_region=macro_region,
        granularity="d", metric=metric,
    )

    def to_x(doy):
        return doy if gran == "d" else (doy - 1) // 7 + 1

    pins = []
    for p in pin_rows:
        f, t = parse_dm(p.od), parse_dm(p.do)
        if f is None or t is None:
            continue
        if f > t:
            f, t = t, f
        pins.append({
            "id": p.pk,
            "x": to_x((f + t) // 2),
            "x0": to_x(f),
            "x1": to_x(t),
            "od": p.od, "do": p.do, "roky": p.roky,
            "author": p.author.username,
            "created": p.created_at,
            "body": p.body,
            "is_pinned": p.is_pinned,
            "can_modify": _can_modify(user, p),
            "stats": _stats_from_daily(daily, f, t, p.roky),
        })
    return pins


def _pin_stats_for(pin):
    """
    Stats for a single pin outside the Historie page (Aktuality card).
    Cached 1h per pin — history moves at most once a day.
    """
    key = f"pin_stats_{pin.pk}"
    stats = cache.get(key)
    if stats is None:
        f, t = parse_dm(pin.od), parse_dm(pin.do)
        if f is None or t is None:
            stats = {}
        else:
            if f > t:
                f, t = t, f
            daily = _historical_series(
                granularity="d", metric=pin.metric, **_pin_series_target(pin.sel),
            )
            stats = _stats_from_daily(daily, f, t, pin.roky) or {}
        cache.set(key, stats, 3600)
    return stats or None


def _create_pin_feed_note(pin):
    """
    Cross-post a pin into the Aktuality feed as a regular human note holding
    the pin's comment; the card's param summary + deep link render from the
    linked pin (note.historie_pin) in _note_card.html. Lifecycle of the card
    is the standard prune_notes one, independent of the pin's own.
    """
    country = Note.COUNTRY_BOTH
    if pin.sel in (Note.COUNTRY_CZ, Note.COUNTRY_SK):
        country = pin.sel
    elif pin.sel in WeatherPoint.MACRO_REGION_COUNTRY:
        country = WeatherPoint.MACRO_REGION_COUNTRY[pin.sel].lower()
    elif pin.sel.isdigit():
        point = WeatherPoint.objects.filter(pk=pin.sel).first()
        if point:
            country = point.country.lower()
    note = Note.objects.create(
        author=pin.author,
        body=pin.body,
        note_type=Note.TYPE_HUMAN,
        country=country,
    )
    pin.feed_note = note
    pin.save(update_fields=["feed_note"])


@login_required
def pin_create(request):
    if request.method != "POST":
        return redirect("historie")
    form = HistoriePinForm(request.POST)
    if not form.is_valid():
        return HttpResponseBadRequest("Neplatné parametry pinu.")
    pin = form.save(commit=False)
    pin.author = request.user
    pin.save()
    if pin.show_in_feed:
        _create_pin_feed_note(pin)
    return redirect(pin.historie_url())


@login_required
def pin_edit(request, pk):
    pin = get_object_or_404(HistoriePin, pk=pk)
    if not _can_modify(request.user, pin):
        raise Http404

    if request.method == "POST":
        form = PinEditForm(request.POST, instance=pin)
        if form.is_valid():
            form.save()
            # The cross-posted card carries the same comment — keep it in sync
            if pin.feed_note_id:
                Note.objects.filter(pk=pin.feed_note_id).update(body=pin.body)
            return redirect(pin.historie_url())
    else:
        form = PinEditForm(instance=pin)
    return render(request, "notes/note_form.html", {
        "form": form,
        "action": "Upravit pin",
        "cancel_url": pin.historie_url(),
    })


@login_required
def pin_delete(request, pk):
    pin = get_object_or_404(HistoriePin, pk=pk)
    if not _can_modify(request.user, pin):
        raise Http404
    url = pin.historie_url()
    if request.method == "POST":
        # Model delete() also removes the cross-posted Aktuality card
        pin.delete()
    return redirect(url)


@login_required
def pin_toggle(request, pk):
    if not _can_pin(request.user):
        raise Http404
    pin = get_object_or_404(HistoriePin, pk=pk)
    if request.method == "POST":
        pin.is_pinned = not pin.is_pinned
        pin.save(update_fields=["is_pinned"])
    return redirect(pin.historie_url())


def _progression(daily, doy_from, doy_to, roky, gran="w"):
    """
    Per-week (gran="w") or per-day (gran="d") means over the pin's doy range
    across its year window, with step-over-step deltas (°C/mm and %, per the
    dashboard delta convention). Feeds the printable "postupnost" tables on
    Subhistorie. Labels come from the same non-leap reference year parse_dm
    uses.
    """
    current_year = max((r["year"] for r in daily), default=None)
    if current_year is None:
        return []
    buckets = defaultdict(list)
    for r in daily:
        if r["value"] is None:
            continue
        x = int(r["x"])
        if doy_from <= x <= doy_to and r["year"] > current_year - roky:
            buckets[(x - 1) // 7 if gran == "w" else x].append(r["value"])

    steps = []
    prev = None
    for key in sorted(buckets):
        vals = buckets[key]
        avg = sum(vals) / len(vals)
        if gran == "w":
            start = date(2001, 1, 1) + timedelta(days=key * 7)
            end = start + timedelta(days=6)
            label = f"{start.day}. {start.month}. – {end.day}. {end.month}."
        else:
            d = date(2001, 1, 1) + timedelta(days=key - 1)
            label = f"{d.day}. {d.month}."
        delta = delta_pct = None
        if prev is not None:
            delta = round(avg - prev, 1)
            # % is meaningless against a near-zero base (−0,1 °C → −1446 %);
            # only show it when the previous step is at least 2 °C/mm away
            # from zero
            delta_pct = round((avg - prev) / abs(prev) * 100) if abs(prev) >= 2.0 else None
        steps.append({
            "label": label,
            "avg": round(avg, 1),
            "min": round(min(vals), 1),
            "max": round(max(vals), 1),
            "delta": delta,
            "delta_pct": delta_pct,
        })
        prev = avg
    return steps


@login_required
def subhistorie(request):
    """
    All pins in one printable place — no bod/metric context filter. Each pin
    shows its comment, stats and the weekly progression table computed from
    the same daily series rules as everywhere else. One series query per
    distinct (sel, metric) pair, shared across pins.
    """
    pin_rows = list(
        HistoriePin.objects.select_related("author")
        .filter(is_hidden=False)
        .order_by("-created_at")
    )
    series_by_key = {}
    entries = []
    for pin in pin_rows:
        f, t = parse_dm(pin.od), parse_dm(pin.do)
        if f is None or t is None:
            continue
        if f > t:
            f, t = t, f
        key = (pin.sel, pin.metric)
        if key not in series_by_key:
            series_by_key[key] = _historical_series(
                granularity="d", metric=pin.metric, **_pin_series_target(pin.sel),
            )
        daily = series_by_key[key]
        entries.append({
            "pin": pin,
            "stats": _stats_from_daily(daily, f, t, pin.roky),
            "weeks": _progression(daily, f, t, pin.roky, gran="w"),
            "days": _progression(daily, f, t, pin.roky, gran="d"),
            "can_modify": _can_modify(request.user, pin),
        })
    return render(request, "notes/subhistorie.html", {
        "entries": entries,
        "user_can_pin": _can_pin(request.user),
    })


# ── Revision tracker ────────────────────────────────────────────────────────

def _mlr_revision_context(horizon, threshold, proti_raw=""):
    """
    Same shape as the aktuální branch below, but for a MediumLongRangeForecast
    horizon (EC46/SEAS5): compares the latest issued_at snapshot against the
    previous one by default, or against an older stored snapshot picked via
    ?proti=YYYY-MM-DD (up to 14 snapshots back). Uses temp_mean directly (no
    max/min to split). precip_prob_delta is a delta of the RAW mm sum stored
    in the precip_probability field, not a real probability — Open-Meteo's
    seasonal API has no true precip-probability variable; see the comment in
    fetch_seasonal.py (and fetch_ec46.py) for why that field holds mm.
    Returns a dict to merge into the view context — either {"revisions",
    "latest_batch", "previous_batch", "batch_options"} or
    {"revisions": [], "not_enough_data": True}.
    """
    today = date.today()

    batch_dates = list(
        MediumLongRangeForecast.objects
        .filter(horizon=horizon)
        .order_by("-issued_at")
        .values_list("issued_at", flat=True)
        .distinct()[:14]
    )
    if len(batch_dates) < 2:
        return {"revisions": [], "not_enough_data": True}

    latest, previous = batch_dates[0], batch_dates[1]
    for bd in batch_dates[1:]:
        if bd.date().isoformat() == proti_raw:
            previous = bd
            break

    def get_nat_series(batch_dt, country):
        rows = list(
            MediumLongRangeForecast.objects.filter(
                horizon=horizon, issued_at=batch_dt, point__country=country,
                target_date__gte=today,
            )
        )
        by_date = defaultdict(list)
        for r in rows:
            by_date[r.target_date].append(r)
        return {
            fd: {"temp_mean": _avg(day_rows, "temp_mean"), "precip_prob": _avg(day_rows, "precip_probability")}
            for fd, day_rows in sorted(by_date.items())
        }

    revisions = []
    for country, label in [("CZ", "ČR"), ("SK", "SR")]:
        latest_series = get_nat_series(latest, country)
        prev_series = get_nat_series(previous, country)
        for fd in sorted(latest_series):
            if fd not in prev_series:
                continue
            lt = latest_series[fd]["temp_mean"]
            pt = prev_series[fd]["temp_mean"]
            lp = latest_series[fd]["precip_prob"]
            pp = prev_series[fd]["precip_prob"]
            temp_delta = round(lt - pt, 1) if lt is not None and pt is not None else None
            precip_prob_delta = round(lp - pp) if lp is not None and pp is not None else None
            if temp_delta is not None and abs(temp_delta) >= threshold:
                revisions.append({
                    "country": label,
                    "date": fd,
                    "temp_latest": lt,
                    "temp_prev": pt,
                    "temp_delta": temp_delta,
                    "temp_pct": _temp_pct(temp_delta, pt),
                    "precip_prob_delta": precip_prob_delta,
                })

    return {
        "revisions": revisions, "latest_batch": latest, "previous_batch": previous,
        "batch_options": batch_dates[1:],
    }


@login_required
def revision_tracker(request):
    bucket = request.GET.get("rozsah", "aktualni")
    if bucket not in ("aktualni", "strednedobe", "dlouhodobe"):
        bucket = "aktualni"
    zeme = request.GET.get("zeme", "")
    if zeme not in ("cz", "sk"):
        zeme = ""
    # Which older snapshot to compare the latest against — defaults to the
    # immediately previous one ("co se teď stalo")
    proti_raw = request.GET.get("proti", "")
    context = {"bucket": bucket, "zeme": zeme}

    if bucket == "aktualni":
        today = date.today()

        # All distinct issued_at dates for short-range, newest first
        batch_dates = list(
            DailyForecast.objects
            .filter(horizon=DailyForecast.HORIZON_SHORT, issued_at__isnull=False)
            .order_by("-issued_at__date")
            .values_list("issued_at__date", flat=True)
            .distinct()[:14]  # last 2 weeks of snapshots
        )

        if len(batch_dates) >= 2:
            latest = batch_dates[0]
            previous = batch_dates[1]
            for bd in batch_dates[1:]:
                if bd.isoformat() == proti_raw:
                    previous = bd
                    break

            def get_nat_series(batch_date, country):
                rows = list(
                    DailyForecast.objects.filter(
                        horizon=DailyForecast.HORIZON_SHORT,
                        issued_at__date=batch_date,
                        point__country=country,
                        forecast_date__gte=today,
                    ).order_by("forecast_date")
                )
                from collections import defaultdict
                by_date = defaultdict(list)
                for r in rows:
                    by_date[r.forecast_date].append(r)
                return {fd: {"temp_max": _avg(day_rows, "temperature_max"), "precip": _avg(day_rows, "precipitation_sum")} for fd, day_rows in sorted(by_date.items())}

            revisions = []
            for country, label in [("CZ", "ČR"), ("SK", "SR")]:
                latest_series = get_nat_series(latest, country)
                prev_series = get_nat_series(previous, country)
                for fd in sorted(latest_series):
                    if fd not in prev_series:
                        continue
                    lt = latest_series[fd]["temp_max"]
                    pt = prev_series[fd]["temp_max"]
                    lp = latest_series[fd]["precip"]
                    pp = prev_series[fd]["precip"]
                    temp_delta = round(lt - pt, 1) if lt is not None and pt is not None else None
                    precip_delta = round(lp - pp, 1) if lp is not None and pp is not None else None
                    if temp_delta is not None and abs(temp_delta) >= 0.5:
                        revisions.append({
                            "country": label,
                            "date": fd,
                            "temp_latest": lt,
                            "temp_prev": pt,
                            "temp_delta": temp_delta,
                            "temp_pct": _temp_pct(temp_delta, pt),
                            "precip_delta": precip_delta,
                        })

            context["revisions"] = revisions
            context["latest_batch"] = latest
            context["previous_batch"] = previous
            context["batch_options"] = batch_dates[1:]
        else:
            context["revisions"] = []
            context["not_enough_data"] = True

    elif bucket == "strednedobe":
        context.update(_mlr_revision_context(
            MediumLongRangeForecast.HORIZON_EC46, threshold=1.0, proti_raw=proti_raw,
        ))

    elif bucket == "dlouhodobe":
        context.update(_mlr_revision_context(
            MediumLongRangeForecast.HORIZON_SEAS5, threshold=1.0, proti_raw=proti_raw,
        ))

    # Country filter applies to whichever bucket produced the rows
    if zeme and context.get("revisions"):
        wanted = "ČR" if zeme == "cz" else "SR"
        context["revisions"] = [r for r in context["revisions"] if r["country"] == wanted]

    context["user_can_pin"] = _can_pin(request.user)
    return render(request, "notes/revision_tracker.html", context)


@login_required
def revize_check_now(request):
    """
    Leader/admin manual trigger of the daily EC46/SEAS5 revision-note check —
    same command cron-daily runs, just outside the schedule. Idempotent, so
    pressing it repeatedly can't duplicate the day's notes.
    """
    if not _can_pin(request.user):
        raise Http404
    if request.method == "POST":
        call_command("detect_mlr_changes")
    return redirect("notes:revision_tracker")


def _get_custom_week_data(point, year, week):
    """
    Data for one arbitrary ISO year/week for a single point, checked in order:
    short-range forecast (min/max) → historical actuals (min/max) → seasonal
    forecast (mean only). Returns None if the week is invalid or no source has
    any rows for it.
    """
    try:
        week_start = date.fromisocalendar(year, week, 1)
    except ValueError:
        return None
    week_end = week_start + timedelta(days=6)

    short_rows = list(
        DailyForecast.objects
        .filter(point=point, horizon=DailyForecast.HORIZON_SHORT,
                forecast_date__gte=week_start, forecast_date__lte=week_end)
        .order_by("forecast_date", "-issued_at")
    )
    if short_rows:
        latest_per_day = {}
        for r in short_rows:
            latest_per_day.setdefault(r.forecast_date, r)  # first hit = newest issued_at
        ordered = sorted(latest_per_day.values(), key=lambda r: r.forecast_date)
        return {
            "kind": "range",
            "dates": [r.forecast_date.isoformat() for r in ordered],
            "temps_max": [r.temperature_max for r in ordered],
            "temps_min": [r.temperature_min for r in ordered],
        }

    hist_rows = list(
        HistoricalActual.objects
        .filter(point=point, date__gte=week_start, date__lte=week_end)
        .order_by("date")
    )
    if hist_rows:
        return {
            "kind": "range",
            "dates": [r.date.isoformat() for r in hist_rows],
            "temps_max": [r.temp_max for r in hist_rows],
            "temps_min": [r.temp_min for r in hist_rows],
        }

    seas_rows = list(
        MediumLongRangeForecast.objects
        .filter(point=point, target_date__gte=week_start, target_date__lte=week_end)
        .order_by("target_date", "-issued_at")
    )
    if seas_rows:
        latest_per_day = {}
        for r in seas_rows:
            latest_per_day.setdefault(r.target_date, r)
        ordered = sorted(latest_per_day.values(), key=lambda r: r.target_date)
        temps = [round(r.temp_mean, 1) for r in ordered if r.temp_mean is not None]
        if temps:
            return {
                "kind": "mean",
                "dates": [r.target_date.isoformat() for r in ordered if r.temp_mean is not None],
                "temps": temps,
            }

    return None


# ── Point detail ─────────────────────────────────────────────────────────────

@login_required
def point_detail(request):
    points = list(WeatherPoint.objects.all())
    if not points:
        return render(request, "notes/point_detail.html", {"points": []})

    today = date.today()

    # ── National summary + per-point today rows for the selector ──
    batch_rows, _ = _get_latest_short_rows()
    _, cz_avg, sk_avg, cz_today, sk_today = _get_weather_panel(batch_rows)

    # Lookup dict for optional today-temp display in selector (may be incomplete)
    today_by_pid = {r.point_id: r for r in (cz_today or []) + (sk_today or [])}
    # All points split by country (used for selector regardless of forecast data)
    cz_pts = sorted([p for p in points if p.country == "CZ"], key=lambda p: p.name)
    sk_pts = sorted([p for p in points if p.country == "SK"], key=lambda p: p.name)

    # ── Report filters (r_ prefix avoids collision with bod/land params) ──
    r_rozsah = request.GET.get("r_rozsah", "7d")
    r_horizont = request.GET.get("r_horizont", "")
    r_zeme = request.GET.get("r_zeme", "")

    # ── Forecast table day count (?pd=) — default 7, custom 1–16 ──
    try:
        forecast_days = int(request.GET.get("pd", 7))
    except (ValueError, TypeError):
        forecast_days = 7
    forecast_days = max(1, min(16, forecast_days))

    def _filter_reports(base_qs):
        qs = _apply_time_filter(base_qs, r_rozsah)
        qs = _apply_horizon_filter(qs, r_horizont)
        qs = _apply_country_filter(qs, r_zeme)
        return list(qs.order_by("-is_pinned", "-created_at")[:12])

    # ── Custom year/week horizon (?custom_year=&custom_week=) ──
    min_custom_year = 2015
    max_custom_year = today.year + 1
    custom_year_raw = request.GET.get("custom_year")
    custom_week_raw = request.GET.get("custom_week")
    custom_active = bool(custom_year_raw and custom_week_raw)
    custom_year = custom_week = None
    if custom_active:
        try:
            custom_year = min(max_custom_year, max(min_custom_year, int(custom_year_raw)))
            custom_week = min(53, max(1, int(custom_week_raw)))
        except ValueError:
            custom_active = False

    # ── Determine selection: ?land=cz/sk → national view; ?bod=pk → city ──
    land = request.GET.get("land", "").lower()
    selected_land = land if land in ("cz", "sk") else None
    selected = None

    if selected_land:
        nat_avg = cz_avg if selected_land == "cz" else sk_avg
        nat_country = selected_land.upper()
        base = (Note.objects
                .filter(note_type__startswith="system_", is_hidden=False)
                .filter(Q(country=selected_land) | Q(country=Note.COUNTRY_BOTH)))
        reports = _filter_reports(base)
        return render(request, "notes/point_detail.html", {
            "points": points,
            "cz_pts": cz_pts, "sk_pts": sk_pts,
            "today_by_pid": today_by_pid,
            "cz_avg": cz_avg, "sk_avg": sk_avg,
            "selected_land": selected_land,
            "nat_avg": nat_avg,
            "nat_country": nat_country,
            "reports": reports,
            "r_rozsah": r_rozsah, "r_horizont": r_horizont, "r_zeme": r_zeme,
            # No per-city data in national view
            "today_row": None, "forecast_rows": [],
            "chart_json": {"dates": [], "temps_max": [], "temps_min": []},
            "mid_chart": {"dates": [], "temps": []},
            "long_chart": {"dates": [], "temps": []},
            "has_mid": False, "has_long": False,
            "revision_deltas": [], "latest_issued": None,
            "custom_active": False, "custom_chart": {"kind": None},
            "custom_year": None, "custom_week": None,
            "min_custom_year": min_custom_year, "max_custom_year": max_custom_year,
            "forecast_days": forecast_days,
        })

    # ── City selection via ?bod=<id>, default to first point ──
    try:
        point_id = int(request.GET.get("bod", points[0].pk))
        selected = next((p for p in points if p.pk == point_id), points[0])
    except (ValueError, TypeError):
        selected = points[0]

    # Latest issued batch for this point
    latest_issued = (
        DailyForecast.objects
        .filter(point=selected, horizon=DailyForecast.HORIZON_SHORT, issued_at__isnull=False)
        .order_by("-issued_at")
        .values_list("issued_at__date", flat=True)
        .first()
    )

    forecast_rows = []
    chart_json = {"dates": [], "temps_max": [], "temps_min": []}
    revision_deltas = []
    today_row = None

    if latest_issued:
        all_rows = list(
            DailyForecast.objects
            .filter(point=selected, horizon=DailyForecast.HORIZON_SHORT, issued_at__date=latest_issued)
            .order_by("forecast_date")
        )

        today_matches = [r for r in all_rows if r.forecast_date == today]
        today_row = today_matches[0] if today_matches else None

        # Table starting from today (or nearest future date), capped to the
        # requested day count and to however many days are actually available.
        future_rows = [r for r in all_rows if r.forecast_date >= today]
        forecast_rows = future_rows[:forecast_days]

        # Full 16-day chart series
        chart_json = {
            "dates": [r.forecast_date.isoformat() for r in all_rows],
            "temps_max": [r.temperature_max for r in all_rows],
            "temps_min": [r.temperature_min for r in all_rows],
        }

        # Per-point revision deltas: compare last two issued batches
        batch_dates = list(
            DailyForecast.objects
            .filter(point=selected, horizon=DailyForecast.HORIZON_SHORT, issued_at__isnull=False)
            .order_by("-issued_at__date")
            .values_list("issued_at__date", flat=True)
            .distinct()[:2]
        )
        if len(batch_dates) >= 2:
            latest_b, prev_b = batch_dates[0], batch_dates[1]
            latest_map = {
                r.forecast_date: r.temperature_max
                for r in DailyForecast.objects.filter(
                    point=selected, horizon=DailyForecast.HORIZON_SHORT,
                    issued_at__date=latest_b, forecast_date__gte=today,
                )
            }
            prev_map = {
                r.forecast_date: r.temperature_max
                for r in DailyForecast.objects.filter(
                    point=selected, horizon=DailyForecast.HORIZON_SHORT,
                    issued_at__date=prev_b, forecast_date__gte=today,
                )
            }
            for fd in sorted(latest_map)[:7]:
                lt = latest_map.get(fd)
                pt = prev_map.get(fd)
                if lt is not None and pt is not None:
                    delta = round(lt - pt, 1)
                    if abs(delta) >= 0.5:
                        revision_deltas.append({
                            "date": fd,
                            "delta": delta,
                            "latest": lt,
                            "prev": pt,
                            "pct": _temp_pct(delta, pt),
                        })

    # ── Per-point seasonal series for the horizon switcher ──
    mid_chart = {"dates": [], "temps": []}
    long_chart = {"dates": [], "temps": []}
    latest_seas = (
        MediumLongRangeForecast.objects
        .filter(point=selected, horizon=MediumLongRangeForecast.HORIZON_SEAS5)
        .order_by("-issued_at")
        .values_list("issued_at", flat=True)
        .first()
    )
    if latest_seas:
        mid_cutoff = today + timedelta(days=120)
        for r in MediumLongRangeForecast.objects.filter(
            point=selected, horizon=MediumLongRangeForecast.HORIZON_SEAS5, issued_at=latest_seas,
        ).order_by("target_date"):
            if r.temp_mean is None:
                continue
            target = mid_chart if r.target_date <= mid_cutoff else long_chart
            target["dates"].append(r.target_date.isoformat())
            target["temps"].append(round(r.temp_mean, 1))

    # ── System reports for this point's country (hidden notes excluded) ──
    base = (Note.objects
            .filter(note_type__startswith="system_", is_hidden=False)
            .filter(Q(country=selected.country.lower()) | Q(country=Note.COUNTRY_BOTH)))
    reports = _filter_reports(base)

    # ── Custom year/week horizon, if requested ──
    custom_chart = {"kind": None}
    if custom_active:
        custom_chart = _get_custom_week_data(selected, custom_year, custom_week) or {"kind": None}

    return render(request, "notes/point_detail.html", {
        "points": points,
        "cz_pts": cz_pts, "sk_pts": sk_pts,
        "today_by_pid": today_by_pid,
        "cz_avg": cz_avg, "sk_avg": sk_avg,
        "selected_land": None,
        "selected": selected,
        "today_row": today_row,
        "forecast_rows": forecast_rows,
        "chart_json": chart_json,
        "mid_chart": mid_chart,
        "long_chart": long_chart,
        "has_mid": bool(mid_chart["dates"]),
        "has_long": bool(long_chart["dates"]),
        "reports": reports,
        "r_rozsah": r_rozsah, "r_horizont": r_horizont, "r_zeme": r_zeme,
        "revision_deltas": revision_deltas,
        "latest_issued": latest_issued,
        "custom_active": custom_active,
        "custom_chart": custom_chart,
        "custom_year": custom_year,
        "custom_week": custom_week,
        "min_custom_year": min_custom_year,
        "max_custom_year": max_custom_year,
        "forecast_days": forecast_days,
    })


# ── Historie (ERA5 multi-year overlay) ──────────────────────────────────────

class _ExtractDoy(Func):
    """EXTRACT(DOY FROM date) — day-of-year 1–366. Django has no built-in for this."""
    template = "EXTRACT(DOY FROM %(expressions)s)"
    output_field = IntegerField()


def _historical_series(country=None, point_id=None, macro_region=None, granularity="w", metric="t"):
    """
    Series aggregated in SQL, grouped by (year, x) where x is ISO
    week-of-year (weekly) or day-of-year (daily).

    Aggregation differs per metric — temperature is a state (average it),
    precipitation accumulates (sum it):
      - temp: Avg of the daily midpoint (temp_min + temp_max) / 2
      - precip weekly: Sum / Count(DISTINCT point) (per-point weekly total,
        averaged across points for national/regional aggregates)
      - precip daily: Avg across points
    """
    qs = HistoricalActual.objects.all()
    if point_id is not None:
        qs = qs.filter(point_id=point_id)
    elif macro_region is not None:
        qs = qs.filter(point__macro_region=macro_region)
    elif country is not None:
        qs = qs.filter(point__country=country)

    if granularity == "d":
        qs = qs.annotate(year=ExtractYear("date"), x=_ExtractDoy("date"))
    else:
        qs = qs.annotate(year=ExtractIsoYear("date"), x=ExtractWeek("date"))

    if metric == "p":
        if granularity == "d":
            value = Avg("precip_mm")
        else:
            value = ExpressionWrapper(
                Sum("precip_mm") * 1.0 / Count("point", distinct=True),
                output_field=FloatField(),
            )
    else:
        value = Avg((F("temp_min") + F("temp_max")) / 2.0)

    return list(
        qs.values("year", "x")
        .annotate(value=value)
        .order_by("year", "x")
    )


_MLR_FORECAST_CACHE_KEY = "historie_mlr_forecast_rows_v2"  # v2: rows carry point__macro_region


def _get_mlr_forecast_rows():
    """
    Future-dated temp rows from the latest EC46 snapshot plus the latest
    SEAS5 snapshot, all points (callers filter by point/country), as plain
    dicts. Cached for 1 hour like _get_seasonal_chart_data — the underlying
    data changes at most once per day, and the raw fetch is cached rather
    than any merged/aggregated result, which varies per point/granularity.
    """
    cached = cache.get(_MLR_FORECAST_CACHE_KEY)
    if cached is not None:
        return cached

    today = date.today()
    rows = []
    for horizon in (MediumLongRangeForecast.HORIZON_EC46, MediumLongRangeForecast.HORIZON_SEAS5):
        latest = (
            MediumLongRangeForecast.objects
            .filter(horizon=horizon)
            .order_by("-issued_at")
            .values_list("issued_at", flat=True)
            .first()
        )
        if latest is None:
            continue
        rows += list(
            MediumLongRangeForecast.objects
            .filter(horizon=horizon, issued_at=latest,
                    target_date__gte=today, temp_mean__isnull=False)
            .values("point_id", "point__country", "point__macro_region", "target_date", "temp_mean", "horizon")
            .order_by("target_date")
        )
    cache.set(_MLR_FORECAST_CACHE_KEY, rows, _CACHE_TTL)
    return rows


def _forecast_overlay_series(country=None, point_id=None, macro_region=None, granularity="w",
                             metric="t", doy_from=None, doy_to=None):
    """
    Forecast continuation of the current year for the Historie overlay,
    aggregated to the same x axis and with the same averaging rules as
    _historical_series (temp: mean of daily midpoints; precip weekly:
    sum / distinct-point-count; precip daily: avg across points).

    Temperature merges three sources per date — short-range 16-day
    DailyForecast wins on overlap, then EC46, then SEAS5 (temp_mean).
    Precipitation uses the short range only: MediumLongRangeForecast has
    just precip_probability, which isn't comparable to mm sums.

    Dates past the current (ISO) year are dropped — their x would wrap
    around to 1 and corrupt the overlay. Returns [{x, value}] sorted by x.
    """
    today = date.today()

    def wanted(pid, pcountry, pmacro):
        if point_id is not None:
            return pid == point_id
        if macro_region is not None:
            return pmacro == macro_region
        if country is not None:
            return pcountry == country
        return True

    # Per-source {date: {point_id: daily value}} maps, in priority order.
    short_map = defaultdict(dict)
    short_rows, _ = _get_latest_short_rows()
    # Two ingests on the same day share a batch date — sort by issued_at so
    # the newest snapshot's value wins for a duplicated (point, date).
    for r in sorted(short_rows, key=lambda r: r.issued_at):
        if r.forecast_date < today or not wanted(r.point_id, r.point.country, r.point.macro_region):
            continue
        if metric == "p":
            if r.precipitation_sum is not None:
                short_map[r.forecast_date][r.point_id] = r.precipitation_sum
        elif r.temperature_min is not None and r.temperature_max is not None:
            short_map[r.forecast_date][r.point_id] = (r.temperature_min + r.temperature_max) / 2.0

    sources = [short_map]
    if metric == "t":
        ec46_map, seas5_map = defaultdict(dict), defaultdict(dict)
        for r in _get_mlr_forecast_rows():
            if not wanted(r["point_id"], r["point__country"], r.get("point__macro_region", "")):
                continue
            target = ec46_map if r["horizon"] == MediumLongRangeForecast.HORIZON_EC46 else seas5_map
            target[r["target_date"]][r["point_id"]] = r["temp_mean"]
        sources += [ec46_map, seas5_map]

    merged = {}
    for src in sources:
        for d, pts in src.items():
            merged.setdefault(d, pts)

    if granularity == "d":
        def x_of(d): return d.timetuple().tm_yday  # matches EXTRACT(DOY ...)
        def in_current_year(d): return d.year == today.year
    else:
        def x_of(d): return d.isocalendar()[1]     # matches ExtractWeek (ISO)
        def in_current_year(d): return d.isocalendar()[0] == today.isocalendar()[0]

    buckets = defaultdict(list)  # x → [(point_id, value)]
    for d, pts in merged.items():
        if not in_current_year(d):
            continue
        x = x_of(d)
        if doy_from is not None and doy_to is not None and not (doy_from <= x <= doy_to):
            continue
        buckets[x].extend(pts.items())

    series = []
    for x in sorted(buckets):
        pairs = buckets[x]
        vals = [v for _, v in pairs]
        if metric == "p" and granularity != "d":
            value = sum(vals) / len({pid for pid, _ in pairs})
        else:
            value = sum(vals) / len(vals)
        series.append({"x": x, "value": round(value, 1)})
    return series


@login_required
def historie(request):
    points = list(WeatherPoint.objects.order_by("country", "name"))

    sel = request.GET.get("bod", "cz")
    gran = request.GET.get("g", "w")
    if gran not in ("w", "d"):
        gran = "w"
    metric = request.GET.get("m", "t")
    if metric not in ("t", "p"):
        metric = "t"
    rozsah = request.GET.get("rozsah", "plna")
    if rozsah not in ("plna", "vlastni"):
        rozsah = "plna"
    rezim = request.GET.get("rezim", "abs")
    if rezim not in ("abs", "pct"):
        rezim = "abs"

    # Manual comparison: date range typed as "D.M" (e.g. 12.7 till 15.11)
    # plus how many recent years to compare
    od_raw = request.GET.get("od", "")
    do_raw = request.GET.get("do", "")
    try:
        roky = max(2, min(12, int(request.GET.get("roky", "5"))))
    except ValueError:
        roky = 5
    doy_from = parse_dm(od_raw)
    doy_to = parse_dm(do_raw)
    if rozsah == "vlastni":
        # Missing/invalid bounds default to the whole year, so the comparison
        # works with just "počet let" (or nothing at all) filled in
        if doy_from is None:
            od_raw, doy_from = "1.1", 1
        if doy_to is None:
            do_raw, doy_to = "31.12", 365
    if doy_from is not None and doy_to is not None and doy_from > doy_to:
        doy_from, doy_to = doy_to, doy_from
        od_raw, do_raw = do_raw, od_raw

    scope, selection_label, _ = _parse_bod(sel, points)
    if scope is None:
        sel = "cz"
        scope, selection_label, _ = _parse_bod(sel, points)
    country = scope.get("country")
    point_id = scope.get("point_id")
    macro_region = scope.get("macro_region")

    # Overlay: full history, or manual comparison (daily, custom doy range,
    # last `roky` years, all traces visible)
    if rozsah == "vlastni":
        gran = "d"
        rezim = "abs"
    rows = _historical_series(country=country, point_id=point_id, macro_region=macro_region,
                              granularity=gran, metric=metric)

    # "Current year" must be determined from the full unfiltered series,
    # not from the doy-filtered by_year below — a custom range that reaches
    # into the future (e.g. today..end of year) has no rows yet for the
    # current year in that slice, which would otherwise make max(by_year)
    # silently resolve to last year and shift the whole "last N years"
    # window off by one.
    current_year = max((r["year"] for r in rows), default=None)

    by_year = {}
    for r in rows:
        if r["value"] is None:
            continue
        x = int(r["x"])
        if rozsah == "vlastni" and not (doy_from <= x <= doy_to):
            continue
        by_year.setdefault(r["year"], {"x": [], "values": []})
        by_year[r["year"]]["x"].append(x)
        by_year[r["year"]]["values"].append(round(r["value"], 1))

    if rozsah == "vlastni" and current_year is not None:
        by_year = {y: s for y, s in by_year.items() if y > current_year - roky}

    # Similarity % of each year vs the current year over overlapping x:
    # 100 % = identical, each °C (or mm) of mean abs difference costs 12.5 pts.
    # current_year may have no rows in this doy slice (e.g. range reaches
    # into the future) — cur_map stays empty and similarity is skipped.
    cur_map = dict(zip(by_year[current_year]["x"], by_year[current_year]["values"])) if current_year in by_year else {}
    for year, s in by_year.items():
        if year == current_year or not cur_map:
            s["sim"] = None
            continue
        diffs = [abs(v - cur_map[x]) for x, v in zip(s["x"], s["values"]) if x in cur_map]
        s["sim"] = max(0, round(100 - (sum(diffs) / len(diffs)) * 12.5)) if diffs else None

    # Percentage mode: deviation from the all-years average per x,
    # normalized by the seasonal amplitude so values stay sane around 0 °C.
    if rezim == "pct" and by_year:
        clim = defaultdict(list)
        for s in by_year.values():
            for x, v in zip(s["x"], s["values"]):
                clim[x].append(v)
        clim_avg = {x: sum(vs) / len(vs) for x, vs in clim.items()}
        amplitude = (max(clim_avg.values()) - min(clim_avg.values())) or 1.0
        for s in by_year.values():
            s["values"] = [
                round((v - clim_avg[x]) / amplitude * 100, 1)
                for x, v in zip(s["x"], s["values"])
            ]

    # Dashed forecast continuation of the current year — abs mode only
    # (pct deviations need a climatology row per x, which future dates
    # don't have). Skipped when the current year has no real rows in the
    # selected slice: there'd be no trace to visually continue from.
    if rezim == "abs" and current_year in by_year:
        fc = _forecast_overlay_series(
            country=country, point_id=point_id, macro_region=macro_region,
            granularity=gran, metric=metric,
            doy_from=doy_from if rozsah == "vlastni" else None,
            doy_to=doy_to if rozsah == "vlastni" else None,
        )
        cur = by_year[current_year]
        real_x = set(cur["x"])
        fc = [p for p in fc if p["x"] not in real_x]
        if fc:
            # Prepend the last real point so the dashed segment connects
            # to the real trace with no visual gap.
            cur["forecast_x"] = [cur["x"][-1]] + [p["x"] for p in fc]
            cur["forecast_values"] = [cur["values"][-1]] + [p["value"] for p in fc]

    def _year_entry(y, s):
        entry = {
            "year": y,
            "x": s["x"],
            "values": s["values"],
            "name": f"{y} · {s['sim']} %" if s.get("sim") is not None else str(y),
        }
        if "forecast_x" in s:
            entry["forecast_x"] = s["forecast_x"]
            entry["forecast_values"] = s["forecast_values"]
        return entry

    chart = {
        "mode": "overlay",
        "granularity": gran,
        "metric": metric,
        "rezim": rezim,
        "years": [_year_entry(y, s) for y, s in sorted(by_year.items())],
    }
    if rozsah == "vlastni":
        chart["all_visible"] = True
        chart["xrange"] = [doy_from, doy_to]
    has_data = bool(by_year)

    # Pins live in doy space, so they only render in overlay modes
    pins = _pins_context(
        request.user, sel=sel, metric=metric, gran=gran,
        country=country, point_id=point_id, macro_region=macro_region, rows=rows,
    )

    # Side column: the same Aktuality feed cards, read-only, for side-by-side
    # comparison with the chart (no filters — just the freshest notes)
    feed_notes = list(
        Note.objects.select_related("author", "historie_pin").filter(is_hidden=False)[:10]
    )

    return render(request, "notes/historie.html", {
        "chart_json": chart,  # raw dict — json_script serializes it (never pre-dump!)
        "has_data": has_data,
        "feed_notes": feed_notes,
        "points": points,
        **_selector_context(points),
        "sel": sel,
        "gran": gran,
        "metric": metric,
        "rozsah": rozsah,
        "rezim": rezim,
        "od": od_raw,
        "do": do_raw,
        "roky": roky,
        "selection_label": selection_label,
        "pins": pins,
        "user_can_pin": _can_pin(request.user),
        # Slim marker payload for the chart JS (x for placement, x0/x1 for
        # the selected-pin range shading, the rest feeds the hover text)
        "pins_marker": [
            {"id": p["id"], "x": p["x"], "x0": p["x0"], "x1": p["x1"],
             "author": p["author"], "od": p["od"], "do": p["do"]}
            for p in pins
        ],
    })
