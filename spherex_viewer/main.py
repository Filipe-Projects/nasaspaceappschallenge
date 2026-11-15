"""FastAPI application: JSON API + static frontend."""
from __future__ import annotations

import base64
import gzip
import io
import logging
import mimetypes
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

import numpy as np
from astropy.io import fits
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__, config, pipeline
from .cache import default_cache
from .catalogs import CATALOGS
from .releases import active_collections
from .cutouts import DEFAULT_FLAG_DEFS
from .jobs import JobManager
from .resolve import PRESETS, resolve, viewing_epoch

log = logging.getLogger("spherex_viewer")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
# Some systems' type tables lack these; with the right type the (already
# compressed) fonts and images are not gzipped again on every request.
mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("image/webp", ".webp")


# --------------------------------------------------------------------------
# Background chores
# --------------------------------------------------------------------------
WARMUP_DELAY_SECONDS = 15.0


def _warmup() -> None:
    """Read the test targets from IRSA in the background so visitors get the live result.

    Until a target is read, its saved copy is shown at once.  The warm-up
    waits a moment after start-up (the visit that woke the server comes
    first), and a target a visitor has already set reading is not read twice.
    """
    time.sleep(WARMUP_DELAY_SECONDS)
    for preset in PRESETS:
        try:
            target = resolve(preset["id"])
            params = pipeline.normalise_frame_params({"ra": target["ra_now"], "dec": target["dec_now"],
                                                      "fov": preset["fov"]})
            pipeline.warm(params)
            log.info("warm-up done: %s", preset["name"])
        except Exception as exc:  # noqa: BLE001
            log.warning("warm-up failed for %s: %s", preset["name"], exc)


def _janitor() -> None:
    while True:
        try:
            removed = default_cache().prune()
            if removed:
                log.info("cache pruned: %d files removed", removed)
        except Exception as exc:  # noqa: BLE001
            log.warning("cache prune failed: %s", exc)
        time.sleep(1800)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    active_collections()                      # starts the background check of IRSA's release list
    threading.Thread(target=_janitor, name="janitor", daemon=True).start()
    if config.WARMUP:
        threading.Thread(target=_warmup, name="warmup", daemon=True).start()
    yield


app = FastAPI(
    title="BeyondtheBlue",
    version=__version__,
    description="Play back every SPHEREx visit to any patch of sky and see what moved or changed.",
    lifespan=lifespan,
)
# Level 6 gives the same size as the default 9 on this data in half the CPU time.
app.add_middleware(GZipMiddleware, minimum_size=2048, compresslevel=6)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST"], allow_headers=["*"])

# Jobs wait on the network, not on the CPU; a visitor who changes target leaves the old one running.
jobs = JobManager(max_workers=6)


# --------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------
class FramesRequest(BaseModel):
    ra: float = Field(..., ge=0.0, lt=360.0)
    dec: float = Field(..., ge=-90.0, le=90.0)
    fov: float = Field(config.DEFAULT_FOV_ARCMIN, gt=0.0, le=config.MAX_FOV_ARCMIN)
    group: str = Field("epoch", pattern="^(epoch|exposure)$")
    detector: str = Field("ALL", pattern="^(?i:all|d[1-6])$")
    flags: bool | None = None
    max_exposures: int | None = Field(None, ge=12, le=4 * config.MAX_EXPOSURES)


class FieldModel(BaseModel):
    ra: float = Field(..., ge=0.0, lt=360.0)
    dec: float = Field(..., ge=-90.0, le=90.0)
    fov: float = Field(config.DEFAULT_FOV_ARCMIN, gt=0.0, le=config.MAX_FOV_ARCMIN)


class SpectrumRequest(BaseModel):
    ra: float = Field(..., ge=0.0, lt=360.0)
    dec: float = Field(..., ge=-90.0, le=90.0)
    pmra: float | None = None
    pmdec: float | None = None
    epoch: float | None = 2000.0
    r_ap: float = Field(2.0, ge=1.0, le=4.0)
    field: FieldModel | None = None
    max_exposures: int | None = Field(None, ge=12, le=4 * config.MAX_EXPOSURES)


