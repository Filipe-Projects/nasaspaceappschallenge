/* BeyondtheBlue: the guided tour (no build step).
   On a first visit it offers a short walk through the viewer. The page is dimmed, one part at a time
   is cut out of the shade and outlined, and a card beside it says what that part does. Most steps ask
   the visitor to try it, and answer what just happened. About starts it again; ?tour=1 forces it. */
(() => {
  'use strict';

  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));
  const page = document.body;
  if (page.classList.contains('embed')) return;                 // never inside another site's page

  const SEEN_KEY = 'tour-seen';
  const PAD = 6;            // from a part of the page to the outline around it
  const GAP = 14;           // from the outline to the card
  const EDGE = 12;          // the card stays this far inside the window
  const JOIN = 10;          // cut-outs closer than this become one
  const SLOW_MS = 12000;    // after this long a load can be left to finish on its own

  const calm = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const working = () => page.classList.contains('is-working');
  const loaded = () => !$('#canvas-wrap').classList.contains('is-empty');
  const stuck = () => !loaded() && $('#progress').classList.contains('hidden') && !$('#error').classList.contains('hidden');
  // checkVisibility also sees through a closed <details>, whose content keeps a layout box in current browsers.
  const visible = (el) => Boolean(el) && (typeof el.checkVisibility === 'function' ? el.checkVisibility() : el.getClientRects().length > 0);
  const checked = (name) => { const el = $(`input[name=${name}]:checked`); return el ? el.value : ''; };
  const image = () => [$('#canvas-wrap'), $('.frame-row')];
  const terms = (rows) => `<dl class="tour-terms">${rows.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join('')}</dl>`;
  const CHECK = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 8.6l3.2 3.2L13 5" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg><span class="visually-hidden">Done</span>';

  // ───────────────────────── what each step says ─────────────────────────
  const LOADING = {
    label: 'Loading', title: 'Reading the archive', task: null,
    text: '<p>BeyondtheBlue is gathering every SPHEREx image of this field from IRSA, NASA’s infrared archive. The test targets open in seconds; a field nobody has opened before can take a minute or two.</p>',
  };
  const VIEW_SAYS = {
    diff: 'Red got brighter and blue got fainter. A star that moved leaves a red and blue pair, side by side.',
    blink: 'Two visits, back and forth. Choose which two beside the date under the image.',
    sum: 'Every visit added into one deeper image, where faint sources stand out.',
    exposure: 'Single exposures, straight from the telescope. Each one sees a narrow slice of wavelength.',
    normal: 'Every visit in turn, as at the start.',
  };
  const LAYER_SAYS = {
    'chk-target': 'The white cross follows your target from frame to frame.',
    'chk-changes': 'Yellow marks the sources that moved or changed brightness.',
    'chk-gaia': 'Each circle is a Gaia star, drawn where it was on the date of the frame.',
    'chk-catwise': 'Each square is a CatWISE source. Cold brown dwarfs show up in this catalogue.',
    'chk-flags': 'Flagged pixels are coloured by what went wrong with them. Reading the flags takes a little longer.',
  };
  const TAB_SAYS = {
    spectrum: 'Back to the spectrum.',
    lightcurve: 'One line per band. A steady source stays flat; a variable one rises and falls.',
    sources: 'Click a row to select that source and measure its light.',
    context: 'The same field from other surveys, for comparison.',
    exposures: 'Every exposure with its date and band. Faded rows were left out.',
  };

  /*
   * A step points at `focus` (outlined) and also leaves `show` uncovered, so the visitor sees what a
   * control does to the image. The card sits beside `anchor` (the focus unless given), on the first
   * of the `place` sides with room (`phone` on a narrow screen, where only top and bottom can fit).
   * `task` asks for one action and `on` answers it. A step with a `mood` changes what it says while
   * that changes (a field still loading, a search on its way).
   */
  const STEPS = [
    {
      id: 'search', onStart: true,
      label: 'Search', title: 'Choose a patch of sky',
      text: '<p>Type the name of a star or a galaxy, or its coordinates in any usual form, such as <b>133.76 -7.24</b>.</p>',
      focus: () => [$('#target-form'), $('#target-suggest')], anchor: () => [$('#target-form')],
      radius: 36, place: ['right', 'top', 'bottom'], phone: ['top', 'bottom'],
      task: { ask: 'Click the box and pick one of the test targets, or type your own.' },
      next: 'Open Barnard’s Star',
      act: () => { $('#target-input').value = "Barnard's Star"; $('#target-form').requestSubmit(); },   // a name the server knows as a test target
      mood: () => ($('#target-form').classList.contains('is-busy') ? 'busy' : ''),
      moods: { busy: { next: 'Opening', hold: true } },
      until: working,
    },
    {
      id: 'stage',
      label: 'The image', title: 'A film of one patch of sky',
      text: '<p>Each frame is one visit by SPHEREx, months apart. Most stars stay put; anything that jumps from frame to frame has moved.</p>'
        + '<p>The white cross marks your target, and yellow marks show what the viewer found moving or changing.</p>',
      focus: () => [$('#canvas-wrap')], radius: 4, place: ['right', 'left', 'bottom', 'top'],
      task: {
        ask: 'Point at a star to read its position and brightness, then click it to measure its light.',
        on: (done) => listen($('#view'), 'click', () => done('Measured. Its spectrum is waiting in the Spectrum tab.')),
      },
      mood: () => (loaded() ? '' : stuck() ? 'stuck' : Date.now() - since > SLOW_MS ? 'slow' : 'wait'),
      moods: {
        wait: { ...LOADING, next: 'Loading', hold: true },
        slow: { ...LOADING, next: 'Continue' },
        stuck: { ...LOADING, next: 'Continue', text: '<p>The archive is not answering right now. You can carry on with the tour and try again later.</p>' },
      },
    },
    {
      id: 'player',
      label: 'The player', title: 'Play, pause, step',
      text: '<p>The red button plays and pauses the film, and so does the space bar. The arrows, or the ← and → keys, step one frame at a time, and the slider jumps anywhere. The half-filled circle sets the contrast.</p>',
      focus: () => [$('#btn-prev'), $('#btn-play'), $('#btn-next'), $('#frame-slider'), $('#frame-count'), $('.transport .contrast')],
      show: image, radius: 26, place: ['right', 'bottom', 'top'],
      task: {
        ask: 'Pause the film, then step through it frame by frame.',
        on: (done) => {
          const say = () => done('The date of each frame is written under the image. Watch for anything that shifts.');
          ['#btn-prev', '#btn-next'].forEach((sel) => listen($(sel), 'click', say));
          listen($('#frame-slider'), 'input', say);
          listen(document, 'keydown', (ev) => {
            if ((ev.code === 'ArrowLeft' || ev.code === 'ArrowRight') && !['INPUT', 'SELECT', 'TEXTAREA', 'BUTTON', 'SUMMARY'].includes(ev.target.tagName)) say();
          });
        },
      },
    },
    {
      id: 'view',
      label: 'View', title: 'Five ways to compare visits',
      text: terms([['Epochs', 'every visit in turn'], ['Blink', 'two visits, back and forth'], ['Difference', 'one visit minus another'],
        ['Sum', 'all visits added together'], ['Exposures', 'single images, one wavelength each']]),
      focus: () => [$('#view-seg')], show: image, radius: 20, place: ['right', 'bottom', 'top'],
      task: {
        ask: () => (checked('view') === 'diff' ? 'Choose Blink.' : 'Choose Difference.'),
        on: (done) => $$('input[name=view]').forEach((el) => listen(el, 'change', () => done(VIEW_SAYS[el.value]))),
      },
    },
    {
      id: 'band',
      label: 'Band and field', title: 'Which light, how much sky',
      text: '<p>SPHEREx splits infrared light from 0.75 to 5 µm across six detector bands. <b>Band</b> shows one of them, all six together, or <b>Colour</b>: a picture painted from three pairs of bands.</p>'
        + '<p><b>Field</b> sets how wide the patch is, from 3′ to 30′. A wider field takes longer to load.</p>',
      focus: () => [$('#band-seg'), $('#fov-menu').closest('.opts-field'), $('#band-list'), $('#fov-list')],
      anchor: () => [$('#band-seg'), $('#fov-menu').closest('.opts-field')],
      show: image, radius: 16, place: ['right', 'bottom', 'top'],
      // Colour paints epoch frames, not differences or single exposures: go back to them first.
      enter: () => {
        if (!['diff', 'exposure'].includes(checked('view'))) return;
        const epochs = $('input[name=view][value=normal]');
        if (epochs && !epochs.disabled) epochs.click();
      },
      task: {
        ask: () => (checked('band') === 'RGB' ? 'Open Band and choose band 6.' : 'Open Band and choose Colour.'),
        on: (done) => {
          $$('input[name=band]').forEach((el) => listen(el, 'change', () => done(
            el.value === 'RGB' ? 'Most stars come out blue-white. Cold objects, such as brown dwarfs, come out red.'
              : el.value === 'ALL' ? 'All six bands averaged together: the deepest view of the field.'
                : 'One band on its own. The longer the wavelength, the better it shows cold objects.')));
          listen($('#fov'), 'change', () => done('A new width means new images: the field reloads with every visit that covers it.'));
        },
      },
    },
    {
      id: 'overlays',
      label: 'Overlays', title: 'Marks drawn on the sky',
      text: '<p><b>Target</b> marks where catalogues place your object on the date of each frame, and <b>Changes</b> marks what moved or varied. <b>Gaia DR3</b> and <b>CatWISE</b> add the stars and infrared sources of two sky catalogues. <b>More</b> holds pixel flags and Follow motion.</p>',
      focus: () => [$('.opts-layers')], show: () => [$('#canvas-wrap')], radius: 16, place: ['right', 'bottom', 'top'],
      task: {
        ask: () => ($('#chk-gaia').checked ? 'Switch on CatWISE.' : 'Switch on Gaia DR3.'),
        on: (done) => {
          Object.entries(LAYER_SAYS).forEach(([id, say]) => {
            const el = $(`#${id}`);
            listen(el, 'change', () => done(el.checked ? say : 'Hidden. Switch it back on whenever you like.'));
          });
          const more = $('#more-overlays');
          listen(more, 'toggle', () => { if (more.open) done('Pixel flags colour the pixels SPHEREx marked as unreliable. Follow motion shifts the frames so that an object moving at a chosen speed stays still.'); });
        },
      },
    },
    {
      id: 'display',
      label: 'Display and save', title: 'Tune the picture, keep it',
      text: '<p>The sliders open the display settings: speed, brightness stretch and colour map.</p>'
        + '<p>The arrow saves the film as a video, the frame as a PNG or every frame as a FITS file, and copies a link that reopens this exact view.</p>',
      focus: () => [$('#menu-display'), $('#menu-save'), $('#menu-display .menu-panel'), $('#menu-save .menu-panel')],
      anchor: () => [$('#menu-display'), $('#menu-save')],
      show: image, radius: 24, place: ['right', 'top', 'bottom'],
      task: {
        ask: 'Open the display settings.',
        on: (done) => {
          const display = $('#menu-display'), save = $('#menu-save');
          listen(display, 'toggle', () => { if (display.open) done('Speed sets the frames per second. Stretch and Colour change how brightness turns into pixels.'); });
          listen(save, 'toggle', () => { if (save.open) done('Copy link gives an address that reopens this exact view, ready to bookmark or send.'); });
        },
      },
    },
    {
      id: 'sheet',
      label: 'All frames', title: 'Every visit side by side',
      text: '<p>One small image for each visit in each band. The outlined one is on the main image.</p>',
      focus: () => [$('#sheet')], show: () => [$('#canvas-wrap')], radius: 4, place: ['right', 'top', 'bottom'],
      task: {
        ask: () => ($('#sheet').open ? 'Click any of them to show it on the main image.' : 'Open All frames, then click any of them.'),
        on: (done) => listen($('#thumbs'), 'click', (ev) => { if (ev.target.closest('.thumb:not(.is-missing)')) done('Now on the main image. Press play to carry on.'); }),
      },
    },
    {
      id: 'target',
      label: 'Your target', title: 'What you are looking at',
      text: '<p>The name of your target. <b>Details</b> holds its position and motion, and <b>What changed</b>: the sources the viewer found moving or varying by itself, without being told where to look.</p>',
      focus: () => [$('#target-block')], show: () => [$('#canvas-wrap')], radius: 4, place: ['bottom', 'left', 'top'],
      task: {
        ask: () => ($('#target-details').open ? 'Click a source under What changed.' : 'Open Details.'),
        on: (done) => {
          const details = $('#target-details');
          listen(details, 'toggle', () => {
            if (details.open) done($('#changes-list .change') ? 'Moving sources carry an arrow, varying ones a diamond. Click one to mark it on the image.' : 'Its position and motion, and what the comparison of every visit found.');
          });
          listen($('#changes-list'), 'click', (ev) => { if (ev.target.closest('.change')) done('Selected. Its spectrum is being measured.'); });
        },
      },
    },
    {
      id: 'spectrum',
      label: 'Spectrum', title: 'Its light, wavelength by wavelength',
      text: '<p>How bright the selected source is at each wavelength, measured in every SPHEREx exposure, one colour per visit.</p>'
        + '<p>It starts on your target. Clicking a star on the image measures that one instead.</p>',
      focus: () => [$('.tabs'), $('.tabpane[data-pane="spectrum"]')], anchor: () => [$('.tabs'), $('#spectrum-key')],
      radius: 4, place: ['left', 'top', 'bottom'], phone: ['top', 'bottom'],
      enter: () => { const tab = $('.tab[data-tab="spectrum"]'); if (tab && !tab.classList.contains('active')) tab.click(); },
      task: {
        when: () => $('#spectrum-key button'),
        ask: 'Click a visit in the key above the plot to hide it, then again to bring it back.',
        on: (done) => listen($('#spectrum-key'), 'click', (ev) => { if (ev.target.closest('button')) done('Hiding visits one at a time shows whether the source changed between them.'); }),
      },
    },
    {
      id: 'tabs',
      label: 'Measurements', title: 'Four more tabs',
      text: terms([['Light curve', 'brightness against time'], ['Sources', 'every bright source and its motion'],
        ['Surveys', 'the field in other telescopes’ images'], ['Exposures', 'every SPHEREx image used']]),
      focus: () => [$('.tabs'), $('.tabpanes')], anchor: () => [$('.tabs')],
      radius: 4, place: ['left', 'top', 'bottom'], phone: ['top', 'bottom'],
      task: {
        ask: 'Open Light curve.',
        on: (done) => $$('.tab').forEach((tab) => listen(tab, 'click', () => done(TAB_SAYS[tab.dataset.tab]))),
      },
    },
    {
      id: 'again',
      label: 'Search', title: 'Look somewhere else',
      text: '<p>Search for another object or position at any time. The address in your browser always reopens the view you are looking at, so you can bookmark it or send it to someone.</p>',
      focus: () => [$('#header-search-slot')], radius: 26, place: ['bottom'],
    },
    {
      id: 'home',
      label: 'Home', title: 'Back to the start',
      text: '<p>The BeyondtheBlue logo always takes you back to the start page, to begin again with a new search.</p>',
      focus: () => [$('.logo')], radius: 8, place: ['bottom', 'right'],
    },
    {
      id: 'about',
      label: 'About', title: 'That’s the tour',
      text: '<p>About explains how BeyondtheBlue works, lists the keyboard shortcuts and credits the data. You can take this tour again from there.</p>',
      focus: () => [$('#btn-help')], radius: 8, place: ['bottom', 'left'], next: 'Finish',
    },
  ];

  // ───────────────────────── state ─────────────────────────
  let root = null, shade = null, ring = null, card = null, inner = null;
  let active = false, run = [], index = -1, raf = 0;
  let since = 0, mood = '', taskDone = '', bound = false, offs = [];
  let from = null, t0 = 0, drawn = null, kept = null, last = {};

  /** Listens for as long as the current step is shown. */
  function listen(target, type, fn) {
    if (!target) return;
    target.addEventListener(type, fn);
    offs.push(() => target.removeEventListener(type, fn));
  }
  function remember() { try { localStorage.setItem(SEEN_KEY, '1'); } catch (e) { /* storage is optional */ } }
  /** While the welcome is up, the page behind it cannot be reached by keyboard either. */
  function inertPage(on) {
    ['.skip-link', '.site-header', '#error', '#start', '#viewer'].forEach((sel) => { const el = $(sel); if (el) el.inert = on; });
  }
  /** A step is shown when its part of the page is there. */
  function available(step) {
    if (step.onStart && working()) return false;
    return step.focus().some(visible);
  }
  /** Steps counted in "3 of 13": the search they began with, every step to come, and the parts that appear with a field. */
  function counts(step) {
    if (step.onStart) return true;
    return !working() || available(step) || (['sheet', 'target'].includes(step.id) && !loaded() && !stuck());
  }
  const q = (sel) => inner.querySelector(sel);

  // ───────────────────────── building and showing ─────────────────────────
  function build() {
    if (root) return;
    root = document.createElement('div');
    root.className = 'tour';
    root.hidden = true;
    root.innerHTML = '<svg class="tour-shade" aria-hidden="true"><path/></svg><div class="tour-ring" hidden></div>'
      + '<div class="tour-card" role="dialog" aria-modal="false" aria-labelledby="tour-title" aria-describedby="tour-text" tabindex="-1">'
      + '<i class="tour-caret" aria-hidden="true"></i><div class="tour-body"></div></div>';
    page.appendChild(root);
    shade = root.querySelector('path');
    ring = root.querySelector('.tour-ring');
    card = root.querySelector('.tour-card');
    inner = root.querySelector('.tour-body');
    card.addEventListener('click', (ev) => {
      const b = ev.target.closest('button');
      if (!b) return;
      if (b.dataset.act === 'start') go(0, 1);
      else if (b.dataset.act === 'skip' || b.classList.contains('tour-end')) close();
      else if (b.classList.contains('tour-back')) go(index - 1, -1);
      else if (b.classList.contains('tour-next')) next();
    });
  }
  function open(greet) {
    if (active) close();
    build();
    active = true;
    run = STEPS.filter((s) => !s.onStart || !working());
    index = -1; from = null; drawn = null; kept = null; last = {};
    root.hidden = false;
    root.classList.toggle('is-greeting', Boolean(greet));     // the welcome arrives more slowly
    void root.offsetWidth;                                     // so the shade fades in
    root.classList.add('is-on');
    document.addEventListener('keydown', onKey, true);
    raf = requestAnimationFrame(tick);
    if (greet) welcome(); else go(0, 1);
  }
  function close() {
    if (!active) return;
    leave();
    active = false;
    cancelAnimationFrame(raf);
    document.removeEventListener('keydown', onKey, true);
    inertPage(false);
    remember();
    root.classList.remove('is-greeting', 'is-on');
    setTimeout(() => { if (!active) root.hidden = true; }, calm() ? 0 : 220);
  }
  function welcome() {
    index = -1;
    inertPage(true);
    card.className = 'tour-card is-welcome';
    card.dataset.side = 'none';
    const n = run.filter(counts).length;
    inner.innerHTML = '<p class="tour-label">Welcome</p>'
      + '<h2 class="tour-title" id="tour-title">First time here?</h2>'
      + '<div class="tour-text" id="tour-text"><p>SPHEREx, a NASA space telescope, maps the whole sky in infrared every six months. BeyondtheBlue plays its visits to any patch of sky as a short film, so you can see what moved or changed.</p></div>'
      + '<div class="tour-actions"><button type="button" class="tour-next" data-act="start">Take the tour</button>'
      + '<button type="button" class="text-button" data-act="skip">Skip</button></div>'
      + `<p class="tour-note">${n} short steps, about two minutes. You can also take the tour later from About.</p>`;
    fresh();
    q('[data-act="start"]').focus({ preventScroll: true });
  }
  function skeleton() {
    card.className = 'tour-card';
    inner.innerHTML = '<div class="tour-head"><span class="tour-label"></span><span class="tour-count"></span></div>'
      + '<h2 class="tour-title" id="tour-title"></h2><div class="tour-text" id="tour-text"></div>'
      + '<p class="tour-task" aria-live="polite"><b></b><span class="tour-task-text"></span></p>'
      + '<div class="tour-foot"><button type="button" class="text-button tour-end">End tour</button>'
      + '<button type="button" class="tour-back">Back</button><button type="button" class="tour-next"></button></div>';
  }
  /** Moves to step `i`, or the nearest one with something to show in direction `dir`. */
  function go(i, dir) {
    while (i >= 0 && i < run.length && !available(run[i])) i += dir;
    if (i < 0) return;
    if (i >= run.length) { close(); return; }
    leave();
    if (index < 0) { inertPage(false); remember(); skeleton(); root.classList.remove('is-greeting'); }
    index = i;
    since = Date.now(); taskDone = ''; bound = false; kept = null;
    from = drawn; t0 = performance.now();
    const step = run[index];
    if (step.enter) step.enter();
    render();
    fresh();
    reveal(step);
    // On the start screen the search box gets the focus, so its list of targets opens; not on touch screens,
    // where it would also open the keyboard.
    if (step.id === 'search' && window.matchMedia('(pointer: fine)').matches) $('#target-input').focus({ preventScroll: true });
    else card.focus({ preventScroll: true });
  }
  function leave() {
    offs.forEach((off) => off());
    offs = [];
  }
  function next() {
    const step = run[index];
    if (!step) return;
    if (step.act && !step.until()) { step.act(); return; }
    go(index + 1, 1);
  }
  function render() {
    const step = run[index];
    mood = step.mood ? step.mood() : '';
    const part = mood && step.moods ? { ...step, ...step.moods[mood] } : step;
    const list = run.filter(counts), total = list.length, n = Math.max(1, list.indexOf(step) + 1);
    q('.tour-label').textContent = part.label;
    q('.tour-count').textContent = `${n} of ${total}`;
    q('.tour-title').textContent = part.title;
    q('.tour-text').innerHTML = part.text;
    q('.tour-back').hidden = !run.slice(0, index).some(available);
    q('.tour-end').hidden = n === total;                                   // the last step has Finish
    const button = q('.tour-next');
    button.textContent = part.next || 'Next';
    button.disabled = Boolean(part.hold);
    q('.tour-foot').style.setProperty('--done', `${(n / total) * 100}%`);
    const task = part.task && (!part.task.when || part.task.when()) ? part.task : null;
    if (task && task.on && !bound) { bound = true; task.on((text) => { taskDone = text; paintTask(task); }); }
    paintTask(task);
  }
  function paintTask(task) {
    const box = q('.tour-task');
    box.hidden = !task;
    if (!task) return;
    box.classList.toggle('is-done', Boolean(taskDone));
    box.querySelector('b').innerHTML = taskDone ? CHECK : 'Try it';
    box.querySelector('.tour-task-text').textContent = taskDone || (typeof task.ask === 'function' ? task.ask() : task.ask);
  }
  /** Plays the card's arrival again, as menus arrive on this page. */
  function fresh() {
    card.classList.remove('is-new');
    void card.offsetWidth;
    card.classList.add('is-new');
  }
  function onKey(ev) {
    if (ev.key !== 'Escape' || !active) return;
    if ($('details.menu[open]') || !$('#target-suggest').classList.contains('hidden') || $('#help').open) return;   // Escape closes those first
    close();
  }

  // ───────────────────────── geometry ─────────────────────────
  /** The box around the visible elements of `els`, grown by `pad`; null when none is visible. */
  function area(els, pad) {
    let out = null;
    els.forEach((el) => {
      if (!visible(el)) return;
      const r = el.getBoundingClientRect();
      if (!r.width && !r.height) return;
      out = out ? { left: Math.min(out.left, r.left), top: Math.min(out.top, r.top), right: Math.max(out.right, r.right), bottom: Math.max(out.bottom, r.bottom) }
        : { left: r.left, top: r.top, right: r.right, bottom: r.bottom };
    });
    return out && { left: out.left - pad, top: out.top - pad, right: out.right + pad, bottom: out.bottom + pad };
  }
  const unite = (rects) => rects.filter(Boolean).reduce((a, b) => (a ? { left: Math.min(a.left, b.left), top: Math.min(a.top, b.top),
    right: Math.max(a.right, b.right), bottom: Math.max(a.bottom, b.bottom) } : b), null);
  const mix = (a, b, e) => ({ left: a.left + (b.left - a.left) * e, top: a.top + (b.top - a.top) * e,
    right: a.right + (b.right - a.right) * e, bottom: a.bottom + (b.bottom - a.bottom) * e, r: a.r + (b.r - a.r) * e });
  /** Cut-outs that touch become one, so the shade never shows in a sliver between them. */
  function join(list) {
    const out = list.map((r) => ({ ...r }));
    for (let i = 0; i < out.length; i++) {
      for (let j = i + 1; j < out.length; j++) {
        const a = out[i], b = out[j];
        if (a.left <= b.right + JOIN && b.left <= a.right + JOIN && a.top <= b.bottom + JOIN && b.top <= a.bottom + JOIN) {
          out[i] = { ...unite([a, b]), r: Math.min(a.r, b.r, 4) };
          out.splice(j, 1);
          i = -1;
          break;
        }
      }
    }
    return out;
  }
  function roundRect(h) {
    const x = Math.round(h.left), y = Math.round(h.top), w = Math.round(h.right) - x, ht = Math.round(h.bottom) - y;
    if (w <= 0 || ht <= 0) return '';
    const r = Math.max(0, Math.min(h.r || 0, w / 2, ht / 2));
    return `M${x + r} ${y}H${x + w - r}A${r} ${r} 0 0 1 ${x + w} ${y + r}V${y + ht - r}A${r} ${r} 0 0 1 ${x + w - r} ${y + ht}`
      + `H${x + r}A${r} ${r} 0 0 1 ${x} ${y + ht - r}V${y + r}A${r} ${r} 0 0 1 ${x + r} ${y}Z`;
  }
  /** Where the card goes on one side of `r`, level with the middle of `a`. */
  function beside(r, side, w, h, a) {
    const cx = (a.left + a.right) / 2, cy = (a.top + a.bottom) / 2;
    const x = Math.min(Math.max(cx - w / 2, EDGE), window.innerWidth - w - EDGE);
    const y = Math.min(Math.max(cy - h / 2, EDGE), window.innerHeight - h - EDGE);
    if (side === 'right') return { side, x: r.right + GAP, y };
    if (side === 'left') return { side, x: r.left - GAP - w, y };
    if (side === 'bottom') return { side, x, y: r.bottom + GAP };
    return { side, x, y: r.top - GAP - h };
  }
  const within = (s, w, h) => s.x >= EDGE - 1 && s.y >= EDGE - 1 && s.x + w <= window.innerWidth - EDGE + 1 && s.y + h <= window.innerHeight - EDGE + 1;
  const hits = (s, w, h, r) => s.x < r.right && s.x + w > r.left && s.y < r.bottom && s.y + h > r.top;
  /** How much of `r` the card would cover at `s`, in square pixels. */
  const cover = (s, w, h, r) => (r ? Math.max(0, Math.min(s.x + w, r.right) - Math.max(s.x, r.left)) * Math.max(0, Math.min(s.y + h, r.bottom) - Math.max(s.y, r.top)) : 0);
  const phone = () => window.innerWidth <= 760;
  const sidesFor = (step) => (phone() ? step.phone || ['bottom', 'top'] : step.place || ['right', 'left', 'bottom', 'top']);
  /**
   * The card goes beside what it explains when that leaves the image in view; otherwise beside the
   * image and the control together, level with the control; otherwise over the image; and when the
   * window is too small for any of that, at the edge of the window where it hides least.
   */
  function place(step, focus, anchor, shows, w, h) {
    if (!anchor) return dock(focus, anchor, w, h);
    const sides = sidesFor(step);
    const passes = [
      { around: anchor, avoid: [focus, ...shows], caret: true },
      { around: unite([focus, ...shows]), avoid: [], caret: false },
      { around: anchor, avoid: [focus], caret: true },
    ];
    const attempt = (p, side) => {
      const pass = passes[p], s = beside(pass.around, side, w, h, anchor);
      return within(s, w, h) && !pass.avoid.some((r) => r && hits(s, w, h, r)) ? { ...s, caret: pass.caret } : null;
    };
    if (kept) { const s = attempt(kept.p, kept.side); if (s) return s; }   // stay put while it still fits
    for (let p = 0; p < passes.length; p++) {
      for (const side of sides) {
        const s = attempt(p, side);
        if (s) { kept = { p, side }; return s; }
      }
    }
    kept = null;
    return dock(focus, anchor, w, h);
  }
  function dock(focus, anchor, w, h) {
    const W = window.innerWidth, H = window.innerHeight;
    let best = null;
    [H - h - EDGE, EDGE].forEach((y) => [(W - w) / 2, EDGE, W - w - EDGE].forEach((x) => {
      const s = { side: 'none', x: Math.max(EDGE, x), y: Math.max(EDGE, y), caret: false };
      const cost = 3 * cover(s, w, h, anchor) + cover(s, w, h, focus);
      if (!best || cost < best.cost) best = { ...s, cost };
    }));
    return best;
  }
  /**
   * Scrolls what a step explains into view, no further than needed: all of it when it fits, else the
   * part the card points at. On a phone the card goes above or under it, so the card has to fit in too.
   */
  function reveal(step) {
    const header = $('.site-header');
    const sticky = Boolean(header) && getComputedStyle(header).position === 'sticky';
    const room = phone() ? card.offsetHeight + GAP : 0, side = sidesFor(step)[0];
    const top = (sticky ? header.getBoundingClientRect().bottom : 0) + EDGE + (side === 'top' ? room : 0);
    const bottom = window.innerHeight - EDGE - (side === 'bottom' ? room : 0);
    let els = step.focus().filter(visible), r = area(els, PAD);
    if (r && r.bottom - r.top > bottom - top && step.anchor) { els = step.anchor().filter(visible); r = area(els, PAD); }
    if (!r || (sticky && els.every((el) => header.contains(el)))) return;      // in the header: always in view
    let dy = 0;
    if (r.top < top || r.bottom - r.top > bottom - top) dy = r.top - top;             // above the window, or too tall: its top comes in
    else if (r.bottom > bottom) dy = r.bottom - bottom;                               // below: just enough to bring it in
    if (Math.abs(dy) > 1) window.scrollBy({ top: dy, behavior: calm() ? 'auto' : 'smooth' });
  }

  // ───────────────────────── every frame ─────────────────────────
  // The parts of the page move (menus open, the page scrolls, a field loads), so the cut-out, the
  // outline and the card are measured again on every frame; the page is only touched when they move.
  function tick(now) {
    raf = requestAnimationFrame(tick);
    const step = index >= 0 ? run[index] : null;
    if (step && step.until && step.until()) { go(index + 1, 1); return; }
    if (step && step.mood && step.mood() !== mood) { render(); fresh(); reveal(step); }   // new words, new height
    const focus = step ? area(step.focus(), PAD) : null;
    const anchor = step && step.anchor ? area(step.anchor(), PAD) : focus;
    const shows = step && step.show ? [area(step.show(), 0)].filter(Boolean) : [];
    let shape = focus ? { ...focus, r: Math.min(step.radius || 4, (focus.bottom - focus.top) / 2, (focus.right - focus.left) / 2) } : null;
    if (shape && from && !calm()) {                         // the outline glides from the last step to this one
      const k = Math.min(1, (now - t0) / 280);
      if (k < 1) shape = mix(from, shape, 1 - (1 - k) ** 3);
    }
    drawn = shape;

    const W = window.innerWidth, H = window.innerHeight;
    let d = `M0 0H${W}V${H}H0Z`;
    join([shape, ...shows.map((r) => ({ ...r, r: 2 }))].filter(Boolean)).forEach((hole) => { d += roundRect(hole); });
    if (d !== last.d) { shade.setAttribute('d', d); last.d = d; }

    const outline = shape ? `left:${Math.round(shape.left) - 2}px;top:${Math.round(shape.top) - 2}px;width:${Math.round(shape.right) - Math.round(shape.left) + 4}px;`
      + `height:${Math.round(shape.bottom) - Math.round(shape.top) + 4}px;border-radius:${Math.round(shape.r) + 2}px` : '';
    if (outline !== last.outline) { ring.hidden = !shape; ring.style.cssText = outline; last.outline = outline; }

    const w = card.offsetWidth, h = card.offsetHeight;
    const s = step ? place(step, focus, anchor, shows, w, h)
      : { side: 'none', x: Math.max(EDGE, (W - w) / 2), y: Math.max(EDGE, (H - h) / 2), caret: false };
    let at = 0;
    if (s.caret) {
      const along = s.side === 'left' || s.side === 'right';
      const mid = along ? (anchor.top + anchor.bottom) / 2 - s.y : (anchor.left + anchor.right) / 2 - s.x;
      at = Math.min(Math.max(mid, 36), (along ? h : w) - 20);
    }
    const spot = `${Math.round(s.x)}|${Math.round(s.y)}|${s.caret ? s.side : 'none'}|${Math.round(at)}`;
    if (spot !== last.spot) {
      card.style.left = `${Math.round(s.x)}px`;
      card.style.top = `${Math.round(s.y)}px`;
      card.dataset.side = s.caret ? s.side : 'none';
      card.style.setProperty('--caret', `${Math.round(at)}px`);
      last.spot = spot;
    }
  }

  // ───────────────────────── when it opens ─────────────────────────
  const again = $('#btn-tour');
  if (again) again.addEventListener('click', () => { const help = $('#help'); if (help.open) help.close(); open(false); });
  let seen = true;
  try { seen = localStorage.getItem(SEEN_KEY) === '1'; } catch (e) { /* without storage the tour could not remember being seen, so it waits to be asked */ }
  if (seen && new URLSearchParams(location.search).get('tour') !== '1') return;
  // The visitor sees the start page first: the welcome comes once the fonts are in, and never
  // sooner than 1.2 s after the page began to load.
  const fonts = document.fonts && document.fonts.ready ? document.fonts.ready : Promise.resolve();
  fonts.then(() => setTimeout(() => { if (!active && !$('#help').open) open(true); }, Math.max(300, 1200 - performance.now())));
})();
