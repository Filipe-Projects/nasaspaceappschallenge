"""Target resolution: presets, coordinate parsing, CDS Sesame and proper motion."""
from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.parse import quote

from astropy import units as u
from astropy.coordinates import SkyCoord

from . import config
from .http import fetch

# The five targets named in the challenge brief.  Coordinates are ICRS at
# epoch J2000.0 (Simbad convention) with Gaia/literature proper motions, so
# the viewer can propagate them to the SPHEREx observing epochs.
PRESETS: list[dict] = [
    {
        "id": "barnard",
        "name": "Barnard's Star",
        "aliases": ["barnard", "barnard's star", "barnards star", "gj 699", "v2500 oph"],
        "ra": 269.4520769586187, "dec": 4.69336496657667,
        "pmra": -801.551, "pmdec": 10362.394, "parallax": 546.976,
        "epoch": 2000.0, "fov": 8.0,
        "type": "Nearby M dwarf, fastest known proper motion (10.4 arcsec/yr)",
        "hint": "Moves about 0.8 SPHEREx pixels between consecutive surveys; use the difference mode.",
    },
    {
        "id": "epsindi-b",
        "name": "Epsilon Indi B",
        "aliases": ["epsilon indi b", "eps ind b", "eps indi b", "epsilon indi ba", "eps ind ba"],
        "ra": 331.04414658852124, "dec": -56.78284419511611,
        "pmra": 3981.977, "pmdec": -2466.832, "parallax": 270.658,
        "epoch": 2000.0, "fov": 15.0,
        "type": "Binary T-dwarf pair (Ba+Bb) orbiting the K star Epsilon Indi A, 7 arcmin away",
        "hint": "Both the brown dwarfs and the bright primary share a 4.7 arcsec/yr proper motion.",
    },
    {
        "id": "wise0855",
        "name": "WISE 0855-0714",
        "aliases": ["wise 0855", "wise0855", "wise j085510.83-071442.5", "wisea j085510.74-071442.5",
                    "wise 0855-0714", "w0855"],
        "ra": 133.81890825, "dec": -7.247048,
        "pmra": -8123.7, "pmdec": 673.2, "parallax": 439.0,
        "epoch": 2000.0, "fov": 8.0,
        "type": "Coldest known brown dwarf (Y dwarf), 8.1 arcsec/yr proper motion, very faint",
        "hint": "Only expected in the reddest bands (D5/D6, 3.8-5 um) after stacking; the challenge's "
                "08h55m10.8s -7d14m42.5s is the 2010 WISE position, 2.2 arcmin east of where it is now.",
    },
    {
        "id": "rwaur",
        "name": "RW Aurigae",
        "aliases": ["rw aur", "rw aurigae", "rw aurigae a"],
        "ra": 76.95652603458, "dec": 30.40143817389,
        "pmra": 7.23, "pmdec": -20.33, "parallax": 15.42,
        "epoch": 2000.0, "fov": 8.0,
        "type": "Classical T Tauri binary with deep dimming events",
        "hint": "Static on the sky; compare the per-epoch spectra and the light curve instead.",
    },
    {
        "id": "rdra",
        "name": "R Draconis",
        "aliases": ["r dra", "r draconis"],
        "ra": 248.16761083295, "dec": 66.75497941722,
        "pmra": -20.643, "pmdec": 6.149, "parallax": 1.251,
        "epoch": 2000.0, "fov": 8.0,
        "type": "Mira variable, period 245 days, several magnitudes of variation",
        "hint": "Near the north ecliptic pole, so SPHEREx visits it far more often than the sky average.",
    },
]


def current_decimal_year() -> float:
    now = datetime.now(timezone.utc)
    start = datetime(now.year, 1, 1, tzinfo=timezone.utc)
    end = datetime(now.year + 1, 1, 1, tzinfo=timezone.utc)
    return now.year + (now - start).total_seconds() / (end - start).total_seconds()


