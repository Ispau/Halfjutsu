/* Halfjutsu front end — Halfjourney Lab 2026 */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const fmtTime = (s) => { s = Math.max(0, Math.round(s)); const m = Math.floor(s / 60); return `${m}:${String(s % 60).padStart(2, '0')}`; };
const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
const EASE = { out: 'cubic-bezier(.16,1,.3,1)', inOut: 'cubic-bezier(.65,0,.35,1)', spring: 'cubic-bezier(.34,1.56,.64,1)',
  rise: 'cubic-bezier(.2,.75,.45,1)', fall: 'cubic-bezier(.55,0,.8,.35)' };
const state = { file: null, duration: 0, edited: null, jobId: null, lastMsg: '', samples: [], lastWindow: 0, lastPhase: '', milestone: 0, gpu: [] };

function makeNinja() { return $('#ninja-tpl').content.firstElementChild.cloneNode(true); }

/* ================================================================ INTRO */
const intro = $('#intro');
let introTimers = [], introDone = false;
const later = (ms, fn) => introTimers.push(setTimeout(fn, ms));

function splitTitle() {
  for (const part of $$('#title > span')) {
    part.innerHTML = [...part.textContent].map((c) => `<span class="ch">${c}</span>`).join('');
  }
  const sheen = document.createElement('span'); sheen.className = 'sheen'; sheen.textContent = $('#title').textContent;
  $('#title').appendChild(sheen);
}

function playIntro() {
  document.body.classList.add('locked');
  splitTitle();
  if (reduced) { later(300, revealTitle); later(1600, exitIntro); return; }

  const W = innerWidth, H = innerHeight, g = H * 0.5 + 70;        // g: ground line (feet)
  const wrap = $('#ninja'); wrap.appendChild(makeNinja());
  const at = (x, y, r = 0, sx = 1, sy = 1) => `translate(${x - 75}px, ${y - 175}px) rotate(${r}deg) scale(${sx}, ${sy})`;
  // Two hops with flips, squash on every landing, stretch on take-off, settle with overshoot.
  const path = [
    { offset: 0,    transform: at(-180, g, 0),                       easing: EASE.rise },
    { offset: .17,  transform: at(W * .21, g - 175, -200),            easing: EASE.fall },
    { offset: .33,  transform: at(W * .33, g, -360, 1.28, .72),       easing: 'ease-out' },
    { offset: .39,  transform: at(W * .36, g - 14, -360, .88, 1.16),  easing: EASE.rise },
    { offset: .60,  transform: at(W * .44, g - 250, -720),            easing: EASE.fall },
    { offset: .78,  transform: at(W * .5, g, -1080, 1.32, .7),        easing: EASE.spring },
    { offset: .88,  transform: at(W * .5, g, -1080, .94, 1.07),       easing: 'ease-out' },
    { offset: 1,    transform: at(W * .5, g, -1080, 1, 1) },
  ];
  const dur = 1750;
  wrap.style.transformOrigin = '50% 62%';
  wrap.animate(path, { duration: dur, fill: 'forwards' });
  // motion trail: delayed, desaturated copies
  [0.22, 0.13, 0.07].forEach((op, i) => {
    const ghost = document.createElement('div'); ghost.className = 'ghost'; ghost.style.opacity = op; ghost.style.transformOrigin = '50% 62%';
    ghost.appendChild(makeNinja()); $('#stage').insertBefore(ghost, wrap);
    ghost.animate(path, { duration: dur, delay: 45 * (i + 1), fill: 'forwards' });
    later(dur + 45 * (i + 1), () => ghost.animate([{ opacity: op }, { opacity: 0 }], { duration: 200, fill: 'forwards' }));
  });
  // contact shadow follows x and shrinks with height
  const sh = (x, y, s, o) => ({ transform: `translate(${x}px, ${g + 6}px) scale(${s})`, opacity: o });
  $('#shadow').animate([
    { ...sh(-180, 0, 1, .7), offset: 0, easing: EASE.rise }, { ...sh(W * .21, 0, .35, .2), offset: .17, easing: EASE.fall },
    { ...sh(W * .33, 0, 1.2, .8), offset: .33 }, { ...sh(W * .36, 0, 1, .7), offset: .39, easing: EASE.rise },
    { ...sh(W * .44, 0, .3, .15), offset: .6, easing: EASE.fall }, { ...sh(W * .5, 0, 1.25, .85), offset: .78 }, { ...sh(W * .5, 0, 1, .7), offset: 1 },
  ], { duration: dur, fill: 'forwards' });
  // speed lines while airborne
  [120, 260, 420, 820, 980, 1120].forEach((t, i) => later(t, () => {
    const line = document.createElement('i');
    line.style.left = `${W * (0.05 + t / dur * 0.42)}px`; line.style.top = `${g - 120 - (i % 3) * 38}px`; line.style.width = `${80 + (i % 3) * 40}px`;
    $('#speedlines').appendChild(line); setTimeout(() => line.remove(), 600);
  }));
  // the vanish: hold, flash, smoke + sparks, camera shake
  later(dur + 160, () => vanish(W * .5, g - 95));
  later(dur + 260, revealTitle);
  later(4700, exitIntro);
}

