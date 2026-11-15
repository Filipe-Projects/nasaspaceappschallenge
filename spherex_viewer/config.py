"""Runtime configuration (every value can be overridden by an env var)."""
from __future__ import annotations

import os
from pathlib import Path


def _env_list(name: str, default: str) -> list[str]:
    raw = os.environ.get(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


# IRSA services -------------------------------------------------------------
# The viewer asks IRSA which SPHEREx collections exist (see releases.py), so a
# new release needs no change at all.  DEFAULT_COLLECTIONS is only the fallback
# for when that question cannot be asked; SPHEREX_COLLECTIONS pins the list.
DEFAULT_COLLECTIONS: list[str] = ["spherex_dr1", "spherex_dr1_deep", "spherex_qr3", "spherex_qr3_deep"]
COLLECTIONS_OVERRIDE: list[str] = _env_list("SPHEREX_COLLECTIONS", "")
SIA_URL = os.environ.get("IRSA_SIA_URL", "https://irsa.ipac.caltech.edu/SIA")
IRSA_TAP_URL = os.environ.get("IRSA_TAP_URL", "https://irsa.ipac.caltech.edu/TAP/sync")
# Sesame name resolver and mirrors, tried in order.
SESAME_URLS: list[str] = _env_list(
    "SESAME_URLS",
    "https://cds.unistra.fr/cgi-bin/nph-sesame/-oxp/SNV,"
    "https://vizier.cfa.harvard.edu/viz-bin/nph-sesame/-oxp/SNV,"
    "https://vizier.idia.ac.za/viz-bin/nph-sesame/-oxp/SNV",
)
# Gaia DR3 catalogue overlay (ESA Gaia archive).
GAIA_TAP_URL = os.environ.get(
    "GAIA_TAP_URL", "https://gea.esac.esa.int/tap-server/tap/sync"
)

# HTTP behaviour --------------------------------------------------------------
# Range requests are small (tens of kB), so a slow answer means a stuck one.
HTTP_TIMEOUT = float(os.environ.get("SPHEREX_HTTP_TIMEOUT", "45"))
HTTP_RETRIES = int(os.environ.get("SPHEREX_HTTP_RETRIES", "3"))
# The image search is slower (seconds to tens of seconds per collection); one
# that does not answer is reported as a gap instead of holding the request.
SIA_TIMEOUT = float(os.environ.get("SPHEREX_SIA_TIMEOUT", "75"))
# IRSA's image server honours multi-range requests with at most 100 ranges
# (measured: 100 -> 206 multipart, 101 -> 200 with the whole 70 MB file).
MAX_RANGES_PER_REQUEST = int(os.environ.get("SPHEREX_MAX_RANGES", "100"))
MAX_WORKERS = int(os.environ.get("SPHEREX_MAX_WORKERS", "16"))
USER_AGENT = "beyondtheblue/0.2"

# Cache ------------------------------------------------------------------------
CACHE_DIR = Path(os.environ.get("SPHEREX_CACHE_DIR", "./cache")).resolve()
CACHE_MAX_GB = float(os.environ.get("SPHEREX_CACHE_MAX_GB", "4"))
# New exposures are published weekly, so a search stays valid for a day.
SIA_CACHE_TTL = float(os.environ.get("SPHEREX_SIA_CACHE_TTL", str(24 * 3600)))
CATALOG_CACHE_TTL = float(os.environ.get("SPHEREX_CATALOG_CACHE_TTL", str(30 * 86400)))

# Imaging ----------------------------------------------------------------------
PIXEL_SCALE_ARCSEC = 6.15          # native SPHEREx pixel
DETECTOR_SIZE = 2040               # pixels per side
MIN_FOV_ARCMIN = 2.0
MAX_FOV_ARCMIN = float(os.environ.get("SPHEREX_MAX_FOV_ARCMIN", "40"))
DEFAULT_FOV_ARCMIN = 8.0
MAX_OUTPUT_PIXELS = 480            # longest side of a reprojected frame
# Extra pixels fetched around the needed window so that small follow-up
# requests (re-centring, a click near the edge) are served from the cache.
CUTOUT_MARGIN_PX = 3
# Extra margin when the window is predicted from the SIA footprint before
# the real WCS has been read (the prediction is good to a fraction of a pixel).
PREDICT_MARGIN_PX = 3
# Individual-exposure sequences are capped (frames x pixels) to keep the
# JSON payload reasonable.
MAX_EXPOSURE_MODE_PIXELS = int(os.environ.get("SPHEREX_MAX_EXPOSURE_PIXELS", str(4_000_000)))

# Time grouping ----------------------------------------------------------------
# A gap longer than EPOCH_GAP_DAYS starts a new epoch (one per all-sky survey
# pass).  Where coverage is continuous (near the ecliptic poles) a cluster
# longer than EPOCH_MAX_SPAN_DAYS is cut into calendar bins of EPOCH_BIN_DAYS.
EPOCH_GAP_DAYS = float(os.environ.get("SPHEREX_EPOCH_GAP_DAYS", "45"))
EPOCH_MAX_SPAN_DAYS = float(os.environ.get("SPHEREX_EPOCH_MAX_SPAN_DAYS", "60"))
EPOCH_BIN_DAYS = float(os.environ.get("SPHEREX_EPOCH_BIN_DAYS", "30"))
# Most exposures read for one request; deep-field positions have thousands,
# which are then sampled evenly in time per detector.
MAX_EXPOSURES = int(os.environ.get("SPHEREX_MAX_EXPOSURES", "800"))
# Start of the first all-sky survey; origin of the calendar bins and of the
# "survey N" label.
SURVEY_START_MJD = 60796.0         # 2025-05-01
SURVEY_LENGTH_DAYS = 182.625

# Flags --------------------------------------------------------------------------
# Reading the FLAGS extension costs one more request per file (two for the
# tile-compressed QR3 files, ~4 kB per image row), so it is opt-in per request;
# set SPHEREX_DEFAULT_FLAGS=1 on a server with a fast link to IRSA.
DEFAULT_FLAGS = os.environ.get("SPHEREX_DEFAULT_FLAGS", "0") == "1"
# Flag names that make a pixel unusable for stacking when flags are read.
# Saturation-type flags are deliberately NOT masked: bright targets such as
# Barnard's Star would otherwise vanish from the stacks.
DEFAULT_MASK_NAMES: list[str] = _env_list(
    "SPHEREX_MASK_FLAGS",
    "NONFUNC,MISSING_DATA,REFERENCE,PHANTOM,HOT,COLD,SUR_ERROR,OUTLIER,"
    "SNOWBALL,HALO,STREAK,SATELLITE_HALO",
)

# Startup warm-up: pre-compute the five test targets in the background.
WARMUP = os.environ.get("SPHEREX_WARMUP", "0") == "1"
