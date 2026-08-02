"""Today's avalanche danger from Avalanche Canada's forecast for a spot."""

from .. import net
from ..places import find, place_label
from ..text import html_to_text
from . import command


@command(
    "avy",
    "avalanche",
    help="avy <place>: today's danger ratings and problems from Avalanche Canada's forecast "
    "for that spot, like 'avy Whistler'.",
)
def avalanche(place: str) -> str | None:
    hit = find(place)
    if not hit:
        return None
    data = net.get_json(
        "https://api.avalanche.ca/forecasts/en/products/point",
        "Avalanche Canada",
        params={"lat": hit["latitude"], "long": hit["longitude"]},
    )
    if not isinstance(data, dict) or not data.get("id"):
        return f"Avalanche Canada has no forecast covering {place_label(hit)}."
    report = data.get("report") or {}
    highlights = html_to_text(report.get("highlights") or "")
    ratings = (report.get("dangerRatings") or [{}])[0]
    levels = {
        label: (ratings.get("ratings") or {}).get(key, {}).get("rating") or {}
        for key, label in (("alp", "alpine"), ("tln", "treeline"), ("btl", "below treeline"))
    }
    if {r.get("value") for r in levels.values()} <= {"offseason", None}:
        return f"Avalanche Canada: {highlights}" if highlights else None
    # A rating reads "3 - Considerable", and the dash is a wasted character in an SMS.
    shown = [
        f"{label} {str(r.get('display', '?')).replace(' - ', ' ')}" for label, r in levels.items()
    ]
    # Out of season the title lists every region in BC, so only a short one is used.
    title = report.get("title") or ""
    head = f"Avalanche Canada, {title}" if 0 < len(title) <= 40 else "Avalanche Canada"
    day = (ratings.get("date") or {}).get("display")
    parts = [f"{head}{', ' + day if day else ''}: {', '.join(shown)}"]
    problems = [
        (p.get("type") or {}).get("display") for p in report.get("problems") or [] if p.get("type")
    ]
    if problems:
        parts.append(f"Problems: {', '.join(p for p in problems if p)}")
    if highlights:
        parts.append(highlights)
    return ". ".join(parts)