function vanish(x, y) {
  const wrap = $('#ninja');
  wrap.animate([{ opacity: 1, filter: 'brightness(1)' }, { opacity: 0, filter: 'brightness(4)' }], { duration: 140, fill: 'forwards' });
  $('#shadow').animate([{ opacity: .7 }, { opacity: 0 }], { duration: 200, fill: 'forwards' });
  $('#flash').animate([{ opacity: 0 }, { opacity: .85, offset: .25 }, { opacity: 0 }], { duration: 320, easing: 'ease-out' });
  intro.classList.add('shake');
  const burst = $('#burst'); burst.style.left = `${x}px`; burst.style.top = `${y}px`;
  for (let i = 0; i < 16; i++) {                                   // smoke puffs
    const p = document.createElement('i'); const size = 40 + Math.random() * 70; const a = Math.random() * Math.PI * 2; const d = 40 + Math.random() * 120;
    Object.assign(p.style, { width: `${size}px`, height: `${size}px`, left: `${-size / 2}px`, top: `${-size / 2}px` });
    burst.appendChild(p);
    p.animate([{ transform: 'scale(.2)', opacity: .95 }, { transform: `translate(${Math.cos(a) * d}px, ${Math.sin(a) * d - 30}px) scale(${1.6 + Math.random()})`, opacity: 0 }],
      { duration: 900 + Math.random() * 600, easing: EASE.out, fill: 'forwards' });
  }
  for (let i = 0; i < 12; i++) {                                   // sparks
    const s = document.createElement('b'); const a = (i / 12) * 360 + Math.random() * 20; burst.appendChild(s);
    s.animate([{ transform: `rotate(${a}deg) translateY(10px) scaleY(.2)`, opacity: 1 }, { transform: `rotate(${a}deg) translateY(150px) scaleY(1)`, opacity: 0 }],
      { duration: 520, easing: EASE.out, fill: 'forwards' });
  }
}

function revealTitle() {
  const title = $('#title');
  $('#slash').animate([{ transform: 'rotate(-8deg) scaleX(0)', opacity: 1, transformOrigin: '0 50%' }, { transform: 'rotate(-8deg) scaleX(1)', opacity: 1, offset: .6 },
    { transform: 'rotate(-8deg) scaleX(1)', opacity: 0 }], { duration: 520, easing: EASE.out, fill: 'forwards' });
  title.animate([{ clipPath: 'inset(-20% 100% -20% 0)' }, { clipPath: 'inset(-20% 0 -20% 0)' }], { duration: 480, easing: EASE.out, fill: 'forwards' });
  $$('#title .ch').forEach((ch, i) => {
    const r = (i % 2 ? 1 : -1) * (6 + (i % 3) * 3);
    ch.animate([{ opacity: 0, transform: `translateY(55%) rotate(${r}deg) skewX(-14deg)`, filter: 'blur(14px)' },
      { opacity: 1, transform: 'none', filter: 'blur(0)' }], { duration: 950, delay: 40 + i * 42, easing: EASE.spring, fill: 'forwards' });
  });
  $('.sheen').animate([{ transform: 'translateX(-120%)' }, { transform: 'translateX(120%)' }], { duration: 1100, delay: 1150, easing: EASE.inOut, fill: 'forwards' });
  $('#kicker').animate([{ opacity: 0, letterSpacing: '1.1em' }, { opacity: 1, letterSpacing: '.5em' }], { duration: 1100, delay: 350, easing: EASE.out, fill: 'forwards' });
  $('#brush path').animate([{ strokeDashoffset: 700 }, { strokeDashoffset: 0 }], { duration: 750, delay: 700, easing: EASE.inOut, fill: 'forwards' });
  $('#sub').animate([{ opacity: 0, transform: 'translateY(12px)', filter: 'blur(6px)' }, { opacity: 1, transform: 'none', filter: 'blur(0)' }],
    { duration: 800, delay: 1050, easing: EASE.out, fill: 'forwards' });
}

function exitIntro() {
  if (introDone) return; introDone = true;
  introTimers.forEach(clearTimeout);
  const top = $('#curtain-top'), bottom = $('#curtain-bottom');
  top.style.visibility = bottom.style.visibility = 'visible';
  $('.intro-type').animate([{ opacity: 1, transform: 'scale(1)' }, { opacity: 0, transform: 'scale(1.08)', filter: 'blur(8px)' }], { duration: 450, easing: EASE.inOut, fill: 'forwards' });
  $('.stage').animate([{ opacity: 1 }, { opacity: 0 }], { duration: 300, fill: 'forwards' });
  $('.intro-bg').animate([{ opacity: 1 }, { opacity: 0 }], { duration: 400, fill: 'forwards' });
  $('#skip').remove();
  setTimeout(() => {
    intro.style.background = 'transparent';
    document.body.classList.remove('locked'); document.body.classList.add('ready');
    revealCards();
    const opts = { duration: 900, easing: EASE.inOut, fill: 'forwards' };
    top.animate([{ transform: 'translateY(0)' }, { transform: 'translateY(-101%)' }], opts);
    bottom.animate([{ transform: 'translateY(0)' }, { transform: 'translateY(101%)' }], opts).onfinish = () => intro.remove();
  }, 380);
}

