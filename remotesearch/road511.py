"""Road events outside BC, from Quebec's open data and the 511 sites on one shared platform.

Most provinces and many states run their 511 site on the same software, and its map
pages read two JSON endpoints with no key: one lists the events, the other places them.
They're the sites' own and undocumented, so a site can change them without notice.
"""

import json
import re
from typing import Any, NamedTuple

from . import net
from .places import REGION_CODES, km_between
from .text import html_to_text, words

NEAR_KM = 30  # how far from a town an event still counts as near it
PAGE = 100  # the most rows the list endpoint gives at once
MOST_PAGES = 3
MAJOR = frozenset({"major", "high"})
QUEBEC = "https://ws.mapserver.transports.gouv.qc.ca/swtq"


class Site(NamedTuple):
    name: str
    host: str
    layers: tuple[str, ...]  # the ones holding closures and incidents


INCIDENTS = ("Incidents", "Closures")
# Georgia, Wisconsin and New England split closures by cause, and Ontario only maps
# the construction ones.
SPLIT = ("Incidents", "IncidentClosures", "ConstructionClosures")
NEW_ENGLAND = Site("New England 511", "newengland511.org", SPLIT)
# By country and the region name Open-Meteo's geocoder gives, from what each site served
# when they were checked in November 2026.
SITES: dict[tuple[str, str], Site] = {
    ("CA", "Alberta"): Site("511 Alberta", "511.alberta.ca", INCIDENTS),
    ("CA", "Ontario"): Site("Ontario 511", "511on.ca", ("Incidents", "ConstructionClosures")),
    ("CA", "Saskatchewan"): Site("Saskatchewan Highway Hotline", "hotline.gov.sk.ca", INCIDENTS),
    ("CA", "New Brunswick"): Site("New Brunswick 511", "511.gnb.ca", INCIDENTS),
    ("CA", "Nova Scotia"): Site("511 Nova Scotia", "511.novascotia.ca", INCIDENTS),
    ("CA", "Newfoundland and Labrador"): Site("511 NL", "511nl.ca", INCIDENTS),
    ("CA", "Yukon"): Site("511 Yukon", "511yukon.ca", INCIDENTS),
    ("US", "Alaska"): Site("Alaska 511", "511.alaska.gov", INCIDENTS),
    ("US", "Arizona"): Site("AZ511", "az511.gov", INCIDENTS),
    ("US", "Connecticut"): Site("CTroads", "ctroads.org", INCIDENTS),
    ("US", "Florida"): Site("FL511", "fl511.com", INCIDENTS),
    ("US", "Georgia"): Site("Georgia 511", "511ga.org", SPLIT),
    ("US", "Idaho"): Site("Idaho 511", "511.idaho.gov", INCIDENTS),
    ("US", "Louisiana"): Site("511 Louisiana", "511la.org", INCIDENTS),
    ("US", "Maine"): NEW_ENGLAND,
    ("US", "Nevada"): Site("NVRoads", "nvroads.com", INCIDENTS),
    ("US", "New Hampshire"): NEW_ENGLAND,
    ("US", "Pennsylvania"): Site("511PA", "511pa.com", INCIDENTS),
    ("US", "Utah"): Site("UDOT Traffic", "udottraffic.utah.gov", ("Incidents",)),
    ("US", "Vermont"): NEW_ENGLAND,
    ("US", "Wisconsin"): Site("511 Wisconsin", "511wi.gov", SPLIT),
}
BC = ("CA", "British Columbia")
QC = ("CA", "Quebec")
# Every region a highway can be named in, as in "hwy 2 alberta" or "I-75 GA".
REGIONS = {r.lower(): (c, r) for c, r in (*SITES, BC, QC)}


def region(text: str) -> tuple[str, str] | None:
    asked = " ".join(words(text))
    return REGIONS.get(REGION_CODES.get(asked, asked))


def numbers(road: str) -> list[str]:
    """The route numbers in a road name, like 25 in HWY-25 or 75 and 85 in I-75/I-85."""
    return re.findall(r"\d+[A-Z]?", road.upper())


def on(label: str) -> str:
    """ "Highway 25" as "on Highway 25", for saying there's nothing there."""
    return label.replace("Highway", "on Highway", 1)


