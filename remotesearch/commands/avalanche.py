"""Today's avalanche danger from Avalanche Canada or, in the US, avalanche.org."""

from typing import Any

from .. import net
from ..places import find, place_label
from ..text import html_to_text
from . import command

AVALANCHE_ORG = "https://api.avalanche.org/v2/public"
# The North American scale, which both countries' forecasts use.
LEVELS = {1: "1 Low", 2: "2 Moderate", 3: "3 Considerable", 4: "4 High", 5: "5 Extreme"}
US_BANDS = (("upper", "above treeline"), ("middle", "near treeline"), ("lower", "below treeline"))


def canada(hit: dict[str, Any]) -> str | None:
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


def inside(lon: float, lat: float, geometry: dict[str, Any]) -> bool:
    """Whether a point is in a GeoJSON Polygon or MultiPolygon, holes and all."""
    shapes = geometry.get("coordinates") or []
    if geometry.get("type") == "Polygon":
        shapes = [shapes]
    elif geometry.get("type") != "MultiPolygon":
        return False
    for rings in shapes:
        # Even-odd over every ring, so a point in a hole counts as outside.
        crossings = 0
        for ring in rings:
            for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1], strict=True):
                if (y1 > lat) != (y2 > lat) and lon < x1 + (lat - y1) * (x2 - x1) / (y2 - y1):
                    crossings += 1
        if crossings % 2:
            return True
    return False


def united_states(hit: dict[str, Any]) -> str:
    layer = net.get_json(f"{AVALANCHE_ORG}/products/map-layer", "avalanche.org")
    lat, lon = hit["latitude"], hit["longitude"]
    zone = next(
        (
            f
            for f in (layer or {}).get("features") or []
            if inside(lon, lat, f.get("geometry") or {})
        ),
        None,
    )
    if not zone:
        return f"avalanche.org has no forecast zone covering {place_label(hit)}."
    props = zone.get("properties") or {}
    center = props.get("center") or "avalanche.org"
    name = props.get("name") or ""
    if props.get("off_season"):
        return f"{center}: {name} is out of season, so there's no forecast."
    product = net.get_json(
        f"{AVALANCHE_ORG}/product",
        "avalanche.org",
        params={"type": "forecast", "center_id": props.get("center_id"), "zone_id": zone.get("id")},
    )
    product = product if isinstance(product, dict) else {}
    # Colorado's map calls every zone "CAIC zone", and the forecast has the real name.
    named = [
        z.get("name") for z in product.get("forecast_zone") or [] if z.get("id") == zone.get("id")
    ]
    if named and named[0]:
        name = named[0]
    days: list[dict[str, Any]] = product.get("danger") or []
    today = next((d for d in days if d.get("valid_day") == "current"), {})
    rated = [(label, LEVELS.get(int(today.get(band) or 0))) for band, label in US_BANDS]
    if all(level for _, level in rated):
        danger = ", ".join(f"{label} {level}" for label, level in rated)
    elif props.get("danger_level") in LEVELS:
        danger = LEVELS[props["danger_level"]]
    else:
        danger = "no rating today"
    parts = [f"{center}, {name}: {danger}"]
    problems = [p.get("name") for p in product.get("forecast_avalanche_problems") or []]
    if any(problems):
        parts.append(f"Problems: {', '.join(p for p in problems if p)}")
    bottom = html_to_text(product.get("bottom_line") or "")
    if bottom:
        parts.append(bottom)
    return ". ".join(parts)


@command(
    "avy",
    "avalanche",
    help="avy <place>: today's danger ratings and problems from Avalanche Canada, or "
    "avalanche.org in the US, like 'avy Whistler'.",
)
def avalanche(place: str) -> str | None:
    hit = find(place)
    if not hit:
        return None
    country = hit.get("country_code")
    if country == "US":
        return united_states(hit)
    if country == "CA":
        return canada(hit)
    return (
        f"There's no avalanche forecast here for {place_label(hit)}, as Avalanche Canada "
        "and avalanche.org only cover Canada and the US."
    )