$('#skip').addEventListener('click', (e) => { e.stopPropagation(); exitIntro(); });
intro.addEventListener('click', exitIntro);
addEventListener('keydown', (e) => { if (!introDone && (e.key === 'Enter' || e.key === 'Escape')) exitIntro(); });
playIntro();

/* ================================================================ AMBIENT INTERACTIONS */
function revealCards() {
  $$('.card').forEach((card, i) => setTimeout(() => card.classList.add('in'), 250 + i * 120));
  setTimeout(placePills, 50);
}

if (!reduced) {
  for (const card of $$('.card.tilt')) {
    card.addEventListener('pointermove', (e) => {
      const r = card.getBoundingClientRect(); const x = (e.clientX - r.left) / r.width, y = (e.clientY - r.top) / r.height;
      card.style.setProperty('--ry', `${(x - .5) * 5}deg`); card.style.setProperty('--rx', `${(.5 - y) * 5}deg`);
      card.style.setProperty('--mx', `${x * 100}%`); card.style.setProperty('--my', `${y * 100}%`);
    });
    card.addEventListener('pointerleave', () => { card.style.setProperty('--rx', '0deg'); card.style.setProperty('--ry', '0deg'); });
  }
  for (const btn of $$('.magnetic')) {
    btn.addEventListener('pointermove', (e) => {
      if (btn.disabled) return;
      const r = btn.getBoundingClientRect();
      btn.style.setProperty('--tx', `${(e.clientX - r.left - r.width / 2) * .18}px`); btn.style.setProperty('--ty', `${(e.clientY - r.top - r.height / 2) * .3}px`);
    });
    btn.addEventListener('pointerleave', () => { btn.style.setProperty('--tx', '0px'); btn.style.setProperty('--ty', '0px'); });
  }
}

/* ================================================================ SYSTEM METERS */
async function pollSystem() {
  try {
    const s = await (await fetch('/api/system')).json();
    if (s.gpu) {
      $('#sys-text').textContent = `${s.gpu.replace('NVIDIA GeForce ', '')} · VRAM ${(s.vram_used / 1024).toFixed(1)}/${(s.vram_total / 1024).toFixed(0)} GB · GPU ${s.gpu_util}%`;
      $('#m-vram').style.width = `${s.vram_used / s.vram_total * 100}%`; $('#t-vram').textContent = `${(s.vram_used / 1024).toFixed(1)} GB`;
      $('#m-gpu').style.width = `${s.gpu_util}%`; $('#t-gpu').textContent = `${s.gpu_util}%`;
      state.gpu.push(s.gpu_util); if (state.gpu.length > 80) state.gpu.shift();
      const pts = state.gpu.map((v, i) => `${(i / 79) * 200},${40 - v * 0.38}`);
      $('#spark-line').setAttribute('d', 'M' + pts.join(' L'));
    }
    if (s.ram_free_gb != null) { $('#m-ram').style.width = `${s.ram_free_gb / (s.ram_total_gb || 32) * 100}%`; $('#t-ram').textContent = `${s.ram_free_gb} GB`; }
  } catch { $('#sys-text').textContent = 'Server unavailable'; }
}
pollSystem();
let sysTimer = setInterval(pollSystem, 3000);

/* ================================================================ INPUTS */
const videoInput = $('#video'), editedInput = $('#edited'), preview = $('#video-preview');
const firstCanvas = $('#first-frame'), beforeCanvas = $('#cmp-before');

for (const zone of $$('.drop')) {
  zone.addEventListener('dragenter', () => zone.classList.add('drag'));
  zone.addEventListener('dragover', (e) => { e.preventDefault(); zone.classList.add('drag'); });
  ['dragleave', 'drop'].forEach((ev) => zone.addEventListener(ev, () => zone.classList.remove('drag')));
}
function pop(zone) { zone.classList.add('filled'); zone.classList.remove('pop'); void zone.offsetWidth; zone.classList.add('pop'); }

function markStep(n, ok, text) {
  const el = $(`#s${n}`); el.querySelector('b').textContent = text; el.classList.toggle('ok', ok);
  document.querySelector(`.card[data-step="${n}"]`).classList.toggle('ok', ok);
  const ready = Boolean(state.file && state.edited);
  const submit = $('#submit');
  if (ready && submit.disabled) { submit.disabled = false; submit.classList.add('armed'); }
  if (!ready) submit.disabled = true;
}