def where(row: dict[str, Any]) -> str:
    return " ".join(str(row.get(k) or "") for k in ("city", "county", "locationDescription"))


def site_events(site: Site) -> list[dict[str, Any]]:
    """Every event in the site's layers, with ``at`` set to its (lat, lon) when it's mapped."""
    rows: list[dict[str, Any]] = []
    for layer in site.layers:
        icons = net.get_json(f"https://{site.host}/map/mapIcons/{layer}", site.name)
        placed = {
            str(i.get("itemId")): tuple(i["location"])
            for i in (icons or {}).get("item2") or []
            if len(i.get("location") or ()) == 2
        }
        for page in range(MOST_PAGES):
            query = {
                "columns": [{"name": "sortId", "s": True}],
                "order": [{"column": 0, "dir": "asc"}],
                "start": page * PAGE,
                "length": PAGE,
                "search": {"value": ""},
            }
            listed = net.get_json(
                f"https://{site.host}/List/GetData/{layer}",
                site.name,
                params={"query": json.dumps(query), "lang": "en"},
            )
            data = (listed or {}).get("data") or []
            rows += [r | {"at": placed.get(str(r.get("id")))} for r in data if isinstance(r, dict)]
            if len(data) < PAGE:
                break
    return rows


def site_answer(site: Site, label: str, rows: list[dict[str, Any]]) -> str:
    rows = sorted(
        rows,
        key=lambda r: (not r.get("isFullClosure"), str(r.get("severity")).lower() not in MAJOR),
    )
    notes = list(dict.fromkeys(html_to_text(str(r.get("description") or "")) for r in rows))
    notes = [n for n in notes if n]
    if not notes:
        return f"No {site.name} closures or incidents {on(label)} right now."
    count = f"{len(notes)} event{'s' if len(notes) != 1 else ''}"
    return f"{site.name}, {label}, {count}: " + " / ".join(notes)


def near_place(site: Site, hit: dict[str, Any]) -> str:
    lat, lon, name = hit["latitude"], hit["longitude"], str(hit.get("name") or "")
    rows = [
        r
        for r in site_events(site)
        # An event the site doesn't map can still name the town.
        if (r["at"] and km_between(lat, lon, *r["at"]) <= NEAR_KM)
        or (not r["at"] and name and name.lower() in where(r).lower())
    ]
    return site_answer(site, f"near {name}", rows)


def on_highway(site: Site, number: str) -> str:
    rows = [r for r in site_events(site) if number in numbers(str(r.get("roadwayName") or ""))]
    return site_answer(site, f"Highway {number}", rows)


def quebec(*, hit: dict[str, Any] | None = None, number: str = "") -> str:
    data = net.get_json(
        QUEBEC,
        "Québec 511",
        params={
            "service": "wfs",
            "version": "2.0.0",
            "request": "getfeature",
            "outputformat": "geojson",
            "typename": "ms:evenements",
            "srsName": "EPSG:4326",  # its own default is in metres
        },
    )
    notes = []
    for feature in (data or {}).get("features") or []:
        props = feature.get("properties") or {}
        if number:
            if str(props.get("numeroRoute") or "").upper() != number:
                continue
        elif hit:
            points = (feature.get("geometry") or {}).get("coordinates") or []
            if points and not isinstance(points[0], list):
                points = [points]  # a Point rather than a LineString
            close = any(
                km_between(hit["latitude"], hit["longitude"], p[1], p[0]) <= NEAR_KM
                for p in points
                if len(p) >= 2
            )
            town = str(props.get("municipalite") or "").lower()
            if not close and town != str(hit.get("name") or "").lower():
                continue
        place = html_to_text(str(props.get("localisation") or ""))
        what = str(props.get("entrave") or "").strip()
        town = str(props.get("municipalite") or "").strip()
        note = f"{what}: {place}" if what else place
        notes.append(f"{note} ({town})" if town else note)
    label = f"Highway {number}" if number else f"near {(hit or {}).get('name', '')}"
    if not notes:
        return f"No Québec 511 events {on(label)} right now."
    count = f"{len(notes)} event{'s' if len(notes) != 1 else ''}"
    return f"Québec 511, {label}, {count}: " + " / ".join(notes)
