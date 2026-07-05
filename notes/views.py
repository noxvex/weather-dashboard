from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render

from ingest.models import DailyForecast
from .forms import NoteForm
from .models import Note

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


def _get_weather_panel():
    today = date.today()
    nearest_date = (
        DailyForecast.objects
        .filter(horizon=DailyForecast.HORIZON_SHORT, forecast_date__gte=today)
        .order_by("forecast_date")
        .values_list("forecast_date", flat=True)
        .first()
    )
    if nearest_date is None:
        return None, None, None, [], []

    # For weather display use the latest issued snapshot for this date
    latest_issued = (
        DailyForecast.objects
        .filter(forecast_date=nearest_date, horizon=DailyForecast.HORIZON_SHORT)
        .order_by("-issued_at", "-fetched_at")
        .values_list("issued_at", flat=True)
        .first()
    )

    rows = list(
        DailyForecast.objects
        .filter(forecast_date=nearest_date, horizon=DailyForecast.HORIZON_SHORT, issued_at=latest_issued)
        .select_related("point")
        .order_by("point__name")
    )
    # Fall back to fetched_at ordering if issued_at is null (pre-Phase-4 data)
    if not rows:
        rows = list(
            DailyForecast.objects
            .filter(forecast_date=nearest_date, horizon=DailyForecast.HORIZON_SHORT)
            .select_related("point")
            .order_by("point__name")
        )

    cz = [r for r in rows if r.point.country == "CZ"]
    sk = [r for r in rows if r.point.country == "SK"]

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


# ── Views ────────────────────────────────────────────────────────────────────

@login_required
def aktuality(request):
    autor = request.GET.get("autor", FILTER_ALL)
    notes_qs = Note.objects.select_related("author").all()
    notes = list(_apply_filter(notes_qs, autor))
    for note in notes:
        note.user_can_modify = _can_modify(request.user, note)

    forecast_date, cz_avg, sk_avg, cz_points, sk_points = _get_weather_panel()
    since_login = _get_since_login(request.user.last_login)
    filter_chips = _build_filter_chips(notes_qs)

    return render(request, "notes/aktuality.html", {
        "notes": notes,
        "user_can_pin": _can_pin(request.user),
        "form": NoteForm(),
        "forecast_date": forecast_date,
        "cz_avg": cz_avg,
        "sk_avg": sk_avg,
        "cz_points": cz_points,
        "sk_points": sk_points,
        "since_login": since_login,
        "filter_chips": filter_chips,
        "active_filter": autor,
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


# ── Revision tracker ────────────────────────────────────────────────────────

@login_required
def revision_tracker(request):
    bucket = request.GET.get("rozsah", "aktualni")
    context = {"bucket": bucket}

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
                            "precip_delta": precip_delta,
                        })

            context["revisions"] = revisions
            context["latest_batch"] = latest
            context["previous_batch"] = previous
        else:
            context["revisions"] = []
            context["not_enough_data"] = True

    return render(request, "notes/revision_tracker.html", context)