videoInput.addEventListener('change', () => {
  const file = videoInput.files[0]; if (!file) return;
  state.file = file;
  preview.src = URL.createObjectURL(file); preview.hidden = false;
  $('#drop-video .drop-empty').hidden = true; pop($('#drop-video'));
  preview.addEventListener('loadedmetadata', () => {
    state.duration = preview.duration;
    $('#video-meta').textContent = `${file.name} · ${preview.videoWidth}×${preview.videoHeight} · ${state.duration.toFixed(1)} s`;
    setupTimeRange(); preview.currentTime = 0.001;
  }, { once: true });
  preview.addEventListener('seeked', () => {
    markStep(1, true, 'ready'); markStep(2, true, 'pick, download & edit');
    preview.play().catch(() => {});
  }, { once: true });
  frameSrc.src = preview.src;
  frameSrc.addEventListener('loadedmetadata', () => { frameAt.min = 0; frameAt.max = lastFrame(); frameAt.value = 0; syncTime(); seekFrame(); }, { once: true });
});

/* Frame picker: a hidden twin of the preview seeks to any frame of the chosen range and paints it.
   Works in whole frames at 24 fps (the rate the engine normalises to), with Photoshop-style timecode. */
const FPS = 24;
const frameSrc = $('#frame-src'), frameAt = $('#frame-at'), frameTc = $('#frame-tc');
let frameBusy = false, framePending = false;
const lastFrame = () => Math.max(0, Math.floor((frameSrc.duration || 0) * FPS) - 1);
function toTimecode(f) {
  const ff = f % FPS, s = Math.floor(f / FPS);
  return `${Math.floor(s / 3600)}:${String(Math.floor(s / 60) % 60).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}:${String(ff).padStart(2, '0')}`;
}
function parseTimecode(text) {          // "0:00:02:19", "2:19" (s:ff), "2.79" (seconds)
  text = text.trim();
  if (/^\d+([.,]\d+)?$/.test(text)) return Math.round(Number(text.replace(',', '.')) * FPS);
  const parts = text.split(/[:;]/).map(Number);
  if (!parts.length || parts.some((n) => !Number.isFinite(n))) return null;
  const ff = parts.pop(), [ss = 0, mm = 0, hh = 0] = parts.reverse();
  return ((hh * 60 + mm) * 60 + ss) * FPS + ff;
}
function setFrame(f) {
  frameAt.value = Math.min(Number(frameAt.max), Math.max(Number(frameAt.min), f)); seekFrame();
}
function seekFrame() {
  const f = Math.min(Number(frameAt.value), lastFrame());
  $('#frame-out').textContent = `${(f / FPS).toFixed(2)} s`; frameTc.value = toTimecode(f); frameTc.classList.remove('bad');
  paintRange(frameAt);
  if (frameBusy) { framePending = true; return; }
  frameBusy = true; frameSrc.currentTime = (f + 0.5) / FPS;    // middle of the frame: never lands on the previous one
}
frameSrc.addEventListener('seeked', () => {
  for (const c of [firstCanvas, beforeCanvas]) { c.width = frameSrc.videoWidth; c.height = frameSrc.videoHeight; c.getContext('2d').drawImage(frameSrc, 0, 0); }
  firstCanvas.hidden = false; $('#frame-pick').hidden = false; $('#grab').disabled = false;
  frameBusy = false; if (framePending) { framePending = false; seekFrame(); }
});
frameAt.addEventListener('input', seekFrame);
$('#frame-prev').addEventListener('click', () => setFrame(Number(frameAt.value) - 1));
$('#frame-next').addEventListener('click', () => setFrame(Number(frameAt.value) + 1));
frameTc.addEventListener('change', () => {
  const f = parseTimecode(frameTc.value);
  if (f === null || f < Number(frameAt.min) || f > Number(frameAt.max)) { frameTc.classList.add('bad'); return; }
  setFrame(f);
});
frameTc.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') { e.preventDefault(); frameTc.dispatchEvent(new Event('change')); }
  if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') { e.preventDefault(); setFrame(Number(frameAt.value) + (e.key === 'ArrowRight' ? 1 : -1)); }
});
function clampFrame(start, dur) {          // keep the picker inside the clip that will be rendered
  if (!frameSrc.src) return;
  const before = Number(frameAt.value), lo = Math.round(start * FPS);
  frameAt.min = lo; frameAt.max = Math.max(lo, Math.min(lastFrame() || Infinity, Math.round((start + dur) * FPS) - 1));
  if (before < lo || before > Number(frameAt.max)) frameAt.value = lo;
  if (Number(frameAt.value) !== before) seekFrame(); else paintRange(frameAt);
}

$('#grab').addEventListener('click', () => {
  firstCanvas.toBlob((blob) => {
    const a = document.createElement('a'); a.href = URL.createObjectURL(blob);
    a.download = state.file.name.replace(/\.[^.]+$/, '') + `_frame_${toTimecode(Number(frameAt.value)).replace(/:/g, '-')}.png`; a.click();
  }, 'image/png');
});

