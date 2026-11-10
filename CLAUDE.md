# Halfjutsu – notes for Claude Code

Local web app (FastAPI + vanilla JS) that replaces a person in a video with Viggle-Animate,
run in-process through WanGP's Python API. Windows, NVIDIA 16 GB+.

## Layout
- `install.bat` – one-click installer (GPU/driver check, Git, uv, WanGP clone pinned to
  commit `bd1ff5903cbc18b46c3cce3b75bd140870b536a2` = WanGP v19.14, venv, licence prompt,
  `download_models.py`, desktop shortcut). Re-runnable; every step is idempotent.
- `start_halfjutsu.bat` – runs `uvicorn app.main:app` on 127.0.0.1:8787.
- `app/main.py` – HTTP API (`POST /api/jobs`, `/api/jobs/{id}/run`, `GET /api/jobs/{id}`,
  `/output`, `/api/system`). Jobs live in memory; files in `data/uploads/<id>/`.
- `app/wangp_engine.py` – `WanGPAnimateEngine._render_viggle()` is the only supported path.
- `app/swap_prepare.py` – `normalize_only()`: orientation, 24 fps, size (multiples of 32).
- `app/static/`, `app/templates/` – UI (English), motion design, progress polling.
- `download_models.py` – resumable, SHA-256-checked weight download via WanGP's locator;
  writes `data/viggle_ready.txt`.
- `scripts/validate_viggle.py` – CLI render of a prepared clip, for debugging outside the app.

## Pinned environment (tested)
Python 3.11.14 (uv) · torch 2.10.0+cu130 / torchvision 0.25.0 / torchaudio 2.10.0 ·
triton-windows 3.6.x · sageattention 2.2.0+cu130torch2.10.0andhigher.post6 ·
wgpkernels 1.4.2+cu130 · WanGP requirements.txt at the pinned commit.
WanGP is started with `--attention sage2 --profile 5 --max-ram 24` (see `best_attention()`).

## Model facts that matter
- Viggle-Animate: 3 steps, euler, flow shift 3, prompt ignored (fixed pre-encoded prompt),
  `video_prompt_type="IVU"`, one edited frame in `image_refs`, control video in `video_guide`.
- H3 frame grid is 17k+5, minimum 107; a single window is at most 124 frames.
  A single-window clip of 108–123 frames gets stretched to 124 and the output trails the
  source by ~17 frames → the engine pads such clips to 124 with the last frame.
- `measure_lag()` correlates motion (frame differences on a 4×4 grid) and only reports a
  lag that clearly beats 0; outputs are realigned with it.
- Lip sync: soundtrack → `audio_guide` + `audio_prompt_type="A"` (experimental in Viggle).
- **Licence:** Viggle-Animate = MiniMax H3 Community License, NOT granted in EU, UK, Republic of Korea, USA (model or outputs). Stated in the README licence section and the installer prompt.
- **Long clips:** chaining sliding windows compounds artefacts (visible from ~8 s). Clips
  > 124 frames are split into independent 124-frame chunks, all on the uploaded edited
  frame, overlapping by `CUT_MARGIN`=24; `join_hard_cuts()` cuts at the least different
  frame and eases colour for 24 frames. Chunks resume via `chunk_N.done`.
  See `docs/LONG_VIDEOS.md` for the rejected alternatives and measurements.
- Code paths for Wan Animate / H3 Ref2VA and `HistoryCleaner` are legacy (unused; the
  `HISTORY_HOOK` they expect is not in upstream WanGP). Do not re-enable without testing.

## Common install problems
- `torch.cuda.is_available()` False → driver older than CUDA 13 needs (580+), or a CPU
  torch wheel was installed; reinstall torch from the cu130 index.
- ffmpeg/ffprobe live in `vendor/WanGP/ffmpeg_bins/` and are downloaded by WanGP on first
  init (`download_models.py` triggers it).
- ffprobe csv output can carry extra fields (e.g. `278,`): parse numbers with a regex.
- Paths with spaces/accents can break some native tools: prefer `D:\halfjutsu`.

## Rules
- Never commit `vendor/`, `.venv-wangp/`, `data/` or any media/weights.
- Do not redistribute model weights; the Viggle/MiniMax H3 licence has regional exclusions.
