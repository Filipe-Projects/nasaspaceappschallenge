/* BeyondtheBlue: browser application (no build step). */
(() => {
  'use strict';

  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));
  const D2R = Math.PI / 180, R2D = 180 / Math.PI;

  // Detector bands run from the shortest wavelength (blue) to the longest (red).
  const BAND_IDS = ['D1', 'D2', 'D3', 'D4', 'D5', 'D6'];
  const BAND_ORDER = ['ALL', ...BAND_IDS];
  const BAND_COLORS = { D1: '#2743b5', D2: '#3c78e2', D3: '#7aaef4', D4: '#f0957f', D5: '#dc4038', D6: '#96203a' };
  const BAND_SYMBOLS = { D1: 'circle', D2: 'square', D3: 'diamond', D4: 'triangle-up', D5: 'triangle-down', D6: 'cross' };
  const BAND_EDGES = [1.105, 1.64, 2.415, 3.83, 4.42];          // where one detector hands over to the next, µm
  const BAND_CENTRES = [0.93, 1.37, 2.03, 3.12, 4.12, 4.71];
  const BAND_RANGES = { RGB: '0.75–5.01', ALL: '0.75–5.01', D1: '0.75–1.11', D2: '1.10–1.65', D3: '1.63–2.43', D4: '2.40–3.85', D5: '3.81–4.43', D6: '4.41–5.01' };
  // Epochs: earlier visits in blue, the latest one in red.
  const EPOCH_BLUES = ['#8fb4f4', '#5b8fee', '#3463d3', '#1a40bf', '#14308f'];
  const EPOCH_LAST = '#d4292e';
  // Marks drawn over the image, for a dark sky and for the inverted (light) one.
  const OVERLAY = {
    dark: { target: '#ffffff', change: '#ffd23f', gaia: '#7cc4ff', catwise: '#ff9d8a', halo: 'rgba(0, 0, 0, 0.6)', hover: 'rgba(255, 255, 255, 0.55)' },
    light: { target: '#0a1c56', change: '#a52024', gaia: '#1a40bf', catwise: '#8a5200', halo: 'rgba(255, 255, 255, 0.8)', hover: 'rgba(10, 28, 86, 0.5)' },
  };
  const FIRST_FRAME = '#d4292e';
  const FONT = 'Poppins, "Century Gothic", "Segoe UI", system-ui, sans-serif';
  const INK = '#0a1c56', RULE = 'rgba(10, 28, 86, 0.16)';
  const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const BAD_FLAGS = new Set(['OVERFLOW', 'NONLINEAR', 'NONFUNC', 'MISSING_DATA', 'HOT', 'COLD', 'PERSIST', 'SUR_ERROR']);
  // Simbad object types worth spelling out under a resolved name.
  const OTYPES = { '*': 'Star', '**': 'Double star', 'PM*': 'High proper-motion star', 'V*': 'Variable star', 'Mi*': 'Mira variable',
    'LP*': 'Long-period variable', 'TT*': 'T Tauri star', 'Or*': 'Orion variable', 'Y*O': 'Young stellar object', 'BD*': 'Brown dwarf',
    'LM*': 'Low-mass star', 'WD*': 'White dwarf', 'RR*': 'RR Lyrae variable', 'Ce*': 'Cepheid variable', 'Er*': 'Eruptive variable',
    'SB*': 'Spectroscopic binary', 'EB*': 'Eclipsing binary', 'C*': 'Carbon star', 'RG*': 'Red giant', 'AB*': 'Asymptotic giant branch star',
    'Em*': 'Emission-line star', G: 'Galaxy', QSO: 'Quasar', AGN: 'Active galactic nucleus', Sy1: 'Seyfert 1 galaxy', Sy2: 'Seyfert 2 galaxy',
    PN: 'Planetary nebula', GlC: 'Globular cluster', OpC: 'Open cluster', HII: 'H II region', SNR: 'Supernova remnant' };
  const GLYPH = {
    move: '<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="6.4" cy="9.6" r="4" fill="none" stroke="#ffd23f" stroke-width="1.7"/><path d="M9.8 6.2L14.2 1.8M10.6 1.6h3.8v3.8" fill="none" stroke="#ffd23f" stroke-width="1.7"/></svg>',
    vary: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 1.8L14.2 8 8 14.2 1.8 8z" fill="none" stroke="#ffd23f" stroke-width="1.7"/></svg>',
  };
  const SYMBOL_PATHS = {
    circle: '<circle cx="5" cy="5" r="4.2"/>', square: '<rect x="1.2" y="1.2" width="7.6" height="7.6"/>',
    diamond: '<path d="M5 .4L9.6 5 5 9.6.4 5z"/>', 'triangle-up': '<path d="M5 .8L9.6 9H.4z"/>',
    'triangle-down': '<path d="M5 9.2L.4 1h9.2z"/>', cross: '<path d="M3.4.6h3.2v2.8h2.8v3.2H6.6v2.8H3.4V6.6H.6V3.4h2.8z"/>',
  };
  // Aladin Lite 3 ships as an ES module; a pinned version from two mirrors.
  const ALADIN_SOURCES = ['https://cdn.jsdelivr.net/npm/aladin-lite@3.6.5/dist/aladin.js',
    'https://unpkg.com/aladin-lite@3.6.5/dist/aladin.js'];
  const ZERO = { dx: 0, dy: 0 };
  const sleep = (ms) => new Promise((res) => setTimeout(res, ms));
  const num = (v) => { const x = parseFloat(String(v).replace(',', '.')); return Number.isFinite(x) ? x : 0; };

  const state = {
    config: null, target: null, result: null, frames: [], wcs: null, jobId: null,
    sequence: [], seqPos: 0, playing: false, timer: null, loadToken: 0,
    spectrum: null, probe: null, selected: null, hover: null, fullRange: false,
    hiddenEpochs: new Set(), hiddenBands: new Set(), changesOpen: false,
    catalogs: { gaia: null, catwise: null },
    aladin: null, recording: false,
    shiftCache: new Map(), diffCache: new Map(), sumCache: new Map(), levelCache: new Map(),
    upCache: new Map(), upTotal: 0, rgbCache: new Map(),
  };

  // ───────────────────────── small helpers ─────────────────────────
  function b64ToBytes(b64) {
    const bin = atob(b64);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  }
  function decodeFrame(f) {
    const data = b64ToBytes(f.data);
    const flags = f.flags ? b64ToBytes(f.flags) : null;
    const frame = {
      meta: f,
      data: new Float32Array(data.buffer, 0, data.length / 4),
      nexp: f.nexp ? b64ToBytes(f.nexp) : null,
      flags: flags ? new Uint32Array(flags.buffer, 0, flags.length / 4) : null,
      sat: null,
    };
    delete f.data; delete f.flags; delete f.nexp;        // keep only the typed arrays
    frame.sat = fillSaturated(frame.data, f.shape[0], f.stats);
    return frame;
  }
  /**
   * The core of a saturated star arrives with no data: every exposure dropped those pixels, and
   * the brightest star of the field would be drawn with a dark hole in it. A small gap that is
   * ringed by bright pixels is filled with the brightest value around it. Returns a mask of the
   * filled pixels (they are reported as saturated, never as a measurement), or null.
   */
  function fillSaturated(data, n, stats) {
    const bright = stats.p50 + 25 * Math.max(stats.rms, 1e-6);
    const seen = new Uint8Array(n * n);
    let sat = null;
    for (let i = 0; i < n * n; i++) {
      if (data[i] === data[i] || seen[i]) continue;
      const region = [i];
      let open = false, peak = -Infinity, low = Infinity;
      seen[i] = 1;
      for (let k = 0; k < region.length; k++) {
        const x = region[k] % n, y = (region[k] / n) | 0;
        for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) {
          const xx = x + dx, yy = y + dy;
          if (xx < 0 || yy < 0 || xx >= n || yy >= n) { open = true; continue; }
          const q = yy * n + xx, v = data[q];
          if (v !== v) { if (!seen[q]) { seen[q] = 1; region.push(q); } } else { if (v > peak) peak = v; if (v < low) low = v; }
        }
      }
      if (open || region.length > 120 || low < bright) continue;
      if (!sat) sat = new Uint8Array(n * n);
      region.forEach((q) => { data[q] = peak; sat[q] = 1; });
    }
    return sat;
  }
  const pad = (n, w = 2) => String(n).padStart(w, '0');
  function raToHms(ra, digits = 2) {
    const h = (((ra % 360) + 360) % 360) / 15, hh = Math.floor(h), m = (h - hh) * 60, mm = Math.floor(m), s = (m - mm) * 60;
    return `${pad(hh)}h${pad(mm)}m${s.toFixed(digits).padStart(digits ? digits + 3 : 2, '0')}s`;
  }
  function decToDms(dec, digits = 1) {
    const sign = dec < 0 ? '−' : '+', a = Math.abs(dec), d = Math.floor(a), m = (a - d) * 60, mm = Math.floor(m), s = (m - mm) * 60;
    return `${sign}${pad(d)}°${pad(mm)}′${s.toFixed(digits).padStart(digits ? digits + 3 : 2, '0')}″`;
  }
  const shortCoord = (ra, dec) => `${raToHms(ra, 1)} ${decToDms(dec, 0)}`;
  const mjdToDate = (mjd) => new Date((mjd - 40587) * 86400000).toISOString();
  /** "2026-08-23T…" as "23 Aug 2026", and as "Aug 2026". */
  const fmtDate = (iso) => { const s = String(iso || ''); return s.length >= 10 ? `${parseInt(s.slice(8, 10), 10)} ${MONTHS[parseInt(s.slice(5, 7), 10) - 1]} ${s.slice(0, 4)}` : s; };
  const fmtMonth = (iso) => { const s = String(iso || ''); return s.length >= 7 ? `${MONTHS[parseInt(s.slice(5, 7), 10) - 1]} ${s.slice(0, 4)}` : s; };
  const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;
  function propagate(ra, dec, pmra, pmdec, from, to) {
    if (!pmra && !pmdec) return { ra, dec };
    const dt = to - from, cosd = Math.max(Math.cos(dec * D2R), 1e-6);
    return { ra: ra + (pmra || 0) / 3.6e6 * dt / cosd, dec: dec + (pmdec || 0) / 3.6e6 * dt };
  }
  function skyToPix(w, ra, dec) {
    const ra0 = w.crval[0] * D2R, dec0 = w.crval[1] * D2R, a = ra * D2R, d = dec * D2R;
    const cosc = Math.sin(dec0) * Math.sin(d) + Math.cos(dec0) * Math.cos(d) * Math.cos(a - ra0);
    if (cosc <= 0) return null;
    const xi = Math.cos(d) * Math.sin(a - ra0) / cosc;
    const eta = (Math.cos(dec0) * Math.sin(d) - Math.sin(dec0) * Math.cos(d) * Math.cos(a - ra0)) / cosc;
    return { x: (w.crpix[0] - 1) + xi * R2D / w.cdelt[0], y: (w.crpix[1] - 1) + eta * R2D / w.cdelt[1] };
  }
  function pixToSky(w, x, y) {
    const xi = (x - (w.crpix[0] - 1)) * w.cdelt[0] * D2R, eta = (y - (w.crpix[1] - 1)) * w.cdelt[1] * D2R;
    const ra0 = w.crval[0] * D2R, dec0 = w.crval[1] * D2R;
    const denom = Math.cos(dec0) - eta * Math.sin(dec0);
    const ra = ra0 + Math.atan2(xi, denom);
    const dec = Math.atan2(Math.sin(dec0) + eta * Math.cos(dec0), Math.hypot(xi, denom));
    return { ra: ((ra * R2D) % 360 + 360) % 360, dec: dec * R2D };
  }
  function median(values) {
    const v = values.filter(Number.isFinite).sort((a, b) => a - b);
    return v.length ? v[Math.floor(v.length / 2)] : 0;
  }
  function robustStats(arr) {
    const vals = [];
    const step = Math.max(1, Math.floor(arr.length / 20000));
    for (let i = 0; i < arr.length; i += step) if (Number.isFinite(arr[i])) vals.push(arr[i]);
    vals.sort((a, b) => a - b);
    if (!vals.length) return { med: 0, rms: 1e-6 };
    const med = vals[Math.floor(vals.length / 2)];
    const dev = vals.map((v) => Math.abs(v - med)).sort((a, b) => a - b);
    return { med, rms: 1.4826 * dev[Math.floor(dev.length / 2)] || 1e-6 };
  }
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const sentence = (s) => { const t = String(s || '').trim(); return t.charAt(0).toUpperCase() + t.slice(1); };
  /** Text from the server with typographic apostrophes, as written everywhere else on the page. */
  const curly = (s) => String(s || '').replace(/'/g, '’');
  /** Names as shown in headings: Simbad's "NAME " prefix dropped, a true minus sign in designations. */
  const pretty = (name) => curly(String(name || '').replace(/^NAME\s+/, '')).replace(/(\d)-(\d)/g, '$1−$2');
  /** A server progress message as a short status line. */
  const statusText = (m) => (/^(queued|starting|done|loaded)/i.test(m || '') ? 'Working' : sentence(String(m || '').split(' · ')[0]).replace(/(\d+)\/(\d+)/, '$1 of $2'));
  const hexToRgba = (hex, a) => { const v = parseInt(hex.slice(1), 16); return `rgba(${v >> 16}, ${(v >> 8) & 255}, ${v & 255}, ${a})`; };
  function mixStops(stops, t) {
    const x = Math.min(1, Math.max(0, t)) * (stops.length - 1), i = Math.min(stops.length - 2, Math.floor(x)), u = x - i;
    const a = parseInt(stops[i].slice(1), 16), b = parseInt(stops[i + 1].slice(1), 16);
    const ch = (s) => Math.round(((a >> s) & 255) + (((b >> s) & 255) - ((a >> s) & 255)) * u);
    return `#${((1 << 24) | (ch(16) << 16) | (ch(8) << 8) | ch(0)).toString(16).slice(1)}`;
  }
  /** What went wrong, in words a visitor can act on. */
  function plainError(message) {
    const m = String(message || '');
    if (/No SPHEREx exposures/.test(m)) return 'SPHEREx has no images of this position yet.';
    if (/Failed to fetch|NetworkError/i.test(m)) return 'The server is not answering.';
    if (/Name resolver|Sesame/i.test(m)) return 'The name service is not answering. Try coordinates instead.';
    if (/SIA|IRSA|timed out|ConnectionPool|Max retries|Connection|HTTPError|archive/i.test(m)) return 'The SPHEREx archive at IRSA is not answering right now. Try again in a moment.';
    return sentence(m.replace(/^[A-Za-z]+(Error|Exception):\s*/, ''));
  }
  function showError(msg, retry = false) {
    $('#error-text').textContent = msg;
    $('#btn-load').classList.toggle('hidden', !retry);
    $('#error').classList.remove('hidden');
  }
  function clearError() { $('#error').classList.add('hidden'); }
  function setProgress(visible, frac, text) {
    $('#progress').classList.toggle('hidden', !visible);
    $('#progress-fill').style.width = `${Math.round(Math.min(1, frac || 0) * 100)}%`;
    $('#progress-text').textContent = text || '';
  }

  // ───────────────────────── colour maps ─────────────────────────
  const CMAPS = {
    gray: [[0, 0, 0, 0], [1, 255, 255, 255]],
    inferno: [[0, 0, 0, 4], [0.25, 87, 16, 110], [0.5, 188, 55, 84], [0.75, 249, 142, 9], [1, 252, 255, 164]],
    viridis: [[0, 68, 1, 84], [0.25, 59, 82, 139], [0.5, 33, 145, 140], [0.75, 94, 201, 98], [1, 253, 231, 37]],
    diverging: [[0, 64, 132, 246], [0.5, 4, 6, 14], [1, 236, 56, 48]],
    divergingLight: [[0, 26, 64, 191], [0.5, 247, 248, 251], [1, 200, 36, 40]],
  };
  const DIFF_KEY = { brighter: 'rgb(236, 56, 48)', fainter: 'rgb(64, 132, 246)' };
  const BLANK_DARK = [10, 13, 26], BLANK_LIGHT = [228, 231, 240];
  // Colour composite: red, green and blue are the long, middle and short pair of detector bands.
  const COLOUR_PAIRS = [['D5', 'D6'], ['D3', 'D4'], ['D1', 'D2']];
  const COLOUR_KEY = [['#ff5d52', '3.8–5.0 µm'], ['#5fd38d', '1.6–3.9 µm'], ['#6aa5ff', '0.75–1.65 µm']];
  const lutCache = new Map();
  function lut(name, invert) {
    const key = `${name}|${invert ? 1 : 0}`;
    if (lutCache.has(key)) return lutCache.get(key);
    const stops = CMAPS[name] || CMAPS.gray;
    const out = new Uint8ClampedArray(256 * 3);
    for (let i = 0; i < 256; i++) {
      const t = (invert ? 255 - i : i) / 255;
      let k = 0;
      while (k < stops.length - 2 && t > stops[k + 1][0]) k++;
      const a = stops[k], b = stops[k + 1], u = (t - a[0]) / Math.max(b[0] - a[0], 1e-9);
      for (let c = 0; c < 3; c++) out[i * 3 + c] = a[c + 1] + (b[c + 1] - a[c + 1]) * u;
    }
    lutCache.set(key, out);
    return out;
  }
  function stretchFn(kind) {
    const k = 10;
    if (kind === 'linear') return (t) => t;
    if (kind === 'sqrt') return (t) => Math.sqrt(t);
    if (kind === 'log') return (t) => Math.log1p(t * 100) / Math.log1p(100);
    const norm = Math.asinh(k);
    return (t) => Math.asinh(t * k) / norm;
  }
  const STRETCH_STEPS = 4096;
  const stretchTables = new Map();
  /** The stretch as a lookup from a clipped value in 0..1 to an offset into a colour table. */
  function stretchTable(kind) {
    if (!stretchTables.has(kind)) {
      const f = stretchFn(kind), out = new Uint16Array(STRETCH_STEPS + 1);
      for (let i = 0; i <= STRETCH_STEPS; i++) out[i] = Math.round(f(i / STRETCH_STEPS) * 255) * 3;
      stretchTables.set(kind, out);
    }
    return stretchTables.get(kind);
  }
  function renderImage(values, n, lo, hi, kind, table, blank) {
    const img = new ImageData(n, n);
    const px = img.data, steps = stretchTable(kind), scale = STRETCH_STEPS / Math.max(hi - lo, 1e-12);
    for (let y = 0; y < n; y++) {
      const row = n - 1 - y;                       // FITS row 0 is the bottom of the image
      for (let x = 0; x < n; x++) {
        const v = values[row * n + x];
        const o = (y * n + x) * 4;
        px[o + 3] = 255;
        if (v !== v) { px[o] = blank[0]; px[o + 1] = blank[1]; px[o + 2] = blank[2]; continue; }      // NaN: no data
        let t = (v - lo) * scale;
        t = t < 0 ? 0 : t > STRETCH_STEPS ? STRETCH_STEPS : t;
        const i = steps[t | 0];
        px[o] = table[i]; px[o + 1] = table[i + 1]; px[o + 2] = table[i + 2];
      }
    }
    return img;
  }
  /**
   * Three channels to one colour image. Each channel is scaled so that the stars of the field
   * come out white on average; the stretch acts on the brightness and keeps the colour, so a
   * source that is brighter at long wavelengths than its neighbours is red however bright it is.
   */
  function renderColour(channels, n, lv, kind, invert, blank) {
    const img = new ImageData(n, n);
    const px = img.data, steps = stretchTable(kind), [R, G, B] = channels;
    const kr = 1 / lv[0].span, kg = 1 / lv[1].span, kb = 1 / lv[2].span;
    for (let y = 0; y < n; y++) {
      const row = (n - 1 - y) * n;                 // FITS row 0 is the bottom of the image
      for (let x = 0; x < n; x++) {
        const r = R[row + x], g = G[row + x], b = B[row + x];
        const o = (y * n + x) * 4;
        px[o + 3] = 255;
        if (r !== r || g !== g || b !== b) { px[o] = blank[0]; px[o + 1] = blank[1]; px[o + 2] = blank[2]; continue; }
        let tr = (r - lv[0].lo) * kr, tg = (g - lv[1].lo) * kg, tb = (b - lv[2].lo) * kb;
        if (tr < 0) tr = 0; if (tg < 0) tg = 0; if (tb < 0) tb = 0;
        const mean = (tr + tg + tb) / 3;
        let cr = 0, cg = 0, cb = 0;
        if (mean > 0) {
          const s = steps[(mean > 1 ? 1 : mean) * STRETCH_STEPS | 0] / (3 * mean);
          cr = tr * s; cg = tg * s; cb = tb * s;
          const top = Math.max(cr, cg, cb);
          if (top > 255) { const k = 255 / top; cr *= k; cg *= k; cb *= k; }
          if (mean > 2) {                           // very bright, often saturated: it burns out to white, as on a detector
            const w = mean > 10 ? 1 : (mean - 2) / 8;
            cr += (255 - cr) * w; cg += (255 - cg) * w; cb += (255 - cb) * w;
          }
        }
        if (invert) {                               // dark on light: the brightness flips, the hue stays
          const shift = 255 - Math.max(cr, cg, cb) - Math.min(cr, cg, cb);
          cr += shift; cg += shift; cb += shift;
        }
        px[o] = cr; px[o + 1] = cg; px[o + 2] = cb;
      }
    }
    return img;
  }
  /**
   * Enlarges a float image `f` times with a cubic B-spline, in flux before any stretch, so a star
   * becomes a round blob instead of a block. The kernel has no negative lobes: no dark rings
   * around bright stars. Missing pixels stay missing.
   */
  function upsample(src, n, f) {
    const m = n * f, tmp = new Float32Array(m * n), out = new Float32Array(m * m);
    const first = new Int8Array(f), w = new Float32Array(f * 4);
    for (let k = 0; k < f; k++) {
      const x = (k + 0.5) / f - 0.5, t = x < 0 ? x + 1 : x, u = 1 - t;
      first[k] = x < 0 ? -2 : -1;                               // offset of the first of the four taps
      w[k * 4] = u * u * u / 6; w[k * 4 + 1] = (3 * t * t * t - 6 * t * t + 4) / 6;
      w[k * 4 + 2] = (-3 * t * t * t + 3 * t * t + 3 * t + 1) / 6; w[k * 4 + 3] = t * t * t / 6;
    }
    const clampTap = (i) => (i < 0 ? 0 : i >= n ? n - 1 : i);
    for (let y = 0; y < n; y++) {                               // along x: n rows of n -> n rows of m
      for (let j = 0; j < m; j++) {
        const k = j % f, i0 = ((j / f) | 0) + first[k];
        let acc = 0, sum = 0;
        for (let a = 0; a < 4; a++) {
          const v = src[y * n + clampTap(i0 + a)];
          if (v === v) { acc += v * w[k * 4 + a]; sum += w[k * 4 + a]; }
        }
        tmp[y * m + j] = sum > 0.4 ? acc / sum : NaN;
      }
    }
    for (let r = 0; r < m; r++) {                               // along y: n rows of m -> m rows of m
      const k = r % f, i0 = ((r / f) | 0) + first[k];
      const r0 = clampTap(i0) * m, r1 = clampTap(i0 + 1) * m, r2 = clampTap(i0 + 2) * m, r3 = clampTap(i0 + 3) * m;
      const w0 = w[k * 4], w1 = w[k * 4 + 1], w2 = w[k * 4 + 2], w3 = w[k * 4 + 3];
      for (let c = 0; c < m; c++) {
        const a = tmp[r0 + c], b = tmp[r1 + c], d = tmp[r2 + c], e = tmp[r3 + c];
        let acc = 0, sum = 0;
        if (a === a) { acc += a * w0; sum += w0; }
        if (b === b) { acc += b * w1; sum += w1; }
        if (d === d) { acc += d * w2; sum += w2; }
        if (e === e) { acc += e * w3; sum += w3; }
        out[r * m + c] = sum > 0.4 ? acc / sum : NaN;
      }
    }
    return out;
  }

  // ───────────────────────── selections ─────────────────────────
  const checked = (name, fallback) => { const el = document.querySelector(`input[name=${name}]:checked`); return el ? el.value : fallback; };
  function setRadio(name, value) { const el = document.querySelector(`input[name=${name}][value="${value}"]`); if (el && !el.disabled) el.checked = true; }
  const view = () => checked('view', 'normal');                       // normal | blink | diff | sum | exposure
  const seqKind = () => (view() === 'exposure' ? 'exposure' : 'epoch');
  const mode = () => (view() === 'exposure' ? 'normal' : view());
  const bandChoice = () => checked('band', 'ALL');
  /** The band whose frames are played. "Colour" plays the all-band frames and paints them from three band pairs. */
  const band = () => (bandChoice() === 'RGB' ? 'ALL' : bandChoice());
  const refEpoch = () => parseInt($('#ref-epoch').value || '1', 10);
  const blinkEpoch = () => parseInt($('#blink-b').value || '2', 10);
  const group = () => (state.result ? state.result.params.group : 'epoch');
  const inverted = () => $('#chk-invert').checked;
  const colour = () => bandChoice() === 'RGB' && group() === 'epoch';
  function framesOfBand(b) {
    return state.frames.map((f, i) => ({ f, i })).filter((o) => o.f.meta.band === b)
      .sort((a, c) => a.f.meta.mjd - c.f.meta.mjd);
  }
  /** The date of an epoch as shown under the image. */
  function epochDate(index) {
    const f = state.frames.find((x) => x.meta.epoch === index && x.meta.band === 'ALL') || state.frames.find((x) => x.meta.epoch === index);
    if (f) return fmtDate(f.meta.date);
    const ep = state.result && state.result.epochs.find((e) => e.index === index);
    return ep ? fmtDate(ep.date_start) : `Epoch ${index}`;
  }

  // ───────────────────────── proper-motion tracking (shift and add) ─────────────────────────
  function tracking() {
    if (!$('#chk-track').checked) return null;
    const ra = num($('#pmra').value), dec = num($('#pmdec').value);
    return (ra || dec) ? { ra, dec } : null;
  }
  const trackKey = () => { const t = tracking(); return t ? `${t.ra}|${t.dec}` : ''; };
  function refYear() {
    const list = group() === 'exposure' ? state.frames : framesOfBand(band()).map((o) => o.f);
    return list.length ? list[0].meta.year : 2026;
  }
  /** Pixel offset to sample a frame at, so that an object with the tracked motion stays put. */
  function shiftFor(year) {
    const t = tracking();
    if (!t || year == null || !state.result) return ZERO;
    const dt = year - refYear(), scale = state.result.target.scale_arcsec;
    return { dx: -(t.ra / 1000) * dt / scale, dy: (t.dec / 1000) * dt / scale };
  }
  function shiftArray(data, n, dx, dy) {
    const out = new Float32Array(n * n).fill(NaN);
    const ix = Math.floor(dx), iy = Math.floor(dy), fx = dx - ix, fy = dy - iy;
    const w00 = (1 - fx) * (1 - fy), w10 = fx * (1 - fy), w01 = (1 - fx) * fy, w11 = fx * fy;
    for (let y = 0; y < n; y++) {
      const sy = y + iy;
      if (sy < 0 || sy + 1 >= n) continue;
      for (let x = 0; x < n; x++) {
        const sx = x + ix;
        if (sx < 0 || sx + 1 >= n) continue;
        const o = sy * n + sx;
        out[y * n + x] = data[o] * w00 + data[o + 1] * w10 + data[o + n] * w01 + data[o + n + 1] * w11;
      }
    }
    return out;
  }
  function frameData(idx) {
    const f = state.frames[idx];
    const s = shiftFor(f.meta.year);
    if (!s.dx && !s.dy) return f.data;
    const key = `${idx}|${s.dx.toFixed(4)}|${s.dy.toFixed(4)}`;
    if (!state.shiftCache.has(key)) {
      if (state.shiftCache.size > 400) state.shiftCache.clear();
      state.shiftCache.set(key, shiftArray(f.data, state.result.target.npix, s.dx, s.dy));
    }
    return state.shiftCache.get(key);
  }
  const layer = { key: '', canvas: document.createElement('canvas') };          // the image under the overlays
  function clearDerived() {
    state.shiftCache.clear(); state.diffCache.clear(); state.sumCache.clear(); state.levelCache.clear();
    state.upCache.clear(); state.upTotal = 0; state.rgbCache.clear(); layer.key = '';
  }

  // ───────────────────────── sequence ─────────────────────────
  function buildSequence() {
    const m = mode();
    if (!state.result) {
      state.sequence = [];
    } else if (group() === 'exposure') {
      const b = band();
      state.sequence = state.frames.map((f, i) => ({ f, i })).filter((o) => b === 'ALL' || o.f.meta.band === b).map((o) => o.i);
    } else {
      const list = framesOfBand(band());
      if (m === 'blink') {
        const a = list.find((o) => o.f.meta.epoch === refEpoch()), b = list.find((o) => o.f.meta.epoch === blinkEpoch());
        state.sequence = [a, b].filter(Boolean).map((o) => o.i);
      } else if (m === 'diff') {
        state.sequence = list.filter((o) => o.f.meta.epoch !== refEpoch()).map((o) => o.i);
        if (!state.sequence.length) state.sequence = list.map((o) => o.i);
      } else if (m === 'sum') {
        state.sequence = list.length ? ['sum'] : [];
      } else {
        state.sequence = list.map((o) => o.i);
      }
    }
    state.seqPos = Math.max(0, Math.min(state.seqPos, state.sequence.length - 1));
    const slider = $('#frame-slider');
    slider.max = Math.max(0, state.sequence.length - 1);
    slider.disabled = state.sequence.length < 2;
    ['#btn-prev', '#btn-play', '#btn-next'].forEach((sel) => { $(sel).disabled = state.sequence.length < 2; });
    syncSlider();
  }
  function refFrameIndex() {
    const o = framesOfBand(band()).find((x) => x.f.meta.epoch === refEpoch());
    return o ? o.i : null;
  }
  function diffArray(idx, ref) {
    const key = `${idx}-${ref}|${trackKey()}`;
    if (state.diffCache.has(key)) return state.diffCache.get(key);
    const a = frameData(idx), b = frameData(ref), out = new Float32Array(a.length);
    for (let i = 0; i < a.length; i++) out[i] = a[i] - b[i];
    state.diffCache.set(key, out);
    return out;
  }
  function sumArray(b) {
    const key = `${b}|${trackKey()}`;
    if (state.sumCache.has(key)) return state.sumCache.get(key);
    const list = framesOfBand(b);
    const n = state.result.target.npix * state.result.target.npix;
    const acc = new Float32Array(n), cnt = new Uint16Array(n);
    list.forEach((o) => { const d = frameData(o.i); for (let i = 0; i < n; i++) if (Number.isFinite(d[i])) { acc[i] += d[i]; cnt[i]++; } });
    const out = new Float32Array(n);
    for (let i = 0; i < n; i++) out[i] = cnt[i] ? acc[i] / cnt[i] : NaN;
    state.sumCache.set(key, out);
    return out;
  }
  function currentFrame() {
    if (!state.sequence.length) return null;
    const idx = state.sequence[state.seqPos];
    if (idx === 'sum') {
      const list = framesOfBand(band());
      const follow = tracking();
      return {
        key: `sum|${band()}|${trackKey()}`,
        meta: {
          label: `${follow ? 'Shift-and-add' : 'Sum'} of ${plural(list.length, 'epoch')}`, band: band(),
          year: follow ? refYear() : median(list.map((o) => o.f.meta.year)), epoch: null,
          n_exposures: list.reduce((s, o) => s + o.f.meta.n_exposures, 0), isSum: true,
        },
        data: sumArray(band()), flags: null, nexp: null, shift: ZERO,
      };
    }
    const f = state.frames[idx];
    const shift = shiftFor(f.meta.year);
    if (mode() === 'diff' && group() === 'epoch') {
      const ref = refFrameIndex();
      if (ref !== null && ref !== idx) {
        return { key: `diff|${idx}|${ref}|${trackKey()}`, meta: { ...f.meta, isDiff: true }, data: diffArray(idx, ref), flags: null, nexp: f.nexp, shift };
      }
    }
    const moved = shift.dx || shift.dy;
    return { key: `frame|${idx}|${trackKey()}`, meta: f.meta, data: frameData(idx), flags: moved ? null : f.flags, nexp: moved ? null : f.nexp, sat: moved ? null : f.sat, shift };
  }
  function contrastGain() { return Math.pow(10, 3.6 - 3.2 * parseFloat($('#contrast').value) / 100); }
  function bandLevels(b) {
    const list = group() === 'exposure' ? state.sequence.filter((i) => i !== 'sum').map((i) => state.frames[i]) : framesOfBand(b).map((o) => o.f);
    const rms = median(list.map((f) => f.meta.stats.rms)) || 1e-3;
    const med = median(list.map((f) => f.meta.stats.p50));
    return { lo: med - 1.5 * rms, hi: med + contrastGain() * rms };
  }
  function levels(frame) {
    const key = `${mode()}|${band()}|${$('#contrast').value}|${frame.meta.isDiff ? `d${refEpoch()}` : ''}|${group()}|${trackKey()}`;
    if (state.levelCache.has(key)) return state.levelCache.get(key);
    let out;
    if (frame.meta.isDiff) {
      const st = robustStats(frame.data);
      const amp = Math.max(st.rms * Math.max(contrastGain() / 6, 3), 1e-6);
      out = { lo: -amp, hi: amp };
    } else {
      out = bandLevels(band());            // one level for the whole band, so blinking does not flicker
    }
    state.levelCache.set(key, out);
    return out;
  }

  // ───────────────────────── drawing ─────────────────────────
  const canvas = $('#view');
  const ctx = canvas.getContext('2d');

  function fitCanvas() {
    const box = $('#canvas-wrap').getBoundingClientRect();
    const side = Math.min(box.width, box.height) || box.width || 300;
    const size = Math.max(240, Math.round(side * Math.min(window.devicePixelRatio || 1, 2)));
    if (canvas.width !== size) { canvas.width = size; canvas.height = size; }
  }
  function upsampled(key, data, n, f) {
    if (f === 1) return data;
    let out = state.upCache.get(`${key}|${f}`);
    if (!out) {
      if (state.upTotal > 3.6e7) { state.upCache.clear(); state.upTotal = 0; }
      out = upsample(data, n, f);
      state.upCache.set(`${key}|${f}`, out); state.upTotal += out.length;
    }
    return out;
  }
  /** Red, green and blue images for a frame: each the mean of a pair of band stacks. Null when a pair is missing. */
  function colourChannels(frame) {
    const n2 = state.result.target.npix ** 2, out = [];
    for (const pair of COLOUR_PAIRS) {
      const key = `${frame.key}|${pair[0]}`;
      let ch = state.rgbCache.get(key);
      if (!ch) {
        const parts = pair.map((b) => {
          if (frame.meta.isSum) return framesOfBand(b).length ? sumArray(b) : null;
          const i = state.frames.findIndex((f) => f.meta.band === b && f.meta.epoch === frame.meta.epoch);
          return i < 0 ? null : frameData(i);
        }).filter(Boolean);
        if (!parts.length) return null;
        if (parts.length === 1) ch = parts[0];
        else {
          ch = new Float32Array(n2);
          for (let i = 0; i < n2; i++) { const a = parts[0][i], b = parts[1][i]; ch[i] = a === a ? (b === b ? (a + b) / 2 : a) : b; }
        }
        if (state.rgbCache.size > 120) state.rgbCache.clear();
        state.rgbCache.set(key, ch);
      }
      out.push({ key, data: ch });
    }
    return out;
  }
  /** Black point and white point of each colour channel: the same for every epoch, so the film does not flicker. */
  function colourLevels() {
    const key = `rgb|${$('#contrast').value}|${trackKey()}`;
    if (!state.levelCache.has(key)) {
      state.levelCache.set(key, COLOUR_PAIRS.map((pair) => {
        const stats = pair.flatMap((b) => framesOfBand(b).map((o) => o.f.meta.stats));
        const rms = median(stats.map((s) => s.rms)) || 1e-3, mid = median(stats.map((s) => s.p50));
        const white = Math.max(median(stats.map((s) => s.p99 - s.p50)), 40 * rms);
        // the black point sits just above the sky, so its noise does not tint the background
        return { lo: mid + rms, span: white * contrastGain() / 110 };
      }));
    }
    return state.levelCache.get(key);
  }
  /** The frame as pixels, stretched and coloured; redrawn only when something about it changes. */
  function baseLayer(frame, W) {
    const n = state.result.target.npix;
    const f = $('#chk-smooth').checked ? Math.max(1, Math.min(8, Math.round(Math.min(W, 1200) / n))) : 1;
    const { lo, hi } = levels(frame);
    const inv = inverted();
    const cmap = frame.meta.isDiff ? (inv ? 'divergingLight' : 'diverging') : $('#cmap').value;
    const stretch = frame.meta.isDiff ? 'linear' : $('#stretch').value;
    const channels = colour() && !frame.meta.isDiff ? colourChannels(frame) : null;
    const key = [frame.key, f, lo, hi, channels ? `rgb${$('#contrast').value}` : cmap, inv, stretch].join('|');
    if (layer.key !== key) {
      const m = n * f, blank = inv ? BLANK_LIGHT : BLANK_DARK;
      const table = lut(cmap, frame.meta.isDiff ? false : inv);
      const img = channels
        ? renderColour(channels.map((c) => upsampled(c.key, c.data, n, f)), m, colourLevels(), stretch, inv, blank)
        : renderImage(upsampled(frame.key, frame.data, n, f), m, lo, hi, stretch, table, blank);
      layer.canvas.width = m; layer.canvas.height = m;
      layer.canvas.getContext('2d').putImageData(img, 0, 0);
      layer.key = key;
    }
    return layer.canvas;
  }
  /** Draws the canvas only (image and overlays); cheap enough to call on every pointer move. */
  function paint() {
    fitCanvas();
    const W = canvas.width;
    const frame = currentFrame();
    if (!frame) { ctx.fillStyle = '#000'; ctx.fillRect(0, 0, W, W); return null; }
    const n = state.result.target.npix;
    ctx.imageSmoothingEnabled = $('#chk-smooth').checked;
    ctx.imageSmoothingQuality = 'high';
    ctx.drawImage(baseLayer(frame, W), 0, 0, W, W);
    if ($('#chk-flags').checked && frame.flags) drawFlags(frame, n, W);
    drawOverlays(frame, n, W);
    return frame;
  }
  function draw() {
    const frame = paint();
    updateTitle(frame);
    updateLegend(frame);
    highlightThumb();
  }
  function flagCategories() {
    const defs = (state.result && state.result.flag_defs) || {};
    const bits = (names) => names.reduce((m, nm) => (defs[nm] !== undefined ? m | (1 << defs[nm]) : m), 0) >>> 0;
    return [
      { label: 'Saturated', solid: '#ff5d52', color: 'rgba(255, 93, 82, 0.6)', mask: bits(['OVERFLOW', 'NONLINEAR', 'TRANSIENT', 'SUR_ERROR', 'BLOOM']) },
      { label: 'Bad pixel', solid: '#ffd23f', color: 'rgba(255, 210, 63, 0.6)', mask: bits(['HOT', 'COLD', 'NONFUNC', 'MISSING_DATA', 'PHANTOM', 'REFERENCE', 'DICHROIC']) },
      { label: 'Artefact', solid: '#7cc4ff', color: 'rgba(124, 196, 255, 0.6)', mask: bits(['PERSIST', 'OUTLIER', 'SNOWBALL', 'HALO', 'STREAK', 'SATELLITE_HALO', 'GHOST', 'GHOST_EXT', 'CROSSTALK']) },
    ];
  }
  function drawFlags(frame, n, W) {
    const s = W / n;
    flagCategories().forEach((cat) => {
      if (!cat.mask) return;
      ctx.fillStyle = cat.color;
      for (let y = 0; y < n; y++) for (let x = 0; x < n; x++) {
        if ((frame.flags[y * n + x] & cat.mask) >>> 0) ctx.fillRect(x * s, (n - 1 - y) * s, s + 0.5, s + 0.5);
      }
    });
  }
  function arrowPath(x0, y0, x1, y1, head) {
    const ang = Math.atan2(y1 - y0, x1 - x0);
    ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x1, y1);
    ctx.moveTo(x1 - head * Math.cos(ang - 0.5), y1 - head * Math.sin(ang - 0.5)); ctx.lineTo(x1, y1);
    ctx.lineTo(x1 - head * Math.cos(ang + 0.5), y1 - head * Math.sin(ang + 0.5));
  }
  function sourcePixel(src, frame) {
    const hit = frame.meta.epoch ? src.track.find((t) => t.epoch === frame.meta.epoch) : null;
    return hit ? { x: hit.x, y: hit.y } : { x: src.x, y: src.y };
  }
  function drawOverlays(frame, n, W) {
    const w = state.wcs, px = W / n, sh = frame.shift || ZERO;
    const u = W / (canvas.getBoundingClientRect().width || W);          // canvas pixels per CSS pixel
    const C = OVERLAY[inverted() ? 'light' : 'dark'];
    const year = frame.meta.year || (state.target ? state.target.epoch_now : 2026);
    const T = (x, y) => ({ cx: (x - sh.dx + 0.5) * px, cy: (n - (y - sh.dy + 0.5)) * px });
    const inside = (c) => c.cx >= 0 && c.cy >= 0 && c.cx <= W && c.cy <= W;
    // every mark is stroked twice: a soft halo first, so it stays readable on stars and on empty sky alike
    const stroke = (color, width) => {
      ctx.lineWidth = width + 2 * u; ctx.strokeStyle = C.halo; ctx.stroke();
      ctx.lineWidth = width; ctx.strokeStyle = color; ctx.stroke();
    };
    const label = (text, x, y, color) => {
      ctx.lineWidth = 3 * u; ctx.strokeStyle = C.halo; ctx.strokeText(text, x, y);
      ctx.fillStyle = color; ctx.fillText(text, x, y);
    };
    ctx.lineJoin = 'round'; ctx.lineCap = 'round';
    ctx.font = `500 ${Math.round(11.5 * u)}px ${FONT}`;
    ctx.textAlign = 'left'; ctx.textBaseline = 'alphabetic';

    // catalogues
    [['gaia', C.gaia, false, 20], ['catwise', C.catwise, true, 17]].forEach(([name, color, square, faint]) => {
      const rows = state.catalogs[name];
      if (!$(`#chk-${name}`).checked || !rows) return;
      ctx.strokeStyle = color; ctx.lineWidth = 1.3 * u;
      rows.forEach((s) => {
        const p = propagate(s.ra, s.dec, s.pmra, s.pmdec, s.epoch, year);
        const q = skyToPix(w, p.ra, p.dec); if (!q) return;
        const c = T(q.x, q.y); if (!inside(c)) return;
        const r = Math.max(3.5 * u, (faint - (s.mag || faint)) * px * 0.1 + px * 0.45);
        ctx.beginPath();
        if (square) ctx.rect(c.cx - r, c.cy - r, 2 * r, 2 * r); else ctx.arc(c.cx, c.cy, r, 0, 2 * Math.PI);
        ctx.stroke();
        if (Math.hypot(s.pmra || 0, s.pmdec || 0) > 150) {       // arrow: where it will be in 20 years
          const p2 = propagate(s.ra, s.dec, s.pmra, s.pmdec, s.epoch, year + 20);
          const q2 = skyToPix(w, p2.ra, p2.dec);
          if (q2) { const c2 = T(q2.x, q2.y); arrowPath(c.cx, c.cy, c2.cx, c2.cy, 6 * u); ctx.stroke(); }
        }
      });
    });

    // what changed
    const analysis = state.result.analysis;
    if (analysis && group() === 'epoch') {
      analysis.sources.forEach((src) => {
        const chosen = state.selected && state.selected.id === src.id;
        const changed = src.moving || src.variable;
        if (!chosen && !($('#chk-changes').checked && changed)) return;
        const p = sourcePixel(src, frame), c = T(p.x, p.y);
        if (!inside(c)) return;
        const color = changed ? C.change : C.target;
        const r = Math.max(9 * u, px * 2.3), width = (chosen ? 2.6 : 1.6) * u;
        ctx.beginPath();
        if (src.variable && !src.moving) { ctx.moveTo(c.cx, c.cy - r); ctx.lineTo(c.cx + r, c.cy); ctx.lineTo(c.cx, c.cy + r); ctx.lineTo(c.cx - r, c.cy); ctx.closePath(); } else ctx.arc(c.cx, c.cy, r, 0, 2 * Math.PI);
        stroke(color, width);
        if (src.moving && src.pm > 0) {                       // direction of motion, east is to the left
          const ux = -src.pm_ra / src.pm, uy = -src.pm_dec / src.pm, len = r * 1.9;
          arrowPath(c.cx + ux * r, c.cy + uy * r, c.cx + ux * (r + len), c.cy + uy * (r + len), 7 * u);
          stroke(color, width);
        }
        if (chosen) label(String(src.id), c.cx + r * 0.8 + 4 * u, c.cy + r * 0.8 + 10 * u, color);
      });
    }

    // target: four ticks around its catalogue position at the date of the frame
    if ($('#chk-target').checked && state.target) {
      const t = state.target;
      const p = propagate(t.ra, t.dec, t.pmra, t.pmdec, t.epoch || 2000, year);
      const q = skyToPix(w, p.ra, p.dec);
      const c = q ? T(q.x, q.y) : null;
      if (c && inside(c)) {
        const r = Math.max(8 * u, px * 1.5), far = r + Math.max(9 * u, px * 1.4);
        ctx.beginPath();
        [[1, 0], [-1, 0], [0, 1], [0, -1]].forEach(([dx, dy]) => { ctx.moveTo(c.cx + dx * r, c.cy + dy * r); ctx.lineTo(c.cx + dx * far, c.cy + dy * far); });
        stroke(C.target, 1.6 * u);
      }
    }

    // the position being measured, when it is neither the target nor a listed source
    if (state.probe && !state.probe.isTarget && !state.selected) {
      const pr = state.probe;
      const p = propagate(pr.ra, pr.dec, pr.pmra, pr.pmdec, pr.epoch, year);
      const q = skyToPix(w, p.ra, p.dec);
      if (q) {
        const c = T(q.x, q.y);
        ctx.setLineDash([4 * u, 4 * u]);
        ctx.beginPath(); ctx.arc(c.cx, c.cy, Math.max(7 * u, px * (pr.r + 0.6)), 0, 2 * Math.PI);
        stroke(C.target, 1.3 * u);
        ctx.setLineDash([]);
      }
    }

    // scale bar and compass
    if ($('#chk-grid').checked) {
      const scale = state.result.target.scale_arcsec, fov = state.result.target.fov_arcmin;
      const barArcsec = fov >= 20 ? 300 : fov >= 10 ? 120 : fov >= 4 ? 60 : 30;
      const barPx = barArcsec / scale * px;
      const x1 = W - 16 * u, x0 = x1 - barPx, y0 = W - 16 * u;
      ctx.lineCap = 'butt';
      ctx.beginPath(); ctx.moveTo(x0, y0 - 5 * u); ctx.lineTo(x0, y0); ctx.lineTo(x1, y0); ctx.lineTo(x1, y0 - 5 * u);
      stroke(C.target, 1.5 * u);
      ctx.textAlign = 'center';
      label(barArcsec >= 60 ? `${barArcsec / 60}′` : `${barArcsec}″`, (x0 + x1) / 2, y0 - 8 * u, C.target);
      const ox = W - 22 * u, oy = 58 * u, L = 26 * u;
      arrowPath(ox, oy, ox, oy - L, 5 * u); stroke(C.target, 1.5 * u);
      arrowPath(ox, oy, ox - L, oy, 5 * u); stroke(C.target, 1.5 * u);
      label('N', ox, oy - L - 6 * u, C.target);
      ctx.textAlign = 'right';
      label('E', ox - L - 6 * u, oy + 4 * u, C.target);
      ctx.textAlign = 'left';
    }

    // the first frame of a loop carries a red corner, so the eye knows where the film restarts
    if ($('#chk-border').checked && state.sequence.length > 1 && state.seqPos === 0) {
      const R = 20 * u;
      ctx.fillStyle = FIRST_FRAME;
      ctx.beginPath(); ctx.moveTo(0, 0); ctx.lineTo(R, 0); ctx.arc(0, 0, R, 0, Math.PI / 2); ctx.closePath(); ctx.fill();
    }

    if (state.hover) {
      ctx.strokeStyle = C.hover; ctx.lineWidth = u;
      ctx.strokeRect(state.hover.x * px, (n - 1 - state.hover.y) * px, px, px);
    }
  }
  function updateTitle(frame) {
    const title = $('#frame-title'), sub = $('#frame-sub');
    $('#frame-count').textContent = state.sequence.length > 1 ? `${state.seqPos + 1} / ${state.sequence.length}` : '';
    if (!frame) {
      title.textContent = state.result ? 'No frames in this band' : '';
      sub.textContent = '';
      return;
    }
    const m = frame.meta, parts = [];
    let head;
    if (m.isSum) {
      head = m.label;
      parts.push(plural(m.n_exposures, 'exposure'));
    } else if (group() === 'exposure') {
      head = `${fmtDate(m.date)}, ${String(m.date).slice(11, 16)} UTC`;
      parts.push(`Band ${m.band[1]}`);
      if (m.wavelength) parts.push(`${m.wavelength.toFixed(3)} µm`);
    } else if (m.isDiff) {
      head = fmtDate(m.date);
      parts.push(`Epoch ${m.epoch}`);
    } else {
      head = fmtDate(m.date);
      parts.push(`Epoch ${m.epoch}`, plural(m.n_exposures, 'exposure'));
      if (m.detectors && m.detectors.length < 6) parts.push(`band${m.detectors.length > 1 ? 's' : ''} ${m.detectors.join(', ')} only`);
    }
    const follow = tracking();
    if (follow) parts.push(`following ${follow.ra}, ${follow.dec} mas/yr`);
    title.textContent = head;
    sub.textContent = parts.join(' · ');
  }
  let legendHtml = '';
  function updateLegend(frame) {
    const items = [];
    const sw = (color, text) => `<span class="key"><i class="swatch" style="background:${color}"></i>${esc(text)}</span>`;
    if (frame) {
      if (frame.meta.isDiff) items.push(sw(DIFF_KEY.brighter, 'Brighter'), sw(DIFF_KEY.fainter, 'Fainter'));
      else if (colour() && colourChannels(frame)) COLOUR_KEY.forEach(([c, text]) => items.push(sw(c, text)));
      if ($('#chk-flags').checked) {
        if (frame.flags) flagCategories().forEach((c) => items.push(sw(c.solid, c.label)));
        else if (state.result.has_flags) items.push('<span class="key">No flags on combined frames</span>');
      }
    }
    const html = items.join('');
    if (html !== legendHtml) { legendHtml = html; $('#legend').innerHTML = html; }
  }

  // ───────────────────────── contact sheet ─────────────────────────
  function showFrame(idx, b) {
    setPlaying(false);
    if (b) setRadio('band', b);
    onSelectionChange();
    let pos = state.sequence.indexOf(idx);
    if (pos < 0 && view() !== 'normal' && view() !== 'exposure') { setRadio('view', 'normal'); onSelectionChange(); pos = state.sequence.indexOf(idx); }
    state.seqPos = Math.max(0, pos);
    syncSlider(); draw();
  }
  const thumbScale = (n) => (n <= 128 ? 2 : 1);
  let sheetChoice = null, sheetAuto = false;
  try { sheetChoice = localStorage.getItem('sheet'); } catch (e) { /* storage is optional */ }
  function buildThumbs() {
    const box = $('#thumbs');
    box.innerHTML = '';
    $('#sheet').classList.toggle('hidden', !state.result);
    if (!state.result) return;
    if (!sheetChoice) {
      const want = group() === 'exposure' || state.result.epochs.length <= 6;
      if ($('#sheet').open !== want) { sheetAuto = true; $('#sheet').open = want; }
    }
    const n = state.result.target.npix;
    const cell = (tag, cls, text) => { const el = document.createElement(tag); el.className = cls; if (text) el.textContent = text; return el; };
    const f = thumbScale(n);
    const thumb = (idx, title) => {
      const b = cell('button', 'thumb'); b.type = 'button'; b.dataset.index = idx; b.title = title;
      const c = document.createElement('canvas'); c.width = n * f; c.height = n * f; b.appendChild(c);
      return b;
    };
    if (group() === 'epoch') {
      box.style.gridTemplateColumns = `auto repeat(${BAND_ORDER.length}, minmax(0, 1fr))`;
      box.appendChild(cell('span', 'thumb-head'));
      BAND_ORDER.forEach((b) => box.appendChild(cell('span', 'thumb-head', b === 'ALL' ? 'All' : b[1])));
      const dates = state.result.epochs.map((ep) => { const first = state.frames.find((x) => x.meta.epoch === ep.index); return first ? first.meta.date : ep.date_start; });
      const months = dates.map(fmtMonth), byDay = new Set(months).size < months.length;
      state.result.epochs.forEach((ep, row) => {
        box.appendChild(cell('span', 'thumb-label', byDay ? fmtDate(dates[row]) : months[row]));
        BAND_ORDER.forEach((b) => {
          const i = state.frames.findIndex((f) => f.meta.band === b && f.meta.epoch === ep.index);
          if (i < 0) { box.appendChild(cell('span', 'thumb is-missing')); return; }
          const m = state.frames[i].meta;
          const el = thumb(i, `${fmtDate(m.date)}, ${b === 'ALL' ? 'all bands' : `band ${b[1]}`}, ${plural(m.n_exposures, 'exposure')}`);
          el.addEventListener('click', () => showFrame(i, b));
          box.appendChild(el);
        });
      });
    } else {
      box.style.gridTemplateColumns = 'repeat(auto-fill, minmax(64px, 1fr))';
      state.sequence.slice(0, 160).forEach((idx) => {
        const m = state.frames[idx].meta;
        const el = thumb(idx, `${fmtDate(m.date)}, ${String(m.date).slice(11, 16)} UTC, band ${m.band[1]}${m.wavelength ? `, ${m.wavelength.toFixed(3)} µm` : ''}`);
        el.addEventListener('click', () => showFrame(idx, null));
        box.appendChild(el);
      });
    }
    const shown = $$('#thumbs .thumb').length;
    $('#sheet > summary').textContent = group() === 'exposure' && state.sequence.length > shown ? `First ${shown} of ${state.sequence.length} frames` : 'All frames';
    paintThumbs();
  }
  function paintThumbs() {
    if (!state.result) return;
    const n = state.result.target.npix, inv = inverted();
    const table = lut($('#cmap').value, inv), blank = inv ? BLANK_LIGHT : BLANK_DARK;
    const cache = {}, k = thumbScale(n);
    $$('#thumbs .thumb canvas').forEach((c) => {
      const f = state.frames[parseInt(c.parentElement.dataset.index, 10)];
      if (!f) return;
      const key = group() === 'exposure' ? 'x' : f.meta.band;
      if (!cache[key]) cache[key] = bandLevels(f.meta.band);
      const values = k > 1 ? upsample(f.data, n, k) : f.data;
      c.getContext('2d').putImageData(renderImage(values, n * k, cache[key].lo, cache[key].hi, $('#stretch').value, table, blank), 0, 0);
    });
    highlightThumb();
  }
  function highlightThumb() {
    const idx = state.sequence[state.seqPos];
    $$('#thumbs .thumb').forEach((el) => el.classList.toggle('active', String(idx) === el.dataset.index));
  }

  // ───────────────────────── player ─────────────────────────
  function syncSlider() { $('#frame-slider').value = state.seqPos; }
  function step(delta) {
    if (!state.sequence.length) return;
    state.seqPos = (state.seqPos + delta + state.sequence.length) % state.sequence.length;
    syncSlider(); draw();
  }
  function setPlaying(on) {
    state.playing = Boolean(on) && state.sequence.length > 1;
    const button = $('#btn-play');
    button.classList.toggle('is-playing', state.playing);
    button.setAttribute('aria-label', state.playing ? 'Pause' : 'Play');
    if (state.timer) { clearInterval(state.timer); state.timer = null; }
    if (state.playing) state.timer = setInterval(() => step(1), 1000 / parseFloat($('#fps').value));
  }

  // ───────────────────────── API ─────────────────────────
  async function api(path, opts) {
    const r = await fetch(path, opts);
    if (!r.ok) {
      let detail = r.statusText, fromApp = false;
      try { const j = await r.json(); fromApp = j && 'detail' in j; detail = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail || j); } catch (e) { /* not JSON */ }
      const error = new Error(detail || `HTTP ${r.status}`);
      error.status = r.status;
      error.fromApp = fromApp;
      throw error;
    }
    return r.json();
  }
  /**
   * A request worth repeating: the network dropped, or a proxy in front of the server answered for it.
   * A 502 from the server itself (an archive or name service it already asked several times) is final.
   */
  const passing = (e) => !e.status || [408, 429].includes(e.status) || ([502, 503, 504].includes(e.status) && !e.fromApp);
  async function patient(call, tries = 5) {
    for (let attempt = 1; ; attempt++) {
      try { return await call(); } catch (e) {
        if (attempt >= tries || !passing(e)) throw e;
        await sleep(600 * attempt);
      }
    }
  }
  /**
   * Starts a job and waits for its result. A dropped connection while waiting is ridden out, and
   * a job the server no longer knows (it restarted) is started again, so a long load survives both.
   */
  async function runJob(kind, body, onProgress, alive) {
    const start = () => patient(() => api(`/api/jobs/${kind}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }), 3);
    let st = await start(), restarts = 0;
    for (;;) {
      if (alive && !alive()) throw new Error('cancelled');
      if (st.state === 'error') throw new Error(st.error || 'the job failed');
      try {
        if (st.state === 'done') return { result: await patient(() => api(`/api/jobs/${st.job_id}/result`)), job: st };
        onProgress(st);
        await sleep(700);
        st = await patient(() => api(`/api/jobs/${st.job_id}`));
      } catch (e) {
        if (e.status !== 404 || restarts >= 2) throw e;
        restarts++;
        st = await start();
      }
    }
  }

  // ───────────────────────── target ─────────────────────────
  function showWorkspace() {
    if (!document.body.classList.contains('is-start')) return;
    document.body.classList.replace('is-start', 'is-working');
    $('#header-search-slot').appendChild($('#search-box'));
    window.scrollTo(0, 0);
  }

  // ───────────────────────── search suggestions ─────────────────────────
  // The five test targets and the visitor's recent searches, listed under the
  // search box when it is focused and filtered as they type.
  const SUGGESTED = [
    { id: 'barnard', name: 'Barnard’s Star', note: 'Fastest-moving star', fov: 8, keys: 'gj 699 v2500 oph' },
    { id: 'epsindi-b', name: 'Epsilon Indi B', note: 'Brown-dwarf pair', fov: 15, keys: 'eps ind b' },
    { id: 'wise0855', name: 'WISE 0855−0714', note: 'Coldest brown dwarf', fov: 8, keys: 'w0855 wise j085510' },
    { id: 'rwaur', name: 'RW Aurigae', note: 'Young variable star', fov: 8, keys: 'rw aur' },
    { id: 'rdra', name: 'R Draconis', note: 'Pulsating red giant', fov: 8, keys: 'r dra' },
  ];
  const RECENT_KEY = 'recent-targets';
  const searchInput = $('#target-input'), suggestList = $('#target-suggest');
  let suggestItems = [], suggestActive = -1;
  /** Text reduced to lower-case letters and digits, for matching "rw aur" against "RW Aurigae". */
  const fold = (s) => String(s || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/[^a-z0-9]+/g, '');
  const looksLikePosition = (q) => /\d/.test(q) && /^[\d\s.,:;+\-−hmsd°'"′″]+$/i.test(q);
  function readRecent() {
    try {
      const list = JSON.parse(localStorage.getItem(RECENT_KEY) || '[]');
      return Array.isArray(list) ? list.filter((r) => r && typeof r.q === 'string' && typeof r.label === 'string') : [];
    } catch (e) { return []; }
  }
  function rememberTarget(t, query) {
    if (t.source === 'preset') return;                     // the test targets are always listed anyway
    const label = t.source === 'coordinates' ? query : pretty(t.name);
    const list = [{ q: query, label }, ...readRecent().filter((r) => fold(r.label) !== fold(label))].slice(0, 5);
    try { localStorage.setItem(RECENT_KEY, JSON.stringify(list)); } catch (e) { /* storage is optional */ }
  }
  function setSuggestOpen(open) {
    suggestList.classList.toggle('hidden', !open);
    searchInput.setAttribute('aria-expanded', String(open));
    if (!open) markSuggest(-1);
  }
  function markSuggest(i) {
    suggestActive = i;
    $$('#target-suggest [role=option]').forEach((el, k) => el.setAttribute('aria-selected', String(k === i)));
    const el = i >= 0 ? $(`#suggest-${i}`) : null;
    if (el) { searchInput.setAttribute('aria-activedescendant', el.id); el.scrollIntoView({ block: 'nearest' }); }
    else searchInput.removeAttribute('aria-activedescendant');
  }
  function renderSuggest() {
    const q = searchInput.value.trim(), key = fold(q);
    const hit = (...texts) => !key || texts.some((s) => fold(s).includes(key));
    const recent = readRecent().filter((r) => hit(r.label, r.q)).slice(0, key ? 3 : 4);
    const targets = SUGGESTED.filter((t) => hit(t.name, t.note, t.id, t.keys));
    let html = '';
    suggestItems = [];
    const add = (item, inner) => {
      const i = suggestItems.push(item) - 1;
      html += `<li id="suggest-${i}" class="suggest-item" role="option" aria-selected="false" data-i="${i}">${inner}</li>`;
    };
    const group = (label) => { html += `<li class="suggest-group" role="presentation">${label}</li>`; };
    if (q) add({ kind: 'query' }, `<span class="suggest-name"><span class="suggest-verb">${looksLikePosition(q) ? 'Go to' : 'Search'}</span> ${esc(q)}</span>`);
    if (recent.length) {
      group('Recent');
      recent.forEach((r) => add({ kind: 'recent', q: r.q, label: r.label }, `<span class="suggest-name">${esc(r.label)}</span>`));
    }
    if (targets.length) {
      group('Test targets');
      targets.forEach((t) => add({ kind: 'preset', ...t }, `<span class="suggest-name">${esc(t.name)}</span><span class="suggest-note">${esc(t.note)}</span>`));
    }
    suggestList.innerHTML = html;
    const room = window.innerHeight - $('#target-form').getBoundingClientRect().bottom - 24;   // keep the list inside the window
    suggestList.style.maxHeight = `${Math.round(Math.max(180, Math.min(420, room)))}px`;
    setSuggestOpen(suggestItems.length > 0);
  }
  function pickSuggest(i) {
    const item = suggestItems[i];
    if (!item) return;
    setSuggestOpen(false);
    if (item.kind === 'query') { $('#target-form').requestSubmit(); return; }
    searchInput.blur();
    if (item.kind === 'preset') { searchInput.value = item.name; openTarget(item.id, item.fov); }
    else { searchInput.value = item.label; openTarget(item.q); }
  }
  searchInput.addEventListener('focus', renderSuggest);
  searchInput.addEventListener('input', renderSuggest);
  searchInput.addEventListener('click', () => { if (suggestList.classList.contains('hidden')) renderSuggest(); });
  searchInput.addEventListener('blur', () => setSuggestOpen(false));
  searchInput.addEventListener('keydown', (ev) => {
    if (ev.isComposing) return;
    const open = !suggestList.classList.contains('hidden');
    if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') {
      ev.preventDefault();
      if (!open) { renderSuggest(); return; }
      const n = suggestItems.length, d = ev.key === 'ArrowDown' ? 1 : -1;
      if (n) markSuggest(suggestActive < 0 ? (d > 0 ? 0 : n - 1) : (suggestActive + d + n) % n);
    } else if (ev.key === 'Enter' && open && suggestActive >= 0) {
      ev.preventDefault(); pickSuggest(suggestActive);
    } else if (ev.key === 'Escape' && open) {
      ev.preventDefault(); setSuggestOpen(false);
    }
  });
  suggestList.addEventListener('mousedown', (ev) => ev.preventDefault());      // keep the focus in the box
  suggestList.addEventListener('mousemove', (ev) => {
    const li = ev.target.closest('[data-i]');
    if (li && Number(li.dataset.i) !== suggestActive) markSuggest(Number(li.dataset.i));
  });
  suggestList.addEventListener('click', (ev) => { const li = ev.target.closest('[data-i]'); if (li) pickSuggest(Number(li.dataset.i)); });
  function renderTarget() {
    const t = state.target, box = $('#target-info'), about = $('#target-about');
    $('#target-block').classList.toggle('hidden', !t);
    if (!t) { box.innerHTML = ''; about.innerHTML = ''; return; }
    const byPosition = t.source === 'coordinates';
    const name = byPosition ? shortCoord(t.ra, t.dec) : pretty(t.name);
    const type = t.source === 'preset' ? t.type : (OTYPES[t.type] || '');
    const facts = [];
    if (!byPosition) facts.push(['Position now', shortCoord(t.ra_now, t.dec_now)]);
    facts.push(['Position, J2000', shortCoord(t.ra, t.dec)]);
    const pm = Math.hypot(t.pmra || 0, t.pmdec || 0) / 1000;
    if (pm >= 0.005) {
      facts.push(['Proper motion', `${pm.toFixed(2)}″/yr`]);
      facts.push(['In RA, Dec', `${Math.round(t.pmra || 0)}, ${Math.round(t.pmdec || 0)} mas/yr`]);
    }
    const r = state.result;
    if (r) {
      const eps = r.epochs, first = eps[0], last = eps[eps.length - 1];
      facts.push([eps.length > 1 ? `${eps.length} epochs` : 'Epoch', eps.length > 1 ? `${fmtMonth(first.date_start)} to ${fmtMonth(last.date_end)}` : fmtMonth(first.date_start)]);
      facts.push(['Exposures', r.subsampled ? `${r.n_used} of ${r.n_found}` : String(r.n_used)]);
    }
    const was = t.passing;
    const moved = was ? (was.moved_arcsec >= 90 ? `${(was.moved_arcsec / 60).toFixed(1)}′` : `${Math.round(was.moved_arcsec)}″`) : '';
    box.innerHTML = `<h1 class="target-name">${esc(name)}</h1>`
      + (was ? `<p class="target-note"><span>${esc(pretty(was.name))} was here in ${was.year}. It is now ${moved} to the ${esc(was.direction)}.</span>`
        + `<button type="button" data-follow="${esc(was.id)}" data-fov="${was.fov}">Follow it</button></p>` : '');
    // The description, the facts and the comparison stay folded under "Details".
    about.innerHTML = (type ? `<p class="target-type">${esc(curly(sentence(type)))}</p>` : '')
      + `<dl class="facts">${facts.map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join('')}</dl>`
      + (t.hint ? `<p class="target-hint">${esc(curly(t.hint))}</p>` : '');
    const follow = box.querySelector('[data-follow]');
    if (follow) follow.addEventListener('click', () => openTarget(follow.dataset.follow, parseFloat(follow.dataset.fov)));
  }
  function applyParams(p) {
    if (p.get('fov')) ensureFov(parseFloat(p.get('fov')));
    const b = String(p.get('band') || '').toUpperCase();
    if (BAND_ORDER.includes(b) || b === 'RGB') setRadio('band', b);
    if (p.get('seq') === 'exposure') setRadio('view', 'exposure');
    else if (['blink', 'diff', 'sum'].includes(p.get('mode'))) setRadio('view', p.get('mode'));
    const fps = p.get('fps');
    if (fps && Array.from($('#fps').options).some((o) => o.value === fps)) $('#fps').value = fps;
    [['invert', '#chk-invert'], ['gaia', '#chk-gaia'], ['catwise', '#chk-catwise'], ['flags', '#chk-flags']].forEach(([key, sel]) => {
      if (p.get(key) !== null) $(sel).checked = p.get(key) === '1';
    });
    if (p.get('pmra') !== null) $('#pmra').value = p.get('pmra');
    if (p.get('pmdec') !== null) $('#pmdec').value = p.get('pmdec');
    if (p.get('track') === '1') { $('#chk-track').checked = true; $('#track').open = true; $('#track').classList.add('is-on'); }
    if ($('#chk-flags').checked || $('#chk-track').checked) $('#more-overlays').open = true;
  }
  function ensureFov(value) {
    if (!Number.isFinite(value) || value <= 0) return;
    const max = (state.config && state.config.max_fov_arcmin) || 40;
    const v = Math.min(max, Math.max(2, Math.round(value * 10) / 10));
    const sel = $('#fov');
    if (!Array.from(sel.options).some((o) => parseFloat(o.value) === v)) {
      const opt = document.createElement('option'); opt.value = String(v); opt.textContent = `${v}′`;
      const after = Array.from(sel.options).find((o) => parseFloat(o.value) > v);
      sel.insertBefore(opt, after || null);
    }
    sel.value = String(v);
    renderFov();
  }
  /** The band menu: the same kind of list, kept in step with the hidden band radios. */
  function renderBand() {
    const name = (v) => (v === 'ALL' ? 'All' : v === 'RGB' ? 'Colour' : `Band ${v.slice(1)}`);
    $('#band-value').textContent = name(bandChoice());
    $('#band-list').replaceChildren(...Array.from(document.querySelectorAll('input[name=band]')).map((el) => {
      const b = document.createElement('button');
      b.type = 'button'; b.disabled = el.disabled;
      const label = document.createElement('span'); label.textContent = name(el.value);
      const range = document.createElement('small'); range.textContent = el.value === 'RGB' ? 'three colours' : `${BAND_RANGES[el.value]} µm`;
      b.append(label, range);
      if (el.value === 'RGB') b.title = 'Bands 1–2 in blue, 3–4 in green, 5–6 in red. Cold objects come out red';
      if (el.checked) b.setAttribute('aria-current', 'true');
      b.addEventListener('click', () => {
        $('#band-menu').open = false;
        if (el.checked) return;
        el.checked = true;
        el.dispatchEvent(new Event('change'));
      });
      return b;
    }));
  }
  /** The field menu: a list in the platform's own style, kept in step with the hidden #fov select. */
  function renderFov() {
    const sel = $('#fov'), list = $('#fov-list');
    $('#fov-value').textContent = sel.selectedOptions[0] ? sel.selectedOptions[0].textContent : '';
    list.replaceChildren(...Array.from(sel.options).map((o) => {
      const b = document.createElement('button');
      b.type = 'button'; b.textContent = o.textContent;
      if (o.value === sel.value) b.setAttribute('aria-current', 'true');
      b.addEventListener('click', () => {
        $('#fov-menu').open = false;
        if (sel.value === o.value) return;
        sel.value = o.value; renderFov();
        sel.dispatchEvent(new Event('change'));
      });
      return b;
    }));
  }
  async function resolveTarget(query) {
    clearError();
    const form = $('#target-form');
    form.classList.add('is-busy');
    try {
      const t = await patient(() => api(`/api/resolve?q=${encodeURIComponent(query)}`), 3);
      state.target = t;
      $('#target-input').value = t.source === 'preset' ? pretty(t.name) : query;
      rememberTarget(t, query);
      document.title = `${t.source === 'coordinates' ? shortCoord(t.ra, t.dec) : pretty(t.name)} · BeyondtheBlue`;
      return t;
    } catch (e) {
      showError(/^Could not resolve/.test(e.message) ? `Nothing found for “${query}”. Try another name, or coordinates.` : plainError(e.message));
      return null;
    } finally {
      form.classList.remove('is-busy');
    }
  }
  async function openTarget(query, fov) {
    const t = await resolveTarget(query);
    if (!t) return;
    if (fov) ensureFov(fov);
    showWorkspace();
    loadFrames();
  }

  // ───────────────────────── frames ─────────────────────────
  /** Empties the workspace before a different field is loaded. */
  function resetField() {
    state.result = null; state.frames = []; state.wcs = null; state.jobId = null;
    state.sequence = []; state.seqPos = 0;
    state.selected = null; state.probe = null; state.spectrum = null; state.fullRange = false;
    state.hiddenEpochs.clear(); state.hiddenBands.clear();
    state.catalogs = { gaia: null, catwise: null };
    spectrumToken++;
    clearDerived();
    $('#canvas-wrap').classList.add('is-empty');
    $('#readout').textContent = '';
    $('#thumbs').innerHTML = ''; $('#sheet').classList.add('hidden');
    state.changesOpen = false;
    $('#changes-list').innerHTML = ''; $('#changes-summary').textContent = '';
    $('#sources-table tbody').innerHTML = ''; $('#sources-info').textContent = '';
    $('#exposures-table tbody').innerHTML = ''; $('#exposures-info').textContent = '';
    ['spectrum', 'lightcurve'].forEach((name) => {
      $(`#${name}-info`).innerHTML = ''; $(`#${name}-key`).innerHTML = '';
      const el = $(`#${name}-plot`);
      if (typeof Plotly !== 'undefined' && el.data) Plotly.purge(el);
    });
    $('#spectrum-note').textContent = '';
    ['#btn-fits', '#btn-png', '#btn-video'].forEach((sel) => { $(sel).disabled = true; });
    buildSequence();
    syncAvailability();
  }
  /**
   * Loads the frames of the field on show. With *live*, it swaps a saved copy for the frames the
   * server has just read from IRSA, without stopping the film or flashing the progress bar.
   */
  async function loadFrames(live = false) {
    if (!state.target) return;
    clearError();
    const token = ++state.loadToken;
    const wasPlaying = state.playing;
    if (!live) setPlaying(false);
    const t = state.target, kind = seqKind();
    const body = { ra: t.ra_now, dec: t.dec_now, fov: parseFloat($('#fov').value), group: kind,
      detector: kind === 'exposure' ? band() : 'ALL', flags: $('#chk-flags').checked };
    const shown = state.result && state.result.target;
    const sameField = Boolean(shown) && Math.abs(shown.ra - body.ra) < 2e-6 && Math.abs(shown.dec - body.dec) < 2e-6
      && Math.abs(shown.fov_arcmin - body.fov) < 1e-3;
    if (!sameField) resetField();
    renderTarget();
    updateUrl();
    draw();
    if (!live) setProgress(true, 0, 'Searching the archive');
    try {
      const began = Date.now();
      const { result, job } = await runJob('frames', body, (st) => {
        if (live) return;
        const waiting = /^searching/.test(st.message || '') && Date.now() - began > 12000;
        setProgress(true, st.progress, waiting ? 'The archive is slow to answer. Still searching' : statusText(st.message));
      }, () => token === state.loadToken);
      if (token !== state.loadToken) return;
      const regrouped = !state.result || state.result.params.group !== result.params.group;
      state.result = result; state.jobId = job.job_id;
      state.frames = result.frames.map(decodeFrame);
      state.wcs = result.target.wcs;
      clearDerived();
      if (!sameField || regrouped) state.seqPos = 0;
      $('#canvas-wrap').classList.remove('is-empty');
      setProgress(false);
      $('#saved-note').textContent = result.snapshot ? `. Copy saved on ${fmtDate(result.snapshot.saved)}; the latest is being read.` : '';
      populateEpochSelects();
      syncAvailability();
      onSelectionChange();
      buildThumbs();
      fillExposures(result);
      renderTarget();
      renderChanges();
      renderSources();
      ['#btn-fits', '#btn-png', '#btn-video'].forEach((sel) => { $(sel).disabled = false; });
      const notes = (result.warnings || []).slice();
      if (result.n_errors) notes.push(`${plural(result.n_errors, 'exposure')} could not be read.`);
      if (notes.length) showError(notes.join('\n'), Boolean(result.warnings && result.warnings.length));
      draw();
      loadCatalog('gaia');
      if ($('#chk-catwise').checked) loadCatalog('catwise');
      updateContext();
      if (!sameField && result.analysis && result.analysis.sources && group() === 'epoch') {
        const own = result.analysis.sources.find((src) => { const id = identify(src); return id && id.kind === 'target'; });
        if (own && own.saturated && own.curve && Object.keys(own.curve).length) { state.selected = own; $('#lc-kind').value = 'epoch'; renderChanges(); renderSources(); }
      }
      if (!sameField || !state.spectrum) measureAt(t.ra, t.dec, { pmra: t.pmra, pmdec: t.pmdec, epoch: t.epoch || 2000, name: t.source === 'coordinates' ? null : pretty(t.name), isTarget: true }, false);
      else if (live && state.spectrum.snapshot && state.probe) {         // the saved spectrum goes with the saved frames
        const p = state.probe;
        measureAt(p.ra, p.dec, { pmra: p.pmra, pmdec: p.pmdec, epoch: p.epoch, name: p.name, isTarget: p.isTarget }, false);
      }
      const calm = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      if (live ? wasPlaying : group() === 'epoch' && state.sequence.length > 1 && !calm) setPlaying(true);
      if (result.snapshot) followLive(token, body);
    } catch (e) {
      if (e.message === 'cancelled' || token !== state.loadToken) return;
      setProgress(false);
      if (!live) showError(plainError(e.message), true);              // a failed swap leaves the saved copy on show
    }
  }
  /**
   * A test target opens on its saved copy while the server reads the same field from IRSA. That
   * reading is shown over the image as it goes, and its frames take the copy's place when it is done.
   */
  async function followLive(token, body) {
    for (;;) {
      await sleep(1000);
      if (token !== state.loadToken) return;
      let st;
      try {
        st = await api('/api/live', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      } catch (e) {
        st = { state: 'failed' };
      }
      if (token !== state.loadToken) return;
      if (st.state === 'reading') {
        const doing = statusText(st.message).replace(/ from IRSA$/, '').replace(/ the IRSA archive$/, ' the archive');
        setProgress(true, st.progress, `Live from IRSA · ${doing}`);
        continue;
      }
      if (st.state === 'ready') { loadFrames(true); return; }
      setProgress(false);
      return;
    }
  }
  function populateEpochSelects() {
    const eps = state.result.epochs.filter((e) => state.frames.some((f) => f.meta.epoch === e.index));
    if (!eps.length) return;
    const keepRef = $('#ref-epoch').value, keepBlink = $('#blink-b').value;
    const opts = eps.map((e) => `<option value="${e.index}">${esc(epochDate(e.index))}</option>`).join('');
    $('#ref-epoch').innerHTML = opts; $('#blink-b').innerHTML = opts;
    $('#ref-epoch').value = eps.some((e) => String(e.index) === keepRef) ? keepRef : String(eps[0].index);
    $('#blink-b').value = eps.some((e) => String(e.index) === keepBlink) ? keepBlink : String(eps[eps.length - 1].index);
  }
  /** Views that need two epochs, and bands with no frames, cannot be chosen. */
  function syncAvailability() {
    const r = state.result;
    const epochs = r ? new Set(state.frames.map((f) => f.meta.epoch)).size : 2;
    const single = Boolean(r) && group() === 'epoch' && epochs < 2;
    $$('input[name=view]').forEach((el) => { el.disabled = single && ['blink', 'diff', 'sum'].includes(el.value); });
    if (single && ['blink', 'diff', 'sum'].includes(view())) setRadio('view', 'normal');
    $$('input[name=band]').forEach((el) => {
      if (el.value === 'RGB') el.disabled = Boolean(r) && (group() !== 'epoch' || !COLOUR_PAIRS.every((pair) => pair.some((b) => state.frames.some((f) => f.meta.band === b))));
      else el.disabled = Boolean(r) && group() === 'epoch' && !state.frames.some((f) => f.meta.band === el.value);
    });
    if (document.querySelector('input[name=band]:checked:disabled')) { const all = $('input[name=band][value=ALL]'); all.disabled = false; all.checked = true; }
    renderBand();
  }
  function onSelectionChange() {
    const m = mode();
    $('#ref-wrap').classList.toggle('hidden', !(m === 'diff' || m === 'blink') || !state.result);
    $('#blink-wrap').classList.toggle('hidden', m !== 'blink' || !state.result);
    $('#ref-label').textContent = m === 'blink' ? 'Blink' : 'Minus';
    renderBand();
    state.levelCache.clear();
    buildSequence();
    if (state.result && group() === 'exposure') buildThumbs();
    updateUrl();
  }

  // ───────────────────────── catalogues ─────────────────────────
  async function loadCatalog(name) {
    if (!state.result || state.catalogs[name]) return;
    const t = state.result.target;
    const stamp = `${t.ra}|${t.dec}|${t.fov_arcmin}`;
    try {
      const radius = Math.min(0.6, t.fov_arcmin / 60 * 0.75);
      const res = await patient(() => api(`/api/catalog/${name}?ra=${t.ra}&dec=${t.dec}&radius=${radius.toFixed(4)}`), 2);
      if (!state.result || `${state.result.target.ra}|${state.result.target.dec}|${state.result.target.fov_arcmin}` !== stamp) return;
      state.catalogs[name] = Array.isArray(res.sources) ? res.sources : [];
      renderChanges(); renderSources(); draw();
    } catch (e) {
      if ($(`#chk-${name}`).checked) showError(`${name === 'gaia' ? 'Gaia DR3' : 'CatWISE'} is not answering right now.`);
    }
  }
  /** What the catalogues know about a measured source. */
  function identify(src) {
    const w = state.wcs;
    const near = (ra, dec, pmra, pmdec, epoch) => {
      const p = propagate(ra, dec, pmra, pmdec, epoch, src.year);
      const q = skyToPix(w, p.ra, p.dec);
      return q ? Math.hypot(q.x - src.x, q.y - src.y) : Infinity;
    };
    const describe = (name, pmra, pmdec) => {
      const pm = (pmra || pmdec) ? Math.hypot(pmra || 0, pmdec || 0) / 1000 : null;
      return { label: name, pm, pa: pm ? ((Math.atan2(pmra || 0, pmdec || 0) * R2D) + 360) % 360 : null };
    };
    const t = state.target;
    if (t && t.source !== 'coordinates' && near(t.ra, t.dec, t.pmra, t.pmdec, t.epoch || 2000) < 2.5) return { ...describe(pretty(t.name), t.pmra, t.pmdec), kind: 'target' };
    for (const name of ['gaia', 'catwise']) {
      const rows = state.catalogs[name];
      if (!rows) continue;
      let best = null, bestDist = 1.8;
      rows.forEach((s) => { const d = near(s.ra, s.dec, s.pmra, s.pmdec, s.epoch); if (d < bestDist) { bestDist = d; best = s; } });
      if (best) return { ...describe(best.id, best.pmra, best.pmdec), kind: name, mag: best.mag, magBand: best.mag_band };
    }
    return null;
  }

  // ───────────────────────── what changed ─────────────────────────
  function renderChanges() {
    const list = $('#changes-list'), summary = $('#changes-summary');
    const note = (text) => `<p class="note">${esc(text)}</p>`;
    summary.textContent = '';
    list.innerHTML = '';
    if (!state.result) return;
    if (group() === 'exposure') { list.innerHTML = note('Epochs are compared in the Epochs view.'); return; }
    const a = state.result.analysis;
    if (!a) { list.innerHTML = note('One visit so far. The next one arrives within six months.'); return; }
    if (a.error) { list.innerHTML = note('The comparison could not be made. The frames are not affected.'); return; }
    summary.textContent = `${plural(a.n_sources, 'source')} over ${a.baseline_years.toFixed(2)} yr`;
    const found = a.sources.filter((s) => s.moving || s.variable);
    const t = state.target;
    const catalogPm = t ? Math.hypot(t.pmra || 0, t.pmdec || 0) / 1000 : 0;
    let html = '';
    if (t && catalogPm > 0.3) {                 // a fast target that the comparison did not pick up deserves a word
      const atTarget = a.sources.filter((s) => { const id = identify(s); return id && id.kind === 'target'; });
      const follows = atTarget.some((s) => s.moving || Math.abs(s.pm - catalogPm) <= 3 * Math.max(s.pm_err, 0.1));
      const name = pretty(t.name);
      if (!atTarget.length) html += note(`${name} is too faint to be measured here. Try band 5 or 6.`);
      else if (!follows) html += note(`The source at the position of ${name} is not moving with it (${atTarget[0].pm.toFixed(1)} against ${catalogPm.toFixed(1)}″/yr). Probably a background star.`);
    }
    if (!found.length) {
      const scale = state.result.target.scale_arcsec;
      const limit = 5 * 1.5 * a.position_scatter_px * scale / Math.max(a.baseline_years, 0.1);
      list.innerHTML = html + note(`Nothing moved or varied. A motion above ${limit.toFixed(1)}″/yr would show.`);
      return;
    }
    list.innerHTML = html;
    const LIMIT = 3;
    const chosen = state.selected ? found.findIndex((x) => x.id === state.selected.id) : -1;
    const all = state.changesOpen || found.length <= LIMIT + 1 || chosen >= LIMIT;
    (all ? found : found.slice(0, LIMIT)).forEach((src) => {
      const id = identify(src);
      const row = document.createElement('button');
      row.type = 'button';
      row.className = `change${state.selected && state.selected.id === src.id ? ' selected' : ''}`;
      row.dataset.id = src.id;
      const second = [src.moving && src.variable ? 'Moving, variable' : src.moving ? 'Moving' : 'Variable'];
      if (src.moving && id && id.pm) second.push(`catalogue ${id.pm.toFixed(2)}″/yr`);
      if (src.variable) { const how = describeVariation(src); if (how) second.push(how); }
      if (src.saturated) second.push('saturated');
      if (!id && state.catalogs.gaia) second.push('not in Gaia DR3');
      let value, extra;
      if (src.moving) { value = `${src.pm.toFixed(2)} ± ${src.pm_err.toFixed(2)}″/yr`; extra = `towards ${Math.round(src.pm_pa)}°`; }
      else { value = `${(src.var_amp * 100).toFixed(0)} %`; extra = 'change'; }
      row.innerHTML = `<span class="key">${src.moving ? GLYPH.move : GLYPH.vary}</span>`
        + `<span class="what"><b>${esc(id ? pretty(id.label) : shortCoord(src.ra, src.dec))}</b><small>${esc(second.join(' · '))}</small></span>`
        + `<span class="value">${esc(value)}<small>${esc(extra)}</small></span>`;
      row.addEventListener('click', () => selectSource(src));
      list.appendChild(row);
    });
    if (found.length > LIMIT + 1 && chosen < LIMIT) {
      const more = document.createElement('button');
      more.type = 'button'; more.className = 'changes-more';
      more.setAttribute('aria-expanded', String(all));
      more.textContent = all ? 'Show fewer' : `Show all ${found.length}`;
      more.addEventListener('click', () => { state.changesOpen = !all; renderChanges(); });
      list.appendChild(more);
    }
  }
  /** "brighter in bands 1, 2, fainter in 5, 6" for a variable source. */
  function describeVariation(src) {
    const changing = (src.bands || []).filter((b) => b.varying);
    const list = (items) => items.map((b) => b.band[1]).join(', ');
    const up = changing.filter((b) => b.trend !== null && b.trend > 0.05);
    const down = changing.filter((b) => b.trend !== null && b.trend < -0.05);
    if (src.colour_change && up.length && down.length) return `brighter in band${up.length > 1 ? 's' : ''} ${list(up)}, fainter in ${list(down)}`;
    return changing.length ? `band${changing.length > 1 ? 's' : ''} ${list(changing)}` : '';
  }
  function renderSources() {
    const tb = $('#sources-table tbody');
    tb.innerHTML = '';
    const a = state.result && state.result.analysis;
    if (!a || !a.sources || group() === 'exposure') { $('#sources-info').textContent = state.result ? 'Sources are measured when there are two epochs or more.' : ''; return; }
    $('#sources-info').textContent = '';
    a.sources.forEach((src) => {
      const tr = document.createElement('tr');
      tr.className = `clickable${state.selected && state.selected.id === src.id ? ' selected' : ''}`;
      tr.dataset.id = src.id;
      const notes = [src.moving ? 'moving' : '', src.variable ? 'variable' : '', src.saturated ? 'saturated' : '', src.blended ? 'blended' : '', src.detected_in === 'red' ? 'red' : ''].filter(Boolean).join(', ');
      const id = identify(src);
      if (id) tr.title = pretty(id.label);
      tr.innerHTML = `<td>${src.id}</td><td>${shortCoord(src.ra, src.dec)}</td><td class="num">${src.flux_mjy.toPrecision(3)}</td>`
        + `<td class="num">${src.pm.toFixed(2)} ± ${src.pm_err.toFixed(2)}</td><td class="num">${src.motion_sig.toFixed(1)}</td>`
        + `<td class="num">${(src.var_amp * 100).toFixed(0)} %</td><td>${esc(notes)}</td>`;
      tr.addEventListener('click', () => selectSource(src));
      tb.appendChild(tr);
    });
  }
  function selectSource(src) {
    state.selected = src;
    $$('#changes-list .change').forEach((el) => el.classList.toggle('selected', el.dataset.id === String(src.id)));
    $$('#sources-table tbody tr').forEach((el) => el.classList.toggle('selected', el.dataset.id === String(src.id)));
    draw();
    if (src.saturated && src.curve && Object.keys(src.curve).length) $('#lc-kind').value = 'epoch';
    const id = identify(src);
    measureAt(src.ra, src.dec, { pmra: src.moving ? src.pm_ra * 1000 : 0, pmdec: src.moving ? src.pm_dec * 1000 : 0, epoch: src.year,
      name: id ? pretty(id.label) : `Source ${src.id}` }, true);
    if (src.saturated && src.variable) activateTab('lightcurve');
    plotLightcurve(state.spectrum);
  }

  // ───────────────────────── spectrum and light curve ─────────────────────────
  let plotQueue = [];
  /** Runs `fn` once the plotting library is there; the page does not wait for it to start. */
  function whenPlotly(fn) { if (typeof Plotly !== 'undefined') fn(); else plotQueue.push(fn); }
  const plotlyTag = document.querySelector('script[data-plotly]');
  if (plotlyTag) {
    plotlyTag.addEventListener('load', () => { const queue = plotQueue; plotQueue = []; queue.forEach((fn) => fn()); });
    plotlyTag.addEventListener('error', () => { $('#spectrum-note').textContent = 'The plots could not be loaded.'; });
  }
  const PLOT_CONFIG = { responsive: true, displayModeBar: false, doubleClick: 'reset' };
  function paneTitle(sel, name, detail) {
    $(sel).innerHTML = `${esc(name)}${detail ? `<small>${esc(detail)}</small>` : ''}`;
  }
  let spectrumToken = 0;
  async function measureAt(ra, dec, pm, focus = true) {
    if (!state.result) return;
    const token = ++spectrumToken;
    const r = parseFloat($('#aperture').value);
    state.probe = { ra, dec, r, pmra: pm.pmra || 0, pmdec: pm.pmdec || 0, epoch: pm.epoch || 2000, name: pm.name || null, isTarget: Boolean(pm.isTarget) };
    state.fullRange = false;
    paint();
    const who = pm.name || shortCoord(ra, dec);
    paneTitle('#spectrum-info', who, 'Measuring');
    paneTitle('#lightcurve-info', who, '');
    if (focus && !['spectrum', 'lightcurve'].includes(activeTab())) activateTab('spectrum');
    try {
      const t = state.result.target;
      const body = { ra, dec, pmra: pm.pmra || 0, pmdec: pm.pmdec || 0, epoch: pm.epoch || 2000, r_ap: r,
        field: { ra: t.ra, dec: t.dec, fov: t.fov_arcmin } };
      const { result } = await runJob('spectrum', body, (st) => { if (token === spectrumToken) paneTitle('#spectrum-info', who, statusText(st.message)); },
        () => token === spectrumToken);
      if (token !== spectrumToken) return;
      state.spectrum = result;
      result.who = who;
      plotSpectrum(result); plotLightcurve(result);
    } catch (e) {
      if (e.message !== 'cancelled' && token === spectrumToken) paneTitle('#spectrum-info', who, 'This position could not be measured');
    }
  }
  function epochColors(n) {
    if (n <= 1) return ['#1a40bf'];
    if (n === 2) return ['#1a40bf', EPOCH_LAST];
    if (n === 3) return ['#5b8fee', '#1a40bf', EPOCH_LAST];
    const out = [];
    for (let k = 0; k < n - 1; k++) out.push(mixStops(EPOCH_BLUES, k / (n - 2)));
    out.push(EPOCH_LAST);
    return out;
  }
  const isFlagged = (p) => Array.isArray(p.flags) && p.flags.some((f) => BAD_FLAGS.has(f));
  /** Flux densities are plotted in the unit that keeps the axis numbers short. */
  function fluxUnit(values) {
    const v = values.filter(Number.isFinite).map(Math.abs).sort((a, b) => a - b);
    const top = v.length ? v[Math.floor(0.95 * (v.length - 1))] : 1;
    if (top >= 2000) return { k: 1e-3, name: 'Jy' };
    if (top < 0.5) return { k: 1e3, name: 'µJy' };
    return { k: 1, name: 'mJy' };
  }
  function axis(title, extra) {
    return Object.assign({
      title: { text: title, font: { size: 12.5, color: INK }, standoff: 10 },
      showline: true, linecolor: INK, linewidth: 1, mirror: 'ticks',
      ticks: 'inside', ticklen: 5, tickwidth: 1, tickcolor: INK, tickfont: { size: 11.5, color: INK },
      minor: { ticks: 'inside', ticklen: 2.5, tickwidth: 1, tickcolor: INK, showgrid: false },
      showgrid: false, zeroline: false, automargin: true,
    }, extra || {});
  }
  function plotLayout(xTitle, yTitle) {
    return { paper_bgcolor: '#ffffff', plot_bgcolor: '#ffffff', font: { family: FONT, size: 12, color: INK },
      margin: { l: 52, r: 1, t: 26, b: 44 }, showlegend: false, hovermode: 'closest', hoverdistance: 24, dragmode: 'zoom',
      hoverlabel: { bgcolor: INK, bordercolor: INK, font: { family: FONT, size: 12, color: '#ffffff' }, align: 'left' },
      xaxis: axis(xTitle), yaxis: axis(yTitle) };
  }
  /** y range that shows the bulk of the points; strays stay reachable with "Show all". */
  function robustRange(values) {
    const v = values.filter(Number.isFinite).sort((a, b) => a - b);
    if (v.length < 12) return null;
    const q = (f) => v[Math.min(v.length - 1, Math.max(0, Math.round(f * (v.length - 1))))];
    const lo = q(0.03), hi = q(0.97), med = q(0.5);
    const top = hi + 0.6 * Math.max(hi - med, 1e-9), bottom = Math.min(0, lo - 0.3 * Math.max(med - lo, 0));
    if (v[v.length - 1] <= top * 1.15 && v[0] >= bottom * 1.15 - 1e-12) return null;      // nothing is far off
    return [bottom, top];
  }
  /** The legend above a plot: one switch per series. */
  function renderKey(box, items, onToggle) {
    box.innerHTML = '';
    const compact = items.length > 6;
    box.classList.toggle('is-compact', compact);
    if (items.length < 2) return;
    if (compact) box.insertAdjacentHTML('beforeend', `<span class="key-end">${esc(items[0].label)}</span>`);
    items.forEach((item) => {
      const b = document.createElement('button');
      b.type = 'button'; b.title = item.label;
      b.setAttribute('aria-pressed', String(!item.hidden));
      b.innerHTML = `${item.swatch}<span>${esc(item.label)}</span>`;
      b.addEventListener('click', () => onToggle(item.id));
      box.appendChild(b);
    });
    if (compact) box.insertAdjacentHTML('beforeend', `<span class="key-end">${esc(items[items.length - 1].label)}</span>`);
  }
  const toggle = (set, id) => { if (set.has(id)) set.delete(id); else set.add(id); };
  function plotSpectrum(res) {
    whenPlotly(() => {
      if (res !== state.spectrum) return;
      const present = res.epochs.map((e) => e.index).filter((ep) => res.points.some((p) => p.epoch === ep && p.wavelength_um));
      const colors = epochColors(present.length);                 // an epoch keeps its colour whether shown or not
      const unit = fluxUnit(res.points.map((p) => p.flux_mjy));
      const shown = present.filter((ep) => !state.hiddenEpochs.has(ep));
      const traces = shown.map((ep) => {
        const color = colors[present.indexOf(ep)];
        const pts = res.points.filter((p) => p.epoch === ep && p.wavelength_um);
        return {
          x: pts.map((p) => p.wavelength_um), y: pts.map((p) => p.flux_mjy * unit.k), type: 'scatter', mode: 'markers',
          marker: { size: 6, color, symbol: pts.map((p) => (isFlagged(p) ? 'circle-open' : 'circle')),
            line: { color: '#ffffff', width: pts.map((p) => (isFlagged(p) ? 1.3 : 0.5)) } },
          error_y: { type: 'data', array: pts.map((p) => p.err_mjy * unit.k), visible: true, thickness: 1, width: 0, color: hexToRgba(color, 0.42) },
          customdata: pts.map((p) => [fmtDate(p.date), p.band[1], isFlagged(p) ? `<br>${p.flags.join(', ').toLowerCase()}` : '']),
          hovertemplate: `<b>%{y:.4g} ${unit.name}</b><br>%{x:.3f} µm, band %{customdata[1]}<br>%{customdata[0]}%{customdata[2]}<extra></extra>`,
        };
      });
      const layout = plotLayout('Wavelength, µm', `Flux density, ${unit.name}`);
      Object.assign(layout.xaxis, { range: [0.7, 5.1], dtick: 1 });
      layout.xaxis.minor.dtick = 0.25;
      Object.assign(layout.yaxis, { rangemode: 'tozero', zeroline: true, zerolinecolor: RULE, zerolinewidth: 1 });
      layout.shapes = BAND_EDGES.map((x) => ({ type: 'line', xref: 'x', yref: 'paper', x0: x, x1: x, y0: 0, y1: 1, layer: 'below', line: { color: RULE, width: 1 } }));
      const small = { size: 11, color: INK };
      layout.annotations = BAND_CENTRES.map((x, i) => ({ xref: 'x', yref: 'paper', x, y: 1, yanchor: 'bottom', yshift: 3, text: String(i + 1), showarrow: false, font: small }));
      layout.annotations.push({ xref: 'paper', yref: 'paper', x: 0, y: 1, xanchor: 'right', yanchor: 'bottom', xshift: -8, yshift: 3, text: 'Band', showarrow: false, font: small });
      const values = res.points.filter((p) => shown.includes(p.epoch)).map((p) => p.flux_mjy * unit.k);
      const range = state.fullRange ? null : robustRange(values);
      let strays = 0;
      if (range) {
        Object.assign(layout.yaxis, { range, rangemode: 'normal' });
        strays = values.filter((v) => v > range[1] || v < range[0]).length;
      }
      Plotly.react('spectrum-plot', traces, layout, PLOT_CONFIG);

      paneTitle('#spectrum-info', res.who, plural(res.points.length, 'measurement'));
      renderKey($('#spectrum-key'), present.map((ep, k) => {
        const info = res.epochs.find((e) => e.index === ep) || {};
        return { id: ep, label: fmtMonth(info.date_start), hidden: state.hiddenEpochs.has(ep), swatch: `<i style="background:${colors[k]}"></i>` };
      }), (ep) => { toggle(state.hiddenEpochs, ep); plotSpectrum(res); });
      const note = $('#spectrum-note');
      note.textContent = '';
      const lost = res.n_exposures - res.points.length;
      const parts = [];
      if (lost > 0.3 * res.n_exposures) parts.push(`${plural(lost, 'exposure')} gave no measurement here.`);
      if (res.has_flags && res.points.some(isFlagged)) parts.push('Hollow points touch flagged pixels.');
      if (strays) parts.push(`${plural(strays, 'point')} off the scale.`);
      note.textContent = parts.join(' ');
      if (strays) {
        const all = document.createElement('button');
        all.type = 'button'; all.textContent = 'Show all';
        all.addEventListener('click', () => { state.fullRange = true; plotSpectrum(res); });
        note.append(' ', all);
      }
    });
  }
  /** Every exposure against time, one strip per detector band on a shared time axis. */
  function plotStrips(res) {
    const el = $('#lightcurve-plot');
    const bands = BAND_IDS.filter((b) => res.points.some((p) => p.band === b));
    $('#lightcurve-key').innerHTML = '';
    paneTitle('#lightcurve-info', res.who, plural(res.points.length, 'exposure'));
    if (!bands.length) { if (el.data) Plotly.purge(el); return; }
    const unit = fluxUnit(res.points.map((p) => p.flux_mjy));
    const n = bands.length, gap = n > 1 ? 0.03 : 0, h = (1 - gap * (n - 1)) / n;
    el.style.height = `${Math.max(240, 56 + n * 66)}px`;
    const layout = plotLayout('', '');
    layout.margin = { l: 58, r: 1, t: 8, b: 34 };
    layout.xaxis = axis('', { type: 'date', anchor: n > 1 ? `y${n}` : 'y', mirror: 'allticks', hoverformat: '%d %b %Y' });
    // room on the right for the band names, so they never sit on the points
    const days = res.points.map((p) => p.mjd), t0 = Math.min(...days), t1 = Math.max(...days), span = Math.max(t1 - t0, 2);
    layout.xaxis.range = [mjdToDate(t0 - 0.04 * span), mjdToDate(t1 + 0.17 * span)];
    layout.annotations = [];
    const small = { size: 11, color: INK };
    const traces = bands.map((b, i) => {
      const top = 1 - i * (h + gap);
      const pts = res.points.filter((p) => p.band === b).sort((a, c) => a.mjd - c.mjd);
      layout[i === 0 ? 'yaxis' : `yaxis${i + 1}`] = axis('', { domain: [Math.max(0, top - h), top], anchor: 'x', nticks: 4, mirror: true,
        tickfont: { size: 10.5, color: INK }, automargin: false, minor: { ticks: '' } });
      layout.annotations.push({ xref: 'paper', yref: 'paper', x: 1, y: top, xanchor: 'right', yanchor: 'top', xshift: -8, yshift: -5,
        text: `Band ${b[1]}`, showarrow: false, font: small });
      return {
        x: pts.map((p) => p.date), y: pts.map((p) => p.flux_mjy * unit.k), type: 'scatter', mode: 'markers',
        xaxis: 'x', yaxis: i === 0 ? 'y' : `y${i + 1}`,
        marker: { size: 5.5, color: BAND_COLORS[b], symbol: pts.map((p) => (isFlagged(p) ? 'circle-open' : 'circle')),
          line: { color: '#ffffff', width: pts.map((p) => (isFlagged(p) ? 1.2 : 0.4)) } },
        error_y: { type: 'data', array: pts.map((p) => p.err_mjy * unit.k), visible: true, thickness: 1, width: 0, color: hexToRgba(BAND_COLORS[b], 0.42) },
        customdata: pts.map((p) => [p.wavelength_um ? `${p.wavelength_um.toFixed(3)} µm` : `band ${b[1]}`]),
        hovertemplate: `<b>%{y:.4g} ${unit.name}</b><br>%{customdata[0]}<br>%{x|%d %b %Y, %H:%M}<extra></extra>`,
      };
    });
    layout.annotations.push({ xref: 'paper', yref: 'paper', x: 0, y: 0.5, xanchor: 'center', yanchor: 'middle', xshift: -48, textangle: -90,
      text: `Flux density, ${unit.name}`, showarrow: false, font: { size: 12.5, color: INK } });
    Plotly.react(el, traces, layout, PLOT_CONFIG);
  }
  /** The epoch stacks of a listed source: each band relative to its own mean. */
  function plotEpochCurve(src) {
    const el = $('#lightcurve-plot');
    el.style.height = '';
    const epochs = state.result.epochs;
    const dateOf = (index) => { const ep = epochs.find((e) => e.index === index); return ep ? mjdToDate(ep.mjd_mean) : null; };
    const bands = BAND_IDS.filter((b) => src.curve[b] && src.curve[b].length);
    const traces = bands.filter((b) => !state.hiddenBands.has(b)).map((b) => ({
      x: src.curve[b].map((p) => dateOf(p[0])), y: src.curve[b].map((p) => p[1]), type: 'scatter', mode: 'lines+markers',
      line: { color: BAND_COLORS[b], width: 2 },
      marker: { size: 9, color: BAND_COLORS[b], symbol: BAND_SYMBOLS[b], line: { color: '#ffffff', width: 1.5 } },
      hovertemplate: `<b>%{y:.2f} × mean</b><br>band ${b[1]}<br>%{x|%d %b %Y}<extra></extra>`,
    }));
    const layout = plotLayout('', 'Relative to the band mean');
    layout.margin = { l: 52, r: 1, t: 8, b: 34 };
    Object.assign(layout.xaxis, { type: 'date' });
    layout.shapes = [{ type: 'line', xref: 'paper', yref: 'y', x0: 0, x1: 1, y0: 1, y1: 1, layer: 'below', line: { color: RULE, width: 1 } }];
    Plotly.react(el, traces, layout, PLOT_CONFIG);
    const id = identify(src);
    paneTitle('#lightcurve-info', id ? pretty(id.label) : `Source ${src.id}`, src.saturated ? 'Epoch stacks, measured from the wings' : 'Epoch stacks');
    renderKey($('#lightcurve-key'), bands.map((b) => ({ id: b, label: `Band ${b[1]}`, hidden: state.hiddenBands.has(b),
      swatch: `<svg viewBox="0 0 10 10" aria-hidden="true" fill="${BAND_COLORS[b]}">${SYMBOL_PATHS[BAND_SYMBOLS[b]]}</svg>` })),
    (b) => { toggle(state.hiddenBands, b); plotEpochCurve(src); });
  }
  function plotLightcurve(res) {
    whenPlotly(() => {
      const src = state.selected;
      const hasCurve = Boolean(src && src.curve && Object.keys(src.curve).length);
      const kind = $('#lc-kind');
      kind.querySelector('option[value=epoch]').disabled = !hasCurve;
      if (!hasCurve && kind.value === 'epoch') kind.value = 'exposure';
      if (kind.value === 'epoch') { plotEpochCurve(src); return; }
      if (res && res === state.spectrum) plotStrips(res);
    });
  }

  // ───────────────────────── other surveys ─────────────────────────
  function updateContext() {
    if (!state.result) return;
    const t = state.result.target, choice = $('#context-layer').value;
    if (activeTab() !== 'context') return;
    const note = $('#context-note');
    if (choice === 'aladin') {
      $('#context-image').classList.add('hidden'); $('#aladin').classList.remove('hidden');
      note.textContent = 'Loading';
      showAladin().then(() => { note.textContent = ''; }).catch(() => { note.textContent = 'Aladin Lite is not answering right now'; });
      return;
    }
    $('#aladin').classList.add('hidden'); $('#context-image').classList.remove('hidden');
    const size = 512, pixscale = t.fov_arcmin * 60 / size;
    const img = $('#context-img');
    const url = `https://www.legacysurvey.org/viewer/cutout.jpg?ra=${t.ra}&dec=${t.dec}&layer=${choice}&pixscale=${pixscale.toFixed(4)}&size=${size}`;
    if (img.dataset.url !== url) {
      note.textContent = 'Loading';
      img.onload = () => { note.textContent = ''; };
      img.onerror = () => { note.textContent = 'This survey image could not be loaded'; };
      img.dataset.url = url; img.src = url;
    }
  }
  let aladinApi = null;
  async function showAladin() {
    if (!aladinApi) {
      let lastError = new Error('not reachable');
      for (const src of ALADIN_SOURCES) {
        try { const mod = await import(src); aladinApi = mod.default || mod.A || window.A || null; if (aladinApi) break; } catch (e) { lastError = e; }
      }
      if (!aladinApi) throw lastError;
    }
    const A = aladinApi, t = state.result.target, fovDeg = t.fov_arcmin / 60;
    if (A.init && typeof A.init.then === 'function') await A.init;
    if (!state.aladin) {
      state.aladin = A.aladin('#aladin', { survey: 'CDS/P/unWISE/color-W2-W1W2-W1', fov: fovDeg, target: `${t.ra} ${t.dec}`, cooFrame: 'ICRSd', showReticle: true });
    } else {
      state.aladin.gotoRaDec(t.ra, t.dec); state.aladin.setFoV(fovDeg);
    }
  }

  // ───────────────────────── exposures table ─────────────────────────
  function fillExposures(result) {
    const tb = $('#exposures-table tbody');
    const shown = result.exposures.slice(0, 1500);
    tb.innerHTML = shown.map((e) => `<tr class="${e.used ? '' : 'unused'}" title="${esc(e.obs_id)}"><td>${mjdToDate(e.mjd).replace('T', ' ').slice(0, 16)}</td>`
      + `<td><i class="dot" style="background:${BAND_COLORS[`D${e.detector}`]}"></i>${e.detector}</td><td>${e.epoch == null ? '' : e.epoch}</td>`
      + `<td>${esc(e.collection.replace('spherex_', '').replace(/_/g, ' ').toUpperCase())}</td><td>${e.used ? 'Yes' : ''}</td></tr>`).join('');
    const parts = [`${plural(result.n_found, 'exposure')} cover this field`];
    if (result.n_selected < result.n_found) parts.push(`${result.n_selected} read`);
    if (result.exposures.length > shown.length) parts.push(`first ${shown.length} listed`);
    $('#exposures-info').textContent = parts.join(', ');
  }

  // ───────────────────────── export ─────────────────────────
  function download(blob, name) {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob); a.download = name; a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
  }
  const fileStem = () => `beyondtheblue_${(state.target && state.target.id) || 'field'}_${bandChoice()}_${view()}`;
  async function saveVideo() {
    if (state.recording || !state.sequence.length) return;
    if (!canvas.captureStream || typeof MediaRecorder === 'undefined') { showError('This browser cannot record video. Save the frame as PNG instead.'); return; }
    state.recording = true;
    const wasPlaying = state.playing, button = $('#btn-video'), text = button.querySelector('span');
    setPlaying(false);
    text.textContent = 'Recording'; button.disabled = true;
    try {
      const mime = ['video/webm;codecs=vp9', 'video/webm;codecs=vp8', 'video/webm'].find((m) => MediaRecorder.isTypeSupported(m));
      const rec = new MediaRecorder(canvas.captureStream(30), mime ? { mimeType: mime, videoBitsPerSecond: 8e6 } : undefined);
      const chunks = [];
      rec.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
      const stopped = new Promise((res) => { rec.onstop = res; });
      rec.start();
      const fps = parseFloat($('#fps').value), loops = state.sequence.length <= 4 ? 4 : 2;
      for (let l = 0; l < loops; l++) for (let i = 0; i < state.sequence.length; i++) { state.seqPos = i; syncSlider(); draw(); await sleep(1000 / fps); }
      rec.stop(); await stopped;
      download(new Blob(chunks, { type: 'video/webm' }), `${fileStem()}.webm`);
    } catch (e) {
      showError(`The recording failed: ${e.message}`);
    } finally {
      state.recording = false; text.textContent = 'Video'; button.disabled = false;
      if (wasPlaying) setPlaying(true);
    }
  }

  // ───────────────────────── tabs and URL ─────────────────────────
  const activeTab = () => { const t = $('.tab.active'); return t ? t.dataset.tab : 'spectrum'; };
  function activateTab(name) {
    $$('.tab').forEach((t) => { const on = t.dataset.tab === name; t.classList.toggle('active', on); t.setAttribute('aria-selected', String(on)); });
    $$('.tabpane').forEach((p) => p.classList.toggle('active', p.dataset.pane === name));
    if (name === 'context') updateContext();
    if ((name === 'spectrum' || name === 'lightcurve') && typeof Plotly !== 'undefined') {
      const el = $(`#${name}-plot`);
      if (el && el.data) Plotly.Plots.resize(el);
    }
  }
  function updateUrl() {
    if (!state.target) return;
    const p = new URLSearchParams();
    p.set('target', state.target.source === 'preset' ? state.target.id : state.target.query);
    p.set('fov', $('#fov').value);
    if (bandChoice() !== 'ALL') p.set('band', bandChoice());
    if (view() === 'exposure') p.set('seq', 'exposure'); else if (view() !== 'normal') p.set('mode', view());
    if ($('#fps').value !== '2') p.set('fps', $('#fps').value);
    [['invert', '#chk-invert'], ['gaia', '#chk-gaia'], ['catwise', '#chk-catwise'], ['flags', '#chk-flags']].forEach(([key, sel]) => { if ($(sel).checked) p.set(key, '1'); });
    const follow = tracking();
    if (follow) { p.set('pmra', follow.ra); p.set('pmdec', follow.dec); p.set('track', '1'); }
    if (document.body.classList.contains('embed')) p.set('embed', '1');
    history.replaceState(null, '', `${location.pathname}?${p.toString()}`);
    p.delete('embed');
    $('#open-full').href = `${location.pathname}?${p.toString()}`;
  }
  /** The address of the current view, as it is or as the compact player for an iframe. */
  function viewUrl(embed) {
    updateUrl();
    const p = new URLSearchParams(location.search);
    if (embed) p.set('embed', '1'); else p.delete('embed');
    return `${location.origin}${location.pathname}?${p.toString()}`;
  }
  async function copyText(button, text) {
    const label = button.querySelector('span'), was = label.textContent;
    try { await navigator.clipboard.writeText(text); label.textContent = 'Copied'; setTimeout(() => { label.textContent = was; }, 1500); } catch (e) { window.prompt('Copy this', text); }
  }
  function readUrl() {
    const p = new URLSearchParams(location.search);
    if (p.get('embed') === '1') document.body.classList.add('embed');
    applyParams(p);
    const query = p.get('target') || (p.get('ra') && p.get('dec') ? `${p.get('ra')} ${p.get('dec')}` : null);
    return { query, hasFov: Boolean(p.get('fov')) };
  }

  // ───────────────────────── pointer ─────────────────────────
  function canvasToPix(ev) {
    const rect = canvas.getBoundingClientRect();
    const n = state.result.target.npix;
    return { x: (ev.clientX - rect.left) / rect.width * n - 0.5, y: n - (ev.clientY - rect.top) / rect.height * n - 0.5 };
  }
  function flagNames(value) {
    const defs = (state.result && state.result.flag_defs) || {};
    return Object.entries(defs).filter(([, bit]) => ((value >>> 0) & (1 << bit)) !== 0).map(([nm]) => nm);
  }
  canvas.addEventListener('mousemove', (ev) => {
    if (!state.result) return;
    const frame = currentFrame();
    if (!frame) return;
    const { x, y } = canvasToPix(ev);
    const n = state.result.target.npix, ix = Math.round(x), iy = Math.round(y);
    if (ix < 0 || iy < 0 || ix >= n || iy >= n) { state.hover = null; $('#readout').textContent = ''; paint(); return; }
    state.hover = { x: ix, y: iy };
    const sh = frame.shift || ZERO;
    const sky = pixToSky(state.wcs, ix + sh.dx, iy + sh.dy);
    const v = frame.data[iy * n + ix];
    const fl = frame.flags ? flagNames(frame.flags[iy * n + ix]) : [];
    const value = frame.sat && frame.sat[iy * n + ix] ? 'saturated' : Number.isFinite(v) ? `${v.toPrecision(3)} MJy/sr` : 'no data';
    $('#readout').textContent = `${raToHms(sky.ra, 1)} ${decToDms(sky.dec, 0)}   ${value}`
      + (fl.length ? `   ${fl.join(', ').toLowerCase()}` : '');
    paint();
  });
  canvas.addEventListener('mouseleave', () => { state.hover = null; $('#readout').textContent = ''; if (state.result) paint(); });
  canvas.addEventListener('click', (ev) => {
    if (!state.result) return;
    const frame = currentFrame();
    if (!frame) return;
    const { x, y } = canvasToPix(ev);
    const sh = frame.shift || ZERO, w = state.wcs;
    const year = frame.meta.year || state.target.epoch_now;
    const dist = (px) => Math.hypot(px.x - sh.dx - x, px.y - sh.dy - y);
    // a measured source under the cursor wins, then the target, then a catalogue entry
    const analysis = state.result.analysis;
    if (analysis && group() === 'epoch') {
      let hit = null, best = 2.2;
      analysis.sources.forEach((src) => { const d = dist(sourcePixel(src, frame)); if (d < best) { best = d; hit = src; } });
      if (hit) { selectSource(hit); return; }
    }
    state.selected = null;
    $$('#changes-list .change, #sources-table tbody tr').forEach((el) => el.classList.remove('selected'));
    const t = state.target;
    if (t) {
      const p = propagate(t.ra, t.dec, t.pmra, t.pmdec, t.epoch || 2000, year);
      const q = skyToPix(w, p.ra, p.dec);
      if (q && dist(q) < 3) { measureAt(t.ra, t.dec, { pmra: t.pmra, pmdec: t.pmdec, epoch: t.epoch || 2000, name: t.source === 'coordinates' ? null : pretty(t.name), isTarget: true }); plotLightcurve(state.spectrum); return; }
    }
    let pick = null, bestDist = 1.8;
    ['gaia', 'catwise'].forEach((name) => {
      const rows = state.catalogs[name];
      if (!rows || !$(`#chk-${name}`).checked) return;
      rows.forEach((s) => {
        const p = propagate(s.ra, s.dec, s.pmra, s.pmdec, s.epoch, year);
        const q = skyToPix(w, p.ra, p.dec);
        if (q && dist(q) < bestDist) { bestDist = dist(q); pick = s; }
      });
    });
    if (pick) { measureAt(pick.ra, pick.dec, { pmra: pick.pmra, pmdec: pick.pmdec, epoch: pick.epoch, name: pick.id }); return; }
    const sky = pixToSky(w, x + sh.dx, y + sh.dy);
    const follow = tracking();
    measureAt(sky.ra, sky.dec, follow ? { pmra: follow.ra, pmdec: follow.dec, epoch: year } : { pmra: 0, pmdec: 0, epoch: year });
  });

  // ───────────────────────── controls ─────────────────────────
  $('#target-form').addEventListener('submit', (ev) => {
    ev.preventDefault();
    const q = $('#target-input').value.trim();
    setSuggestOpen(false);
    if (q) { $('#target-input').blur(); openTarget(q); }
  });
  $$('button[data-id][data-fov]').forEach((b) => b.addEventListener('click', () => openTarget(b.dataset.id, parseFloat(b.dataset.fov))));
  $('#btn-load').addEventListener('click', () => { if (state.target) loadFrames(); else $('#target-form').requestSubmit(); });
  $('#btn-dismiss').addEventListener('click', clearError);
  renderFov(); renderBand();
  $('#fov').addEventListener('change', () => { if (state.target && document.body.classList.contains('is-working')) loadFrames(); });
  $$('input[name=view]').forEach((el) => el.addEventListener('change', () => {
    if (state.target && state.result && seqKind() !== group()) { loadFrames(); return; }
    state.seqPos = 0;
    onSelectionChange(); renderChanges(); draw();
    if (state.result && state.sequence.length > 1 && view() === 'blink') setPlaying(true);
  }));
  $$('input[name=band]').forEach((el) => el.addEventListener('change', () => {
    if (state.target && state.result && group() === 'exposure') { loadFrames(); return; }
    onSelectionChange(); draw();
  }));
  ['#ref-epoch', '#blink-b'].forEach((sel) => $(sel).addEventListener('change', () => { onSelectionChange(); draw(); }));
  ['#stretch', '#cmap', '#contrast', '#chk-invert'].forEach((sel) => $(sel).addEventListener('input', () => { state.levelCache.clear(); draw(); paintThumbs(); updateUrl(); }));
  ['#chk-smooth', '#chk-target', '#chk-changes', '#chk-grid', '#chk-border'].forEach((sel) => $(sel).addEventListener('change', draw));
  $('#chk-gaia').addEventListener('change', () => { if ($('#chk-gaia').checked) loadCatalog('gaia'); draw(); updateUrl(); });
  $('#chk-catwise').addEventListener('change', () => { if ($('#chk-catwise').checked) loadCatalog('catwise'); draw(); updateUrl(); });
  $('#chk-flags').addEventListener('change', () => {
    updateUrl();
    if ($('#chk-flags').checked && state.result && !state.result.has_flags && state.target) loadFrames(); else draw();
  });
  const onTrackChange = () => {
    $('#track').classList.toggle('is-on', Boolean(tracking()));
    clearDerived(); buildSequence(); draw(); updateUrl();
  };
  ['#pmra', '#pmdec', '#chk-track'].forEach((sel) => $(sel).addEventListener('change', onTrackChange));
  $('#btn-track-target').addEventListener('click', () => {
    if (!state.target || !(state.target.pmra || state.target.pmdec)) { showError('This target has no catalogue proper motion.'); return; }
    $('#pmra').value = Math.round(state.target.pmra || 0); $('#pmdec').value = Math.round(state.target.pmdec || 0);
    $('#chk-track').checked = true; onTrackChange();
  });
  $('#btn-track-source').addEventListener('click', () => {
    if (!state.selected) { showError('Select a source first, on the image or in the list.'); return; }
    $('#pmra').value = Math.round(state.selected.pm_ra * 1000); $('#pmdec').value = Math.round(state.selected.pm_dec * 1000);
    $('#chk-track').checked = true; onTrackChange();
  });
  $('#btn-play').addEventListener('click', () => setPlaying(!state.playing));
  $('#btn-prev').addEventListener('click', () => { setPlaying(false); step(-1); });
  $('#btn-next').addEventListener('click', () => { setPlaying(false); step(1); });
  $('#fps').addEventListener('change', () => { if (state.playing) setPlaying(true); updateUrl(); });
  $('#frame-slider').addEventListener('input', () => { setPlaying(false); state.seqPos = parseInt($('#frame-slider').value, 10); draw(); });
  $('#aperture').addEventListener('change', () => { const p = state.probe; if (p) measureAt(p.ra, p.dec, { pmra: p.pmra, pmdec: p.pmdec, epoch: p.epoch, name: p.name, isTarget: p.isTarget }); });
  $('#context-layer').addEventListener('change', updateContext);
  $('#lc-kind').addEventListener('change', () => plotLightcurve(state.spectrum));
  $$('.tab').forEach((t) => { t.dataset.label = t.textContent; t.addEventListener('click', () => activateTab(t.dataset.tab)); });
  const help = $('#help');
  $('#btn-help').addEventListener('click', () => help.showModal());
  help.addEventListener('click', (ev) => { if (ev.target === help) help.close(); });
  $('#btn-link').addEventListener('click', () => copyText($('#btn-link'), viewUrl(false)));
  $('#btn-embed').addEventListener('click', () => {
    const name = state.target ? (state.target.source === 'coordinates' ? shortCoord(state.target.ra, state.target.dec) : pretty(state.target.name)) : 'the sky';
    copyText($('#btn-embed'), `<iframe src="${viewUrl(true)}" width="480" height="570" style="border:0" loading="lazy" title="SPHEREx images of ${esc(name)}, BeyondtheBlue"></iframe>`);
  });
  $('#btn-fits').addEventListener('click', async () => {
    if (!state.result) return;
    const label = $('#btn-fits').querySelector('span');
    label.textContent = 'Preparing';
    try {
      let id = state.jobId;
      const known = await api(`/api/jobs/${id}`).then((st) => st.state === 'done', () => false);
      if (!known) {                                  // the server forgets finished jobs after an hour
        const p = state.result.params;
        const again = await runJob('frames', { ra: p.ra, dec: p.dec, fov: p.fov, group: p.group, detector: p.detector, flags: p.flags }, () => {});
        id = state.jobId = again.job.job_id;
      }
      const a = document.createElement('a');
      a.href = `/api/jobs/${id}/fits`; a.download = ''; a.click();
    } catch (e) {
      showError('The FITS file could not be prepared. Try again in a moment.');
    } finally {
      label.textContent = 'All frames';
    }
  });
  $('#btn-png').addEventListener('click', () => canvas.toBlob((blob) => download(blob, `${fileStem()}_${state.seqPos + 1}.png`)));
  $('#btn-video').addEventListener('click', saveVideo);
  // menus close when something else is clicked, or with Escape
  document.addEventListener('click', (ev) => { $$('details.menu[open]').forEach((d) => { if (!d.contains(ev.target)) d.open = false; }); });
  document.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape') $$('details.menu[open]').forEach((d) => { d.open = false; });
    if (help.open || ['INPUT', 'SELECT', 'TEXTAREA', 'BUTTON', 'SUMMARY'].includes(ev.target.tagName)) return;
    if (ev.code === 'Space') { ev.preventDefault(); setPlaying(!state.playing); }
    if (ev.code === 'ArrowLeft') { setPlaying(false); step(-1); }
    if (ev.code === 'ArrowRight') { setPlaying(false); step(1); }
  });
  const sheet = $('#sheet');
  sheet.addEventListener('toggle', () => {
    if (sheetAuto) { sheetAuto = false; return; }          // opened or closed by the page, not by the visitor
    sheetChoice = sheet.open ? 'open' : 'closed';
    try { localStorage.setItem('sheet', sheetChoice); } catch (e) { /* storage is optional */ }
  });
  if (sheetChoice) { sheetAuto = sheet.open !== (sheetChoice === 'open'); sheet.open = sheetChoice === 'open'; }
  let resizeTimer = null;
  window.addEventListener('resize', () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => { if (state.result) draw(); }, 80); });

  // ───────────────────────── boot ─────────────────────────
  async function boot() {
    const repo = document.querySelector('meta[name="repo"]');
    if (repo && repo.content) $('#link-github').href = repo.content; else $('#link-github').classList.add('hidden');
    if (document.fonts && document.fonts.load) {
      document.fonts.load(`500 12px Poppins`).then(() => { if (state.result) paint(); }).catch(() => {});
    }
    const url = readUrl();
    onSelectionChange();
    draw();
    try {
      state.config = await patient(() => api('/api/config'), 4);
      if (state.config.default_flags && new URLSearchParams(location.search).get('flags') === null) { $('#chk-flags').checked = true; $('#more-overlays').open = true; }
    } catch (e) {
      showError('The server is not answering.');
    }
    if (!url.query) return;
    const t = await resolveTarget(url.query);
    if (!t) return;
    if (!url.hasFov && t.fov) ensureFov(t.fov);
    showWorkspace();
    loadFrames();
  }
  boot();
})();