editedInput.addEventListener('change', () => {
  const file = editedInput.files[0]; if (!file) return;
  state.edited = file;
  $('#cmp-after').src = URL.createObjectURL(file);
  $('#wipe').hidden = false; $('#drop-frame .drop-empty').hidden = true; pop($('#drop-frame'));
  markStep(3, true, 'ready');
  sweepWipe($('#wipe'));
});

function bindWipe(el) {
  const set = (e) => { const r = el.getBoundingClientRect(); el.style.setProperty('--pos', `${Math.min(100, Math.max(0, (e.clientX - r.left) / r.width * 100))}%`); };
  el.addEventListener('pointerdown', (e) => { e.preventDefault(); el.setPointerCapture(e.pointerId); set(e); el.onpointermove = set; });
  el.addEventListener('pointerup', () => { el.onpointermove = null; });
  el.addEventListener('click', (e) => { e.preventDefault(); e.stopPropagation(); });
}
function sweepWipe(el) {   // a quick reveal sweep shows the user they can drag
  if (reduced) return;
  let t0 = null; const step = (t) => { t0 ??= t; const k = Math.min(1, (t - t0) / 1400);
    const v = 50 + Math.sin(k * Math.PI * 2) * 38 * (1 - k); el.style.setProperty('--pos', `${v}%`); if (k < 1) requestAnimationFrame(step); };
  requestAnimationFrame(step);
}
bindWipe($('#wipe')); bindWipe($('#result-wipe'));

/* ================================================================ PARAMETERS */
function placePills() {
  for (const seg of $$('.seg')) {
    const on = $('button.on', seg), pill = $('.pill', seg); if (!on) continue;
    pill.style.width = `${on.offsetWidth}px`; pill.style.transform = `translateX(${on.offsetLeft}px)`;
  }
}
addEventListener('resize', placePills);
document.fonts?.ready.then(placePills);   // button widths change once the web fonts arrive
for (const card of $$('.card')) card.addEventListener('transitionend', placePills, { once: true });
for (const seg of $$('.seg')) {
  seg.addEventListener('click', (e) => {
    const btn = e.target.closest('button'); if (!btn) return;
    $$('button', seg).forEach((b) => b.classList.toggle('on', b === btn));
    document.querySelector(`input[name="${seg.dataset.name}"]`).value = btn.dataset.value;
    placePills(); updateEstimate();
  });
}
function paintRange(el) { const k = (el.value - el.min) / ((el.max - el.min) || 1) * 100; el.style.setProperty('--fill', `${k}%`); }
const bind = (id, out, fmt = (v) => v) => { const el = $(id), o = $(out); const f = () => { o.textContent = fmt(el.value); paintRange(el); updateEstimate(); }; el.addEventListener('input', f); f(); };
bind('#shift', '#shift-out', (v) => Number(v).toFixed(1));
bind('#win', '#win-out');
bind('#ov', '#ov-out');
$('#dice').addEventListener('click', (e) => { $('#seed').value = Math.floor(Math.random() * 2 ** 31); const d = e.currentTarget; d.classList.remove('roll'); void d.offsetWidth; d.classList.add('roll'); });
$('#audio').addEventListener('change', () => { $('#audio-field').value = $('#audio').checked ? 'true' : 'false'; });
$('#lipsync').addEventListener('change', () => { $('#lipsync-field').value = $('#lipsync').checked ? 'true' : 'false'; });

const startEl = $('#start'), durEl = $('#duration');
function setupTimeRange() {
  const total = Math.floor(state.duration * 10) / 10;
  startEl.max = Math.max(0, total - 2).toFixed(1); startEl.value = 0;
  durEl.min = Math.min(2, total).toFixed(1); durEl.max = total.toFixed(1); durEl.value = total.toFixed(1);
  syncTime();
}
function syncTime() {
  const total = state.duration, start = Number(startEl.value);
  durEl.max = Math.max(2, total - start).toFixed(1);
  if (Number(durEl.value) > Number(durEl.max)) durEl.value = durEl.max;
  const dur = Number(durEl.value);
  $('#start-out').textContent = `${start.toFixed(1)} s`; $('#dur-out').textContent = `${dur.toFixed(1)} s`;
  paintRange(startEl); paintRange(durEl);
  const full = start < 0.05 && Math.abs(dur - total) < 0.15;
  $('#duration-field').value = full ? '' : dur.toFixed(2);
  $('#time-label').textContent = full ? 'full video' : `${start.toFixed(1)}–${(start + dur).toFixed(1)} s`;
  if (total) { $('#tb-range').style.left = `${start / total * 100}%`; $('#tb-range').style.width = `${dur / total * 100}%`; }
  if (preview.src && !full) preview.currentTime = start;
  clampFrame(start, dur);
  updateEstimate();
}
startEl.addEventListener('input', syncTime);
durEl.addEventListener('input', syncTime);