# --------------------------------------------------------------------------
# Meta endpoints
# --------------------------------------------------------------------------
@app.api_route("/api/health", methods=["GET", "HEAD"])
async def health() -> dict:
    # Answered on the event loop, never queued behind slow archive requests in the
    # thread pool: a health check that times out makes the host restart the server.
    # HEAD too: uptime monitors (UptimeRobot) check with HEAD and count a 405 as down.
    return {"status": "ok", "version": __version__, "collections": active_collections()}


@app.get("/api/config")
def api_config() -> dict:
    return {
        "version": __version__,
        "collections": active_collections(),
        "default_flags": config.DEFAULT_FLAGS,
        "max_fov_arcmin": config.MAX_FOV_ARCMIN,
        "max_exposures": config.MAX_EXPOSURES,
        "pixel_scale_arcsec": config.PIXEL_SCALE_ARCSEC,
        "viewing_epoch": viewing_epoch(),
        "flag_defs": DEFAULT_FLAG_DEFS,
        "mask_names": config.DEFAULT_MASK_NAMES,
        "catalogs": {name: {"label": cat["label"], "epoch": cat["epoch"]} for name, cat in CATALOGS.items()},
    }


@app.get("/api/presets")
def presets() -> list[dict]:
    out = []
    for preset in PRESETS:
        resolved = resolve(preset["id"])
        item = {k: v for k, v in resolved.items() if k != "aliases"}
        out.append(item)
    return out


@app.get("/api/resolve")
def api_resolve(q: str = Query(..., min_length=1, max_length=120), epoch: float | None = None) -> dict:
    try:
        return resolve(q, epoch_to=epoch)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Name resolver unavailable: {exc}") from exc


@app.get("/api/exposures")
def api_exposures(ra: float = Query(..., ge=0.0, lt=360.0), dec: float = Query(..., ge=-90.0, le=90.0),
                  fov: float = Query(config.DEFAULT_FOV_ARCMIN, gt=0.0, le=config.MAX_FOV_ARCMIN)) -> dict:
    try:
        exposures, epochs, n_selected = pipeline.search(ra, dec, fov)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"IRSA search failed: {exc}") from exc
    return {"ra": ra, "dec": dec, "fov": fov, "collections": active_collections(),
            "n_exposures": len(exposures), "n_selected": n_selected,
            "epochs": [ep.to_dict() for ep in epochs]}


@app.get("/api/catalog/{name}")
def api_catalog(name: str, ra: float = Query(..., ge=0.0, lt=360.0), dec: float = Query(..., ge=-90.0, le=90.0),
                radius: float = Query(0.1, gt=0.0, le=0.6)) -> dict:
    catalog = CATALOGS.get(name.lower())
    if catalog is None:
        raise HTTPException(status_code=404, detail=f"unknown catalogue {name!r}")
    try:
        found = catalog["fn"](round(ra, 5), round(dec, 5), round(radius, 4))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"{catalog['label']} unavailable: {exc}") from exc
    return {"name": name.lower(), "label": catalog["label"], "epoch": catalog["epoch"],
            "n": len(found), "sources": found}



# --------------------------------------------------------------------------
# Jobs
# --------------------------------------------------------------------------
@app.post("/api/jobs/frames")
def api_frames(req: FramesRequest) -> dict:
    params = pipeline.normalise_frame_params(req.model_dump())
    return jobs.submit("frames", params, pipeline.build_frames).status()


@app.post("/api/jobs/spectrum")
def api_spectrum(req: SpectrumRequest) -> dict:
    params = pipeline.normalise_spectrum_params(req.model_dump())
    return jobs.submit("spectrum", params, pipeline.build_spectrum).status()


@app.get("/api/jobs/{job_id}")
def api_job(job_id: str) -> dict:
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown job")
    return job.status()


@app.get("/api/jobs/{job_id}/result")
def api_job_result(job_id: str, request: Request):
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown job")
    if job.state == "error":
        raise HTTPException(status_code=500, detail=job.error)
    if job.state != "done":
        return JSONResponse(status_code=202, content=job.status())
    # The body was serialised and compressed once when the job finished.
    if "gzip" in request.headers.get("accept-encoding", "").lower():
        return Response(content=job.body, media_type="application/json",
                        headers={"Content-Encoding": "gzip", "Vary": "Accept-Encoding"})
    return Response(content=gzip.decompress(job.body), media_type="application/json")