def viewing_epoch() -> float:
    """Epoch used to centre the field on a moving target: the start of the current quarter.

    The centre (and therefore every cache key) then stays put for three months
    and changes on a known date, the first day of a quarter; even Barnard's
    Star moves less than half a pixel in that time.
    """
    return math.floor(current_decimal_year() * 4.0) / 4.0


def propagate(ra: float, dec: float, pmra: float | None, pmdec: float | None,
              epoch_from: float, epoch_to: float) -> tuple[float, float]:
    """Linear proper-motion propagation (pmra includes the cos(dec) factor, mas/yr)."""
    if not pmra and not pmdec:
        return float(ra), float(dec)
    dt = float(epoch_to) - float(epoch_from)
    cosd = max(math.cos(math.radians(dec)), 1e-6)
    ra_new = ra + (pmra or 0.0) / 3.6e6 * dt / cosd
    dec_new = dec + (pmdec or 0.0) / 3.6e6 * dt
    return float(ra_new % 360.0), float(max(-90.0, min(90.0, dec_new)))


def find_preset(text: str) -> dict | None:
    key = re.sub(r"\s+", " ", text.strip().lower())
    for preset in PRESETS:
        if key == preset["id"] or key == preset["name"].lower() or key in preset["aliases"]:
            return preset
    return None


_FLOAT_PAIR = re.compile(r"^\s*([+-]?\d+(?:\.\d+)?)\s*[,\s]\s*([+-]?\d+(?:\.\d+)?)\s*$")
# Words people paste along with the numbers: "Right Ascension 08h55m10.831s, declination -7d14m42.53s".
_COORD_LABELS = re.compile(r"\b(right\s+ascension|declination|ra|dec|j2000|icrs)\b\.?\s*[:=]?", re.IGNORECASE)


def parse_coordinates(text: str) -> tuple[float, float] | None:
    """Accept '133.8 -7.2', '08h55m10.8s -7d14m42.5s', '08:55:10.8 -07:14:42.5'."""
    text = text.strip().replace("，", ",").replace("−", "-").replace("°", "d")
    text = _COORD_LABELS.sub(" ", text).replace(";", " ").strip(" ,")
    match = _FLOAT_PAIR.match(re.sub(r"(?<=\d)d(?=[\s,]|$)", "", text))      # "133.795d -7.245d": two angles in degrees
    if match:
        ra, dec = float(match.group(1)), float(match.group(2))
        if 0 <= ra < 360 and -90 <= dec <= 90:
            return ra, dec
        return None
    if not re.search(r"\d", text):
        return None
    cleaned = text.replace(",", " ")
    try:
        coord = SkyCoord(cleaned, unit=(u.hourangle, u.deg), frame="icrs")
        return float(coord.ra.deg), float(coord.dec.deg)
    except Exception:
        pass
    try:
        coord = SkyCoord(cleaned, unit=(u.deg, u.deg), frame="icrs")
        return float(coord.ra.deg), float(coord.dec.deg)
    except Exception:
        return None


_COMPASS = ["north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west"]