let lastEstimate = '';
function updateEstimate() {
  if (!state.duration) return;
  const dur = Number(durEl.value) || state.duration;
  const steps = Number($('input[name="steps"]').value), res = $('input[name="resolution"]').value;
  // Measured on the RTX 5080: 19 s at 720p, 3 steps ≈ 16 min (≈ 47 s per second + ~75 s loading).
  const perSecond = res === '720p' ? 47 : 21;
  const seconds = 75 + dur * perSecond * (0.35 + 0.65 * steps / 3);
  const text = `≈ ${Math.max(1, Math.round(seconds / 60))} min`;
  const b = $('#eta-est');
  if (text !== lastEstimate) { b.textContent = text; b.classList.remove('tick'); void b.offsetWidth; b.classList.add('tick'); lastEstimate = text; }
  const win = Number($('#win').value), ov = Number($('#ov').value);
  const windows = Math.max(1, Math.ceil((dur * 24 - ov) / (win - ov)));
  $('#eta-detail').textContent = `${dur.toFixed(1)} s · ${res} · ${steps} steps · ${windows} window${windows > 1 ? 's' : ''}`;
}

/* ================================================================ SUBMIT */
$('#job').addEventListener('submit', async (e) => {
  e.preventDefault();
  const submit = $('#submit');
  submit.disabled = true; submit.querySelector('span').textContent = 'Uploading…';
  const data = new FormData($('#job'));
  for (const [k, v] of [...data.entries()]) if (v === '') data.delete(k);
  try {
    const res = await fetch('/api/jobs', { method: 'POST', body: data });
    const job = await res.json();
    if (!res.ok) throw new Error(job.detail || 'Could not create the job');
    const run = await fetch(`/api/jobs/${job.id}/run`, { method: 'POST' });
    if (!run.ok) throw new Error((await run.json()).detail || 'Could not start');
    startRun(job);
  } catch (err) { alert(err.message); }
  finally { submit.disabled = false; submit.querySelector('span').textContent = 'Generate video'; }
});

/* ================================================================ PROGRESS */
const TIPS = [
  'Viggle carries your edited frame through the whole video: the look comes from that image, the motion from your footage.',
  'Each window covers up to 124 frames and shares 18 with the previous one, so the joins stay invisible.',
  'Viggle is distilled: 3 steps per window get what other models need 20+ steps for.',
  'Everything happens on your RTX 5080 — not a single frame leaves your machine.',
  'The original audio is trimmed and re-attached at the end, in sync with your range.',
  'Tip: an edited frame with good light and photographic texture sets the ceiling for the whole video.',
  'SageAttention nearly doubles attention speed without changing the result.',
  'One continuous shot works best: the model follows the scene from start to finish.',
];
let tipIndex = 0, tipTimer = null, pollTimer = null, clockTimer = null, typingTimer = null;

function rotateTip() { const t = $('#tip'); t.textContent = TIPS[tipIndex++ % TIPS.length]; t.classList.remove('swap'); void t.offsetWidth; t.classList.add('swap'); }

function show(id) {
  for (const s of ['#run', '#done', '#fail']) $(s).hidden = s !== id;
  if (id) { const el = $(id); el.classList.remove('enter'); void el.offsetWidth; el.classList.add('enter'); }
}

/* The running job survives reloads and closed tabs: its id lives in localStorage (or ?job=<id>). */
const JOB_KEY = 'halfjutsu.job';
const saveJob = (id) => { try { id ? localStorage.setItem(JOB_KEY, id) : localStorage.removeItem(JOB_KEY); } catch {} };
const savedJob = () => { try { return localStorage.getItem(JOB_KEY); } catch { return null; } };

async function resumeJob() {
  const fromUrl = new URLSearchParams(location.search).get('job');
  if (fromUrl) { saveJob(fromUrl); history.replaceState(null, '', location.pathname); }
  const id = fromUrl || savedJob();
  if (!id) return;
  let job;
  try { const r = await fetch(`/api/jobs/${id}`); if (!r.ok) throw 0; job = await r.json(); }
  catch { saveJob(null); return; }                     // server restarted or job unknown
  if (!['running', 'queued', 'completed', 'failed'].includes(job.status)) { saveJob(null); return; }
  exitIntro();
  startRun(job, true);
}

function startRun(job, resumed = false) {
  saveJob(job.id);
  state.finishedBefore = resumed && job.status === 'completed';   // its real render time is unknown here
  Object.assign(state, { jobId: job.id, lastMsg: '', samples: [], lastWindow: 0, startedAt: null, lastPhase: '', milestone: 0 });
  $('.hero').hidden = true; $('#job').hidden = true; show('#run');
  document.body.classList.add('rendering');
  $('#log').innerHTML = ''; $('#windows').innerHTML = ''; $('#stepdots').innerHTML = '';
  $('#runner').innerHTML = ''; const n = makeNinja(); n.classList.add('running'); $('#runner').appendChild(n);
  scrollTo({ top: 0, behavior: 'smooth' });
  rotateTip(); tipTimer = setInterval(rotateTip, 7000);
  clearInterval(sysTimer); sysTimer = setInterval(pollSystem, 1500);
  clockTimer = setInterval(tickClock, 1000);
  logLine((resumed ? 'Reconnected to job: ' : 'Job created: ') + job.id);
  poll();
}
resumeJob();

