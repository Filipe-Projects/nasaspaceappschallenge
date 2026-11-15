"""External catalogues overlaid on the SPHEREx frames.

* Gaia DR3 (ESA Gaia archive): optical astrometry with proper motions.
* CatWISE2020 (IRSA): the 3.4/4.6 um WISE catalogue with proper motions, the
  natural mid-infrared counterpart of SPHEREx and the catalogue in which the
  coldest brown dwarfs (invisible to Gaia) live.

Rows share one shape so the browser treats them alike: position at *epoch*,
proper motion in mas/yr (None when unknown or not significant), a magnitude.
"""
from __future__ import annotations

import csv
import io

from . import config
from .cache import default_cache
from .http import fetch

GAIA_EPOCH = 2016.0
CATWISE_EPOCH = 2015.405           # MJD 57170, the reference epoch of ra_pm/dec_pm
GAIA_COLUMNS = "source_id, ra, dec, pmra, pmdec, parallax, phot_g_mean_mag, bp_rp"


def _num(value):
    try:
        if value is None or value == "" or str(value).lower() in ("null", "nan", "--"):
            return None
        number = float(value)
        return number if number == number else None
    except (TypeError, ValueError):
        return None


def _gaia_row(row: dict) -> dict:
    return {
        "id": f"Gaia DR3 {row.get('source_id')}",
        "ra": _num(row.get("ra")), "dec": _num(row.get("dec")),
        "pmra": _num(row.get("pmra")), "pmdec": _num(row.get("pmdec")),
        "parallax": _num(row.get("parallax")),
        "mag": _num(row.get("phot_g_mean_mag")), "mag_band": "G",
        "color": _num(row.get("bp_rp")),
        "epoch": GAIA_EPOCH,
    }


def _gaia_where(ra: float, dec: float, radius_deg: float, max_mag: float) -> str:
    return (f"WHERE CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', {ra:.6f}, {dec:.6f}, {radius_deg:.5f}))=1 "
            f"AND phot_g_mean_mag < {float(max_mag)} ORDER BY phot_g_mean_mag")


def gaia_cone(ra: float, dec: float, radius_deg: float, max_mag: float = 18.0, limit: int = 800) -> list[dict]:
    """Gaia DR3 sources in a cone, brightest first."""
    cache = default_cache()
    key = f"gaia|{ra:.5f}|{dec:.5f}|{radius_deg:.4f}|{max_mag}|{limit}"
    cached = cache.get_json("catalog", key, ttl=config.CATALOG_CACHE_TTL)
    if isinstance(cached, list):
        return cached
    query = f"SELECT TOP {int(limit)} {GAIA_COLUMNS} FROM gaiadr3.gaia_source {_gaia_where(ra, dec, radius_deg, max_mag)}"
    payload = {"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "json", "QUERY": query}
    resp = fetch("POST", config.GAIA_TAP_URL, data=payload, timeout=90, what="Gaia TAP", retries=2)
    resp.raise_for_status()
    doc = resp.json()
    names = [col["name"] for col in doc.get("metadata", [])]
    rows = [_gaia_row(dict(zip(names, values))) for values in doc.get("data", [])]
    cache.put_json("catalog", key, rows)
    return rows


def catwise_cone(ra: float, dec: float, radius_deg: float, max_w2: float = 16.5, limit: int = 800) -> list[dict]:
    """CatWISE2020 sources in a cone, brightest in W2 first."""
    cache = default_cache()
    key = f"catwise|{ra:.5f}|{dec:.5f}|{radius_deg:.4f}|{max_w2}|{limit}"
    cached = cache.get_json("catalog", key, ttl=config.CATALOG_CACHE_TTL)
    if isinstance(cached, list):
        return cached
    query = (
        f"SELECT TOP {int(limit)} source_name, ra_pm, dec_pm, pmra, pmdec, sigpmra, sigpmdec, "
        f"w1mpro_pm, w2mpro_pm FROM catwise_2020 "
        f"WHERE CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', {ra:.6f}, {dec:.6f}, {radius_deg:.5f}))=1 "
        f"AND w2mpro_pm < {float(max_w2)} ORDER BY w2mpro_pm"
    )
    resp = fetch("POST", config.IRSA_TAP_URL, data={"QUERY": query, "FORMAT": "CSV"}, timeout=120,
                 what="IRSA TAP", retries=2)
    resp.raise_for_status()
    text = resp.text
    rows = []
    for row in csv.DictReader(io.StringIO(text)):
        ra_s, dec_s = _num(row.get("ra_pm")), _num(row.get("dec_pm"))
        if ra_s is None or dec_s is None:
            continue
        pmra, pmdec = _num(row.get("pmra")), _num(row.get("pmdec"))          # arcsec/yr
        sra, sdec = _num(row.get("sigpmra")), _num(row.get("sigpmdec"))
        significant = bool(pmra is not None and pmdec is not None and sra and sdec
                           and (pmra / sra) ** 2 + (pmdec / sdec) ** 2 >= 25.0)
        w1, w2 = _num(row.get("w1mpro_pm")), _num(row.get("w2mpro_pm"))
        rows.append({
            "id": f"CWISE {row.get('source_name', '').strip()}",
            "ra": ra_s, "dec": dec_s,
            "pmra": pmra * 1000.0 if significant else None,
            "pmdec": pmdec * 1000.0 if significant else None,
            "parallax": None,
            "mag": w2, "mag_band": "W2",
            "color": (w1 - w2) if (w1 is not None and w2 is not None) else None,
            "epoch": CATWISE_EPOCH,
        })
    cache.put_json("catalog", key, rows)
    return rows


CATALOGS = {
    "gaia": {"label": "Gaia DR3", "fn": gaia_cone, "epoch": GAIA_EPOCH},
    "catwise": {"label": "CatWISE2020", "fn": catwise_cone, "epoch": CATWISE_EPOCH},
}