def passing_preset(ra: float, dec: float, epoch_now: float) -> dict | None:
    """A fast test target that used to be at (ra, dec) and has since moved on.

    Coordinates copied from a catalogue are tied to its epoch: the position the
    challenge quotes for WISE 0855 is where WISE saw it in 2010.  When typed
    coordinates fall on the past track of one of the test targets, the viewer
    can say so and offer to follow the object to where it is now.
    """
    cosd = max(math.cos(math.radians(dec)), 1e-6)
    for preset in PRESETS:
        vx, vy = preset["pmra"] / 1000.0, preset["pmdec"] / 1000.0          # arcsec/yr, east and north
        speed2 = vx * vx + vy * vy
        if speed2 < 0.3 ** 2:
            continue
        dx = ((preset["ra"] - ra + 180.0) % 360.0 - 180.0) * cosd * 3600.0
        dy = (preset["dec"] - dec) * 3600.0
        t_end = epoch_now - preset["epoch"]
        t = min(max(-(dx * vx + dy * vy) / speed2, -15.0), t_end)
        if math.hypot(dx + vx * t, dy + vy * t) > 20.0:
            continue
        moved = math.hypot(dx + vx * t_end, dy + vy * t_end)
        if moved < 30.0:
            continue                                   # still where the coordinates say
        angle = math.degrees(math.atan2(vx, vy)) % 360.0
        return {"id": preset["id"], "name": preset["name"], "year": int(round(preset["epoch"] + t)),
                "moved_arcsec": round(moved, 1), "direction": _COMPASS[int((angle + 22.5) // 45) % 8],
                "fov": preset["fov"]}
    return None


def _text(root: ET.Element, path: str) -> str | None:
    node = root.find(path)
    return node.text.strip() if node is not None and node.text else None


def _parse_sesame(text: str, name: str) -> dict | None:
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return None
    ra, dec = _text(root, ".//jradeg"), _text(root, ".//jdedeg")
    if ra is None or dec is None:
        return None

    def num(path):
        value = _text(root, path)
        try:
            return float(value) if value is not None else None
        except ValueError:
            return None

    return {
        "name": _text(root, ".//oname") or name,
        "type": _text(root, ".//otype"),
        "ra": float(ra),
        "dec": float(dec),
        "pmra": num(".//pmRA"),
        "pmdec": num(".//pmDE"),
        "parallax": num(".//plx/v"),
        "epoch": 2000.0,
        "source": "CDS Sesame/Simbad",
    }


def sesame(name: str) -> dict | None:
    """Resolve a name with the CDS Sesame service (Simbad, NED, VizieR).

    Tries the Strasbourg server and its mirrors in turn.  Returns None when
    the name is unknown; raises when no server could be reached.
    """
    last_error: Exception | None = None
    answered = False
    for base in config.SESAME_URLS:
        url = base + "?" + quote(name)
        try:
            resp = fetch("GET", url, timeout=9, what="Sesame", retries=0, allow_redirects=True)
            if resp.status_code != 200:
                last_error = RuntimeError(f"HTTP {resp.status_code} from {base}")
                continue
            text = resp.text
        except Exception as exc:  # noqa: BLE001 - try the next mirror
            last_error = exc
            continue
        answered = True
        found = _parse_sesame(text, name)
        if found is not None:
            return found
        if "<Resolver" in text or "<Target" in text:
            return None                      # a real answer: the name is unknown
    if not answered and last_error is not None:
        raise last_error
    return None


def resolve(query: str, epoch_to: float | None = None) -> dict:
    """Resolve free text (preset, coordinates or object name) to a target dict."""
    query = query.strip()
    if not query:
        raise ValueError("empty query")
    target: dict | None = None
    preset = find_preset(query)
    if preset:
        target = {k: v for k, v in preset.items() if k != "aliases"}
        target["source"] = "preset"
    if target is None:
        coords = parse_coordinates(query)
        if coords:
            target = {"name": query, "ra": coords[0], "dec": coords[1], "pmra": None,
                      "pmdec": None, "parallax": None, "epoch": 2000.0, "source": "coordinates"}
    if target is None:
        target = sesame(query)
    if target is None:
        raise LookupError(f"Could not resolve {query!r}")
    epoch_to = viewing_epoch() if epoch_to is None else float(epoch_to)
    ra_now, dec_now = propagate(target["ra"], target["dec"], target.get("pmra"),
                                target.get("pmdec"), target.get("epoch", 2000.0), epoch_to)
    target["ra_now"] = ra_now
    target["dec_now"] = dec_now
    target["epoch_now"] = epoch_to
    target["query"] = query
    if target.get("source") == "coordinates":
        target["passing"] = passing_preset(target["ra"], target["dec"], epoch_to)
    target["id"] = target.get("id") or re.sub(r"[^a-z0-9]+", "-", query.lower()).strip("-")
    return target