function logLine(text) {
  const log = $('#log'); $$('.typing', log).forEach((d) => { d.classList.remove('typing'); d.textContent = d.dataset.full; });
  const div = document.createElement('div'); div.dataset.full = text; div.className = 'typing'; log.appendChild(div);
  while (log.children.length > 9) log.firstChild.remove();
  let i = 0; clearInterval(typingTimer);
  typingTimer = setInterval(() => { i += 3; div.textContent = text.slice(0, i); if (i >= text.length) { clearInterval(typingTimer); div.classList.remove('typing'); } }, 16);
}

let shownPct = 0, pctRaf = null;
function animatePct(target) {
  cancelAnimationFrame(pctRaf);
  const step = () => { shownPct += (target - shownPct) * 0.12; if (Math.abs(target - shownPct) < 0.15) shownPct = target;
    $('#pct').textContent = Math.floor(shownPct); if (shownPct !== target) pctRaf = requestAnimationFrame(step); };
  pctRaf = requestAnimationFrame(step);
  const m = Math.floor(target / 25);
  if (m > state.milestone && m < 4) { state.milestone = m; const b = $('#big-pct'); b.classList.remove('milestone'); void b.offsetWidth; b.classList.add('milestone'); dustBurst(10); }
}

function dustBurst(count = 4) {
  const dust = $('#dust');
  for (let i = 0; i < count; i++) {
    const d = document.createElement('i');
    d.style.setProperty('--dx', `${-20 - Math.random() * 40}px`); d.style.setProperty('--dy', `${-6 - Math.random() * 18}px`);
    dust.appendChild(d); setTimeout(() => d.remove(), 750);
  }
}

function setStage(key) {
  const order = ['prep', 'load', 'gen', 'mux', 'done'], idx = order.indexOf(key);
  $$('#timeline li').forEach((li, i) => { li.classList.toggle('done', i < idx); li.classList.toggle('active', i === idx); });
}

function tickClock() {
  if (!state.startedAt) return;
  $('#elapsed').textContent = fmtTime(Date.now() / 1000 - state.startedAt);
  const s = state.samples;
  if (s.length > 3) { const a = s[0], b = s[s.length - 1]; const rate = (b.p - a.p) / (b.t - a.t);
    if (rate > 0.001) $('#remaining').textContent = '≈ ' + fmtTime((100 - b.p) / rate); }
  if (Math.random() < .5) dustBurst(1);
}

async function poll() {
  let job;
  try { job = await (await fetch(`/api/jobs/${state.jobId}`)).json(); } catch { pollTimer = setTimeout(poll, 2000); return; }
  const p = job.progress || {};
  if (p.started_at) state.startedAt = p.started_at;
  const pct = Math.min(100, p.percent || 0);
  animatePct(pct);
  $('#fill').style.width = `${pct}%`; $('#runner').style.left = `${pct}%`; $('#dust').style.left = `${pct}%`;
  const now = Date.now() / 1000; state.samples.push({ t: now, p: pct }); state.samples = state.samples.filter((x) => now - x.t < 90);

  const phase = p.phase || job.message;
  if (phase !== state.lastPhase) { state.lastPhase = phase; const h = $('#run-phase'); h.textContent = phase; h.classList.remove('swap'); void h.offsetWidth; h.classList.add('swap'); }
  if (job.message && job.message !== state.lastMsg) { state.lastMsg = job.message; logLine(job.message); }
  if (p.frames) { $('#kv-frames').textContent = `${p.frames} (${(p.frames / 24).toFixed(1)} s)`; $('#kv-canvas').textContent = `${p.width}×${p.height}`; }

  if (p.windows && $('#windows').children.length !== p.windows) {
    $('#windows').innerHTML = Array.from({ length: p.windows }, (_, i) => `<div class="win" style="animation-delay:${i * 60}ms"><i></i><span>${i + 1}</span></div>`).join('');
  }
  const genStart = 8, genSpan = 90;
  const within = p.windows ? Math.max(0, Math.min(1, ((pct - genStart) / genSpan) * p.windows - (p.window - 1))) : 0;
  [...$('#windows').children].forEach((w, i) => {
    const n = i + 1;
    w.classList.toggle('fin', n < p.window || pct >= 98);
    w.classList.toggle('now', n === p.window && pct < 98 && pct >= genStart);
    w.querySelector('i').style.height = n === p.window ? `${within * 100}%` : '';
  });
  if (p.window && p.window !== state.lastWindow) {
    if (state.lastWindow) { const r = $('#runner'); r.classList.remove('jump'); void r.offsetWidth; r.classList.add('jump'); setTimeout(() => dustBurst(8), 600); }
    state.lastWindow = p.window;
  }
  if (p.steps && $('#stepdots').children.length !== p.steps) $('#stepdots').innerHTML = '<b></b>'.repeat(p.steps);
  [...$('#stepdots').children].forEach((d, i) => { d.classList.toggle('on', i < p.step); d.classList.toggle('cur', i === p.step && pct < 98 && pct >= genStart); });

  if (job.status === 'completed') setStage('done');
  else if (pct >= 98) setStage('mux');
  else if (pct >= genStart && p.window > 0 && /Window|Denois|VAE|Encod/i.test(job.message)) setStage('gen');
  else if (pct >= genStart || /Loading|Model/i.test(job.message)) setStage('load');
  else setStage('prep');

  if (job.status === 'completed') return finish(job);
  if (job.status === 'failed') return fail(job.message);
  pollTimer = setTimeout(poll, 1000);
}