@app.get("/api/jobs/{job_id}/fits")
def api_job_fits(job_id: str):
    """The frames of a finished job as a multi-extension FITS file on the common WCS."""
    job = jobs.get(job_id)
    if job is None or job.kind != "frames":
        raise HTTPException(status_code=404, detail="unknown frames job")
    if job.state != "done":
        raise HTTPException(status_code=409, detail="job not finished")
    result = job.result
    hdul = fits.HDUList([fits.PrimaryHDU()])
    primary = hdul[0].header
    primary["ORIGIN"] = "BeyondtheBlue"
    primary["RA_TARG"] = result["target"]["ra"]
    primary["DEC_TARG"] = result["target"]["dec"]
    primary["FOV_AMIN"] = result["target"]["fov_arcmin"]
    primary["NEXPUSED"] = result["n_used"]
    wcs = result["target"]["wcs"]
    for frame in result["frames"]:
        npix = frame["shape"][0]
        data = np.frombuffer(base64.b64decode(frame["data"]), dtype="<f4").reshape(npix, npix)
        name = frame["id"].replace("/", "_")[:32]
        hdu = fits.ImageHDU(data=data.astype(np.float32), name=name)
        hdr = hdu.header
        hdr["CTYPE1"], hdr["CTYPE2"] = wcs["ctype"]
        hdr["CRVAL1"], hdr["CRVAL2"] = wcs["crval"]
        hdr["CRPIX1"], hdr["CRPIX2"] = wcs["crpix"]
        hdr["CDELT1"], hdr["CDELT2"] = wcs["cdelt"]
        hdr["CUNIT1"] = hdr["CUNIT2"] = "deg"
        hdr["RADESYS"] = "ICRS"
        hdr["BUNIT"] = ("MJy/sr", "sky background of each exposure subtracted")
        hdr["EPOCH_IX"] = frame.get("epoch") or 0
        hdr["BAND"] = frame["band"]
        hdr["NEXP"] = frame["n_exposures"]
        hdr["MJD-AVG"] = frame["mjd"]
        hdr["DATE-AVG"] = frame["date"]
        hdr["WAVEMIN"] = (frame["wl_min"], "[um]")
        hdr["WAVEMAX"] = (frame["wl_max"], "[um]")
        hdul.append(hdu)
    buf = io.BytesIO()
    hdul.writeto(buf)
    name = f"spherex_{result['target']['ra']:.4f}_{result['target']['dec']:+.4f}.fits"
    return Response(content=buf.getvalue(), media_type="application/fits",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


# --------------------------------------------------------------------------
# Frontend
# --------------------------------------------------------------------------
def _asset_version(name: str) -> str:
    try:
        return str(int((FRONTEND_DIR / name).stat().st_mtime))
    except OSError:
        return "0"


@app.api_route("/", methods=["GET", "HEAD"])
def index() -> HTMLResponse:
    # The page links its stylesheet and script with the files' modification time, so a
    # browser never pairs a fresh page with a cached copy of an older app.js or styles.css.
    html = (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
    for name in ("styles.css", "app.js", "tour.js"):
        html = html.replace(f'"/static/{name}"', f'"/static/{name}?v={_asset_version(name)}"')
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


class CachedStaticFiles(StaticFiles):
    """Static files a browser may keep, so a second visit loads nothing but the page.

    Files whose address changes with their content (``?v=`` links, the
    versioned Plotly bundle) and the fonts, which never change, are kept for
    a year; anything else is kept for an hour and then checked again.
    """

    LONG = "public, max-age=31536000, immutable"
    SHORT = "public, max-age=3600"

    def file_response(self, full_path, stat_result, scope, status_code: int = 200) -> Response:
        response = super().file_response(full_path, stat_result, scope, status_code)
        path = str(scope.get("path", ""))
        versioned = b"v=" in scope.get("query_string", b"") or "/vendor/" in path or "/fonts/" in path
        response.headers["Cache-Control"] = self.LONG if versioned else self.SHORT
        return response


if FRONTEND_DIR.exists():
    app.mount("/static", CachedStaticFiles(directory=str(FRONTEND_DIR)), name="static")