function stopRun() {
  clearTimeout(pollTimer); clearInterval(tipTimer); clearInterval(clockTimer);
  clearInterval(sysTimer); sysTimer = setInterval(pollSystem, 3000);
  document.body.classList.remove('rendering');
}

function finish(job) {
  stopRun(); animatePct(100); $('#fill').style.width = '100%'; $('#runner').style.left = '100%';
  const r = $('#runner'); r.classList.remove('jump'); void r.offsetWidth; r.classList.add('jump');
  setTimeout(() => {
    show('#done');
    const elapsed = state.startedAt ? Date.now() / 1000 - state.startedAt : 0;
    $('#done-time').textContent = state.finishedBefore ? '✓ render finished' : `✓ rendered in ${fmtTime(elapsed)}`;
    const url = `/api/jobs/${job.id}/output`;
    const res = $('#out-res'), src = $('#out-src'), start = Number(job.settings.start_seconds || 0);
    res.src = url; $('#download').href = url; $('#download').setAttribute('download', `halfjutsu-${job.id}.mp4`);
    // After a reload the source video is no longer in the browser: show the result on its own.
    $('#result-wipe').classList.toggle('solo', !state.file);
    if (state.file) src.src = URL.createObjectURL(state.file); else src.removeAttribute('src');
    src.dataset.start = start;
    res.addEventListener('loadedmetadata', () => { $('#result-wipe').classList.toggle('landscape', res.videoWidth > res.videoHeight); if (state.file) sweepWipe($('#result-wipe')); }, { once: true });
    src.addEventListener('loadedmetadata', () => { src.currentTime = start; }, { once: true });
    res.addEventListener('timeupdate', () => { if (!src.paused && Math.abs(src.currentTime - start - res.currentTime) > .15) src.currentTime = start + res.currentTime; });
    confetti();
  }, 1000);
}

function fail(message) { stopRun(); show('#fail'); $('#fail-msg').textContent = message; }

function confetti() {
  if (reduced) return;
  const c = $('#confetti'), ctx = c.getContext('2d');
  const W = c.width = c.offsetWidth, H = c.height = c.offsetHeight;
  const colors = ['#ff3d2e', '#ffb547', '#f1efe9', '#8b6cff', '#7cffb2'];
  const parts = Array.from({ length: 140 }, () => ({ x: W / 2, y: H * .3, vx: (Math.random() - .5) * 16, vy: -Math.random() * 14 - 4,
    s: 4 + Math.random() * 6, r: Math.random() * 6, vr: (Math.random() - .5) * .4, c: colors[Math.floor(Math.random() * colors.length)] }));
  let t = 0;
  const frame = () => {
    ctx.clearRect(0, 0, W, H); t++;
    for (const p of parts) { p.vy += .35; p.vx *= .985; p.x += p.vx; p.y += p.vy; p.r += p.vr;
      ctx.save(); ctx.translate(p.x, p.y); ctx.rotate(p.r); ctx.globalAlpha = Math.max(0, 1 - t / 150); ctx.fillStyle = p.c; ctx.fillRect(-p.s / 2, -p.s / 4, p.s, p.s / 2); ctx.restore(); }
    if (t < 150) requestAnimationFrame(frame); else ctx.clearRect(0, 0, W, H);
  };
  requestAnimationFrame(frame);
}

$('#sync').addEventListener('click', () => {
  const src = $('#out-src'), res = $('#out-res'); res.currentTime = 0; res.play();
  if (src.getAttribute('src')) { src.currentTime = Number(src.dataset.start || 0); src.play(); }
});
const backToForm = () => { saveJob(null); show(null); $('.hero').hidden = false; $('#job').hidden = false; setTimeout(placePills, 30); };
$('#again').addEventListener('click', backToForm);
$('#retry').addEventListener('click', backToForm);
