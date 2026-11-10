"""Local, in-process WanGP H3 adapter for PythonJutsu."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .models import RenderJob
from .swap_prepare import check_tracking_assets, probe, FFMPEG

ROOT = Path(__file__).resolve().parents[1]
WANGP_ROOT = ROOT / "vendor" / "WanGP"
MODEL_TYPE = "minimax_h3_ref2va_pruned"
DMAD = 'minimax_h3_DMAD_4step_full_lora_avg_rank_39_bf16.safetensors'


ANIMATE_MODEL = "animate"
ANIMATE_ACCELERATOR = "Wan21_I2V_14B_lightx2v_cfg_step_distill_lora_rank64.safetensors"
ANIMATE_PROMPT = (
    "The person from the reference image performs the exact movements of the person in the video: "
    "same gestures, hand positions, facial expression and timing. Keep the clothing, hair and accessories "
    "of the reference image. Photorealistic, natural skin texture, sharp detailed face, lighting consistent "
    "with the scene, stable identity, no extra objects."
)


def best_attention() -> str:
    """SageAttention 2 (~1.7x faster on the RTX 5080, same quality) when installed, else PyTorch SDPA."""
    try:
        import sageattention  # noqa: F401
        import triton  # noqa: F401
        return "sage2"
    except ImportError:
        return "sdpa"


def wan_frame_count(frames: int) -> int:
    """Wan generates 4n+1 frames; use the largest count that fits the clip."""
    return max(5, (frames - 1) // 4 * 4 + 1)


HARD_CUTS = True   # long clips: independent 124-frame chunks on the edited frame, joined with hard cuts
CUT_MARGIN = 24    # frames shared by neighbouring chunks, searched for the least visible cut
CUT_COLOR_FRAMES = 24  # after a cut, the new chunk's colour eases from the old one's over this many frames


def _read_rgb(video: Path, w: int, h: int, scale: tuple[int, int] | None = None):
    """Yields the frames of `video` as HxWx3 uint8 arrays (optionally downscaled)."""
    import numpy as np
    ow, oh = scale or (w, h)
    proc = subprocess.Popen([str(FFMPEG), "-v", "error", "-i", str(video), "-vf", f"scale={ow}:{oh}",
                             "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    size = ow * oh * 3
    try:
        while True:
            buf = proc.stdout.read(size)
            if len(buf) < size:
                break
            yield np.frombuffer(buf, np.uint8).reshape(oh, ow, 3)
    finally:
        proc.stdout.close()
        proc.wait()


def join_hard_cuts(pieces: list[Path], chunks: list[tuple[int, int]], output: Path, w: int, h: int) -> list[str]:
    """Joins independent chunks with hard cuts (no cross-fade).

    In each overlap both chunks have their own version of the same frames; the cut goes
    where those two versions look most alike (smallest visible jump). After the cut the
    new chunk's colour/contrast starts matched to the old chunk and eases back to its
    own over CUT_COLOR_FRAMES frames (a colour correction, not a blend: no ghosting).
    """
    import numpy as np
    thumbs = [np.stack(list(_read_rgb(p, w, h, (88, 160)))).astype(np.float32) for p in pieces]
    cuts, report = [], []
    for k in range(len(chunks) - 1):
        (a0, b0), (a1, _) = chunks[k], chunks[k + 1]
        span = b0 - a1                                  # shared frames, source indices a1 .. b0-1
        A, B = thumbs[k][a1 - a0: a1 - a0 + span], thumbs[k + 1][:span]
        cost = np.abs(A - B).mean(axis=(1, 2, 3))
        best = int(np.argmin(cost))
        cuts.append(a1 + best)
        report.append(f"cut@{a1 + best} jump {cost[best]:.1f} (first-frame cut would be {cost[0]:.1f}, worst {cost.max():.1f})")
    enc = subprocess.Popen([str(FFMPEG), "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}",
                            "-r", "24", "-i", "-", "-c:v", "libx264", "-crf", "12", "-pix_fmt", "yuv420p", str(output)],
                           stdin=subprocess.PIPE)
    try:
        last_frame = None
        for k, piece in enumerate(pieces):
            a = chunks[k][0]
            first = 0 if k == 0 else cuts[k - 1] - a
            stop = (chunks[k][1] - a) if k == len(pieces) - 1 else cuts[k] - a
            match = None
            for i, frame in enumerate(_read_rgb(piece, w, h)):
                if i < first:
                    if k and i == first - 1:
                        pass
                    continue
                if i >= stop:
                    break
                out = frame
                if k and last_frame is not None and i == first:
                    x = frame.astype(np.float32)
                    ma, sa = last_frame.mean((0, 1)), last_frame.std((0, 1)) + 1e-3
                    mb, sb = x.mean((0, 1)), x.std((0, 1)) + 1e-3
                    match = (ma, mb, np.clip(sa / sb, 0.9, 1.1))
                if match is not None and i - first < CUT_COLOR_FRAMES:
                    weight = 1 - (i - first) / CUT_COLOR_FRAMES
                    ma, mb, ratio = match
                    x = frame.astype(np.float32)
                    corrected = (x - mb) * ratio + ma
                    out = np.clip(weight * corrected + (1 - weight) * x, 0, 255).astype(np.uint8)
                enc.stdin.write(np.ascontiguousarray(out).tobytes())
                last_frame = out.astype(np.float32)
    finally:
        enc.stdin.close()
        enc.wait()
    return report
CLEAN_HISTORY = False  # clean the frames carried between sliding windows instead of cutting into segments


class HistoryCleaner:
    """Stops sliding-window drift without cutting the video.

    Each Viggle window continues from the last frames GENERATED by the previous one;
    every pass adds over-sharpening and blocky noise, and it compounds (a 64 s clip
    went from Laplacian 14 to 45). WanGP calls this (patched hook in wgp.py) on those
    carried-over frames before the next window uses them. Frequency split:
      * low frequencies (colour, shape, the character) are kept from the history;
      * where the history matches the source video (background, car, unchanged
        areas) the fine detail is taken from the SOURCE, which is clean;
      * elsewhere (the character) the history's own fine detail is scaled back to
        the first window's level.
    Continuity is kept: the video is never cut.
    """

    def __init__(self, control: Path, step: int, size: int, sigma: float = 2.5) -> None:
        self.control, self.step, self.size, self.sigma = control, step, size, sigma
        self.target = None
        self.log: list[str] = []

    @staticmethod
    def _gauss(x, sigma):
        import torch
        import torch.nn.functional as F
        radius = max(1, int(3 * sigma))
        t = torch.arange(-radius, radius + 1, dtype=x.dtype, device=x.device)
        g = torch.exp(-(t ** 2) / (2 * sigma ** 2))
        g = g / g.sum()
        c, f, h, w = x.shape
        y = x.permute(1, 0, 2, 3).reshape(f * c, 1, h, w)
        y = F.conv2d(F.pad(y, (radius, radius, 0, 0), mode="replicate"), g.view(1, 1, 1, -1))
        y = F.conv2d(F.pad(y, (0, 0, radius, radius), mode="replicate"), g.view(1, 1, -1, 1))
        return y.reshape(f, c, h, w).permute(1, 0, 2, 3)

    def _source(self, start, count, h, w):
        import numpy as np
        import torch
        raw = subprocess.run([str(FFMPEG), "-v", "error", "-i", str(self.control), "-vf",
                              rf"select=between(n\,{start}\,{start + count - 1}),scale={w}:{h}", "-fps_mode", "passthrough",
                              "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
        frames = np.frombuffer(raw, np.uint8).reshape(-1, h, w, 3)
        if len(frames) < count:
            return None
        return torch.from_numpy(frames.copy()).permute(3, 0, 1, 2).float().div_(127.5).sub_(1.0)

    @staticmethod
    def _lap(x):
        import torch
        import torch.nn.functional as F
        luma = (0.299 * x[0] + 0.587 * x[1] + 0.114 * x[2]).unsqueeze(1)
        k = torch.tensor([[0, -1, 0], [-1, 4, -1], [0, -1, 0]], dtype=x.dtype, device=x.device).view(1, 1, 3, 3)
        return F.conv2d(luma, k).abs().mean().item()

    def _scales(self, x):
        import torch.nn.functional as F
        c, f, h, w = x.shape
        quarter = F.avg_pool2d(x.permute(1, 0, 2, 3), 4).permute(1, 0, 2, 3)  # where the blocky mosaic lives
        return self._lap(x), self._lap(quarter)

    def __call__(self, frames, window_no):
        x = frames.float().cpu()
        fine, coarse = self._scales(x)
        std = x.std(dim=(1, 2, 3))
        if self.target is None:
            self.target = (fine, coarse, std)
            self.log.append(f"w{window_no}: target fine {fine:.4f} coarse {coarse:.4f}")
            return frames
        t_fine, t_coarse, t_std = self.target
        before = (fine, coarse)
        for _ in range(6):
            over_f, over_c = fine / t_fine, coarse / t_coarse
            if max(over_f, over_c) <= 1.02:
                break
            sigma = 3.0 if over_c >= over_f else 1.2
            soft = self._gauss(x, sigma)
            sf, sc = self._scales(soft)
            cur, tgt, sft = (coarse, t_coarse, sc) if over_c >= over_f else (fine, t_fine, sf)
            alpha = max(0.0, min(1.0, (tgt - sft) / max(cur - sft, 1e-6)))
            x = alpha * x + (1 - alpha) * soft
            fine, coarse = self._scales(x)
        mean = x.mean(dim=(1, 2, 3), keepdim=True)
        ratio = (t_std / x.std(dim=(1, 2, 3)).clamp_min(1e-4)).clamp(0.85, 1.1).view(-1, 1, 1, 1)
        x = ((x - mean) * ratio + mean).clamp(-1, 1)
        self.log.append(f"w{window_no}: fine {before[0]:.4f}->{fine:.4f} coarse {before[1]:.4f}->{coarse:.4f} "
                        f"contrast x{ratio.mean().item():.3f}")
        return x.to(device=frames.device, dtype=frames.dtype)


SEGMENT_WINDOWS = 3   # max chained sliding windows before restarting from the edited frame (~14 s)
SEGMENT_FADE = 12     # frames cross-faded between independent segments (0.5 s)
VIGGLE_WINDOW = 124  # H3 frame grid is 17k+5; a single window between 107 and 124 frames gets stretched


def _gray_frames(video: Path, limit: int | None = None):
    import numpy as np
    args = [str(FFMPEG), "-v", "error", "-i", str(video), "-vf", "scale=44:80,format=gray"]
    if limit:
        args += ["-frames:v", str(limit)]
    raw = subprocess.run(args + ["-f", "rawvideo", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, 80, 44).astype("float32")


def measure_lag(source: Path, generated: Path, max_lag: int = 30) -> int:
    """Frames by which `generated` trails `source` (0 = aligned).

    Compares MOTION, not pixels: frame-to-frame change on a 4x4 grid of small greyscale
    thumbnails, correlated over candidate lags. The replaced person looks different, but
    moves when the source moves. A lag is only reported when it clearly beats 0.
    """
    import numpy as np

    def motion(video):
        g = _gray_frames(video).astype(np.float32)
        m = np.abs(np.diff(g, axis=0))
        f, h, w = m.shape
        m = m[:, : h // 4 * 4, : w // 4 * 4].reshape(f, 4, h // 4, 4, w // 4).mean((2, 4)).reshape(f, -1)
        return m

    a, b = motion(source), motion(generated)
    n = min(len(a), len(b))
    scores = {}
    for lag in range(0, min(max_lag, n // 3) + 1):
        x, y = a[: n - lag], b[lag:n]
        x = (x - x.mean(0)) / (x.std(0) + 1e-6)
        y = (y - y.mean(0)) / (y.std(0) + 1e-6)
        scores[lag] = float((x * y).mean())
    best = max(scores, key=scores.get)
    return best if best and scores[best] > scores[0] + 0.1 else 0


def display_is_portrait(video: Path) -> bool:
    probe = WANGP_ROOT / "ffmpeg_bins" / "ffprobe.exe"
    result = subprocess.run(
        [str(probe), "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height:stream_side_data=rotation",
         "-of", "json", str(video)],
        capture_output=True, text=True, check=True,
    )
    stream = json.loads(result.stdout)["streams"][0]
    width, height = int(stream["width"]), int(stream["height"])
    rotation = next((int(item.get("rotation", 0)) for item in stream.get("side_data_list", [])
                     if "rotation" in item), 0)
    if rotation % 180:
        width, height = height, width
    return height > width


class WanGPAnimateEngine:
    """Wan 2.2 Animate in person-replacement mode (pose + SAM mask + relighting).

    SAM 3.1 tracks the person in a separate process; WanGP then extracts the
    DWPose skeleton inside the mask, keeps the real background outside it and
    renders the reference character with the relighting LoRA. The optional
    lightx2v step-distill LoRA allows ~6 steps instead of ~20.
    """

    def __init__(self) -> None:
        self._session = None

    def available(self) -> bool:
        try:
            import torch
            return WANGP_ROOT.is_dir() and torch.cuda.is_available()
        except ImportError:
            return False

    def _session_or_init(self):
        if self._session is None:
            if str(WANGP_ROOT) not in sys.path:
                sys.path.insert(0, str(WANGP_ROOT))
            os.environ["HF_HUB_OFFLINE"] = "1"
            os.environ["TRANSFORMERS_OFFLINE"] = "1"
            # WanGP resolves some checkpoints relative to its own folder.
            os.chdir(WANGP_ROOT)
            from shared.api import init

            self._session = init(
                root=WANGP_ROOT,
                output_dir=ROOT / "data" / "outputs",
                cli_args=["--attention", best_attention(), "--profile", "5", "--max-ram", "24"],
                console_output=False,
            )
        return self._session

    async def render(self, job: RenderJob) -> Path:
        return await asyncio.to_thread(self._render_sync, job)

    def _render_sync(self, job: RenderJob) -> Path:
        if job.edited_frame_path is not None:
            return self._render_viggle(job)
        job.message = "Checking local Animate weights…"
        if not (ROOT / "data" / "animate_ready.txt").is_file():
            raise RuntimeError("Animate weights are not installed (legacy path, unsupported).")
        check_tracking_assets()
        fast = job.settings.fast
        accelerator = WANGP_ROOT / "loras" / "wan_i2v" / ANIMATE_ACCELERATOR
        if fast and not accelerator.is_file():
            raise RuntimeError("The lightx2v accelerator LoRA is missing in vendor/WanGP/loras/wan_i2v.")
        short_side = {"480p": 480, "720p": 720}.get(job.settings.resolution)
        if short_side is None:
            raise RuntimeError("Unsupported resolution: use 480p or 720p.")
        if self._session is not None:
            self._session.close()  # Free system RAM for SAM; Animate reloads afterwards.
            self._session = None

        prepared = job.video_path.parent / "prepared_swap"
        job.message = "Tracking the person and preparing the mask…"
        process = subprocess.Popen(
            [sys.executable, "-u", "-m", "app.swap_prepare", "--video", str(job.video_path.resolve()),
             "--output", str(prepared), "--short-side", str(short_side)],
            cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            encoding="utf-8", errors="replace", env={**os.environ, "PYTHONUTF8": "1"})
        with (job.video_path.parent / "tracking.log").open("w", encoding="utf-8") as log:
            for line in process.stdout:
                log.write(line)
                log.flush()
                if "Tracking:" in line or "SAM 3.1:" in line:
                    job.message = line.strip()
        if process.wait() != 0:
            raise RuntimeError("Tracking failed. See tracking.log.")
        manifest = json.loads((prepared / "manifest.json").read_text(encoding="utf-8"))
        if manifest["empty_mask_frames"]:
            raise RuntimeError("Tracking lost the person in some frames; check the mask preview.")

        session = self._session_or_init()
        if not session.get_model_availability(ANIMATE_MODEL)["available"]:
            raise RuntimeError("WanGP cannot find all Animate weights (legacy path, unsupported).")

        class Progress:
            def on_progress(self, update):
                stage = str(getattr(update, "status", "") or getattr(update, "phase", ""))
                percent = int(getattr(update, "progress", 0) or 0)
                current, total = getattr(update, "current_step", None), getattr(update, "total_steps", None)
                suffix = f" · step {current}/{total}" if current is not None and total else ""
                job.message = f"{stage} · {percent}%{suffix}"

            def on_status(self, message):
                if message:
                    job.message = str(message)

        frames = wan_frame_count(manifest["frames"])
        description = job.prompt.strip()
        prompt = f"{description}. {ANIMATE_PROMPT}" if description else ANIMATE_PROMPT
        settings = session.get_default_settings(ANIMATE_MODEL)
        settings.update({
            "prompt": prompt,
            "image_refs": [str(job.reference_path.resolve())],
            "video_guide": str(prepared / "source.mp4"),
            "video_mask": str(prepared / "edit_mask.mp4"),
            # Replace the person in the control video with the reference person, plus relighting LoRA.
            "video_prompt_type": "PVBAIH#1",
            "resolution": f"{manifest['width']}x{manifest['height']}",
            "video_length": frames,
            "sliding_window_size": 81,
            # Each window starts from the previous window's *generated* frames, so
            # errors compound. One overlap frame (as in the original Wan Animate)
            # cut oversharpening ~40% and removed blocky backgrounds vs 9 frames;
            # pose and background still come from the real video every window.
            # (Colour correction is ignored by Animate: it has no colour reference.)
            "sliding_window_overlap": 1,
            "num_inference_steps": job.settings.steps,
            "guidance_scale": 1.0,
            "seed": job.settings.seed,
            "prompt_enhancer": "",
        })
        if fast:
            settings.update({"activated_loras": [ANIMATE_ACCELERATOR], "loras_multipliers": "1"})
        job.message = "Loading Wan Animate into RAM and GPU…"
        result = session.submit_task(settings, callbacks=Progress()).result()
        if not result.success or not result.generated_files:
            errors = "; ".join(str(e.message) for e in result.errors)
            raise RuntimeError(errors or "Animate no produjo un archivo de salida.")
        # With sliding windows WanGP saves a growing file after each window; the
        # complete video is the longest one, not the first in the list.
        candidates = [Path(name) for name in result.generated_files if Path(name).is_file()]
        if not candidates:
            raise RuntimeError("The generated file does not exist.")
        source = max(candidates, key=lambda path: float(probe(path)["format"]["duration"]))
        if not source.is_file() or source.stat().st_size < 1024:
            raise RuntimeError("The generated file is missing or empty.")

        output = job.video_path.parent / "result.mp4"
        if job.settings.preserve_audio and manifest["has_audio"]:
            job.message = "Restoring the original audio…"
            subprocess.run([str(FFMPEG), "-y", "-v", "error", "-i", str(source), "-i", str(job.video_path),
                            "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                            "-shortest", str(output)], check=True)
        else:
            shutil.copy2(source, output)
        duration = float(probe(output)["format"]["duration"])
        if abs(duration - frames / 24) > 0.8:
            raise RuntimeError(f"Salida incompleta: {duration:.2f}s frente a {frames / 24:.2f}s.")
        return output

    # ------------------------------------------------------------------ Viggle
    def _render_viggle(self, job: RenderJob) -> Path:
        """Viggle-Animate: control video + ONE edited frame of it (appearance), 3 steps.

        No mask or SAM tracking: the edited frame already contains the character in
        the video's first-frame pose, and Viggle propagates it with the video's motion.
        Progress is published in job.progress (phase, overall %, window and step).
        """
        import math
        import re
        import time
        from PIL import Image
        from .swap_prepare import normalize_only

        s, prog = job.settings, job.progress
        prog.started_at = time.time()

        def phase(name: str, percent: float) -> None:
            prog.phase, prog.percent = name, round(percent, 1)
            job.message = name

        phase("Checking local Viggle weights", 1)
        if not (ROOT / "data" / "viggle_ready.txt").is_file():
            raise RuntimeError("Viggle weights are missing. Run install.bat to finish the download.")
        short_side = {"480p": 480, "720p": 720}.get(s.resolution)
        if short_side is None:
            raise RuntimeError("Unsupported resolution: use 480p or 720p.")

        folder = job.video_path.parent / "viggle"
        phase("Normalising the video (orientation, 24 fps, size)", 3)
        manifest = normalize_only(job.video_path, folder, short_side, s.start_seconds, s.duration_seconds)
        width, height, frames = manifest["width"], manifest["height"], manifest["frames"]
        if frames < 49:
            raise RuntimeError("The selected range is too short: use at least 2 seconds.")
        prog.frames, prog.width, prog.height = frames, width, height
        control = folder / "source.mp4"
        render_frames = frames
        if 107 < frames < VIGGLE_WINDOW:   # pad with the last frame so WanGP does not stretch the clip (it lags ~17 frames)
            control = folder / "source_padded.mp4"
            subprocess.run([str(FFMPEG), "-y", "-v", "error", "-i", str(folder / "source.mp4"), "-vf",
                            f"tpad=stop_mode=clone:stop={VIGGLE_WINDOW - frames}", "-an", "-c:v", "libx264", "-crf", "16",
                            "-pix_fmt", "yuv420p", str(control)], check=True)
            render_frames = VIGGLE_WINDOW
        soundtrack = None
        if s.lip_sync and manifest["has_audio"]:
            phase("Extracting the soundtrack to drive the mouth (lip sync)", 5)
            soundtrack = folder / "soundtrack.wav"
            subprocess.run([str(FFMPEG), "-y", "-v", "error"] + (["-ss", f"{s.start_seconds:.3f}"] if s.start_seconds else [])
                           + ["-i", str(job.video_path), "-t", f"{frames / manifest['fps']:.3f}", "-vn", "-ac", "2", "-ar", "44100",
                              "-af", f"apad=whole_dur={render_frames / manifest['fps']:.3f}", str(soundtrack)], check=True)

        phase("Fitting the edited frame to the canvas", 6)
        edited = Image.open(job.edited_frame_path).convert("RGB")
        w, h = edited.size
        target = width / height
        if w / h > target:
            cw = round(h * target)
            edited = edited.crop(((w - cw) // 2, 0, (w - cw) // 2 + cw, h))
        else:
            ch = round(w / target)
            edited = edited.crop((0, (h - ch) // 2, w, (h - ch) // 2 + ch))
        fitted = folder / "edited_frame.png"
        edited.resize((width, height), Image.LANCZOS).save(fitted)

        phase("Loading Viggle into RAM and GPU", 8)
        session = self._session_or_init()
        if not session.get_model_availability("viggle_animate")["available"]:
            raise RuntimeError("WanGP cannot find all Viggle weights. Run install.bat again.")

        settings = session.get_default_settings("viggle_animate")  # 3 steps, euler, shift 3, windows 124/18
        settings.update({
            "image_refs": [str(fitted)],
            "video_guide": str(control),
            "resolution": f"{width}x{height}",
            "video_length": render_frames,
            "seed": s.seed,
            "num_inference_steps": s.steps,
        })
        if soundtrack is not None:   # experimental in Viggle, but makes singing/speaking mouths actually open
            settings["audio_guide"] = str(soundtrack)
            settings["audio_prompt_type"] = "A"
        if s.flow_shift is not None:
            settings["flow_shift"] = s.flow_shift
        if s.sample_solver:
            settings["sample_solver"] = s.sample_solver
        if s.window_frames is not None:
            settings["sliding_window_size"] = s.window_frames
        if s.window_overlap is not None:
            settings["sliding_window_overlap"] = s.window_overlap
        size = settings.get("sliding_window_size") or 124
        overlap = min(settings.get("sliding_window_overlap") or 18, size - 8)
        fps = manifest["fps"]

        def windows_for(n: int) -> int:
            return max(1, math.ceil((n - overlap) / (size - overlap)))

        # Each sliding window continues from the previous window's GENERATED frames, so
        # artefacts compound: past ~4 windows (~18 s) the image over-sharpens and breaks
        # up. Long clips are therefore cut into independent segments of at most
        # SEGMENT_WINDOWS windows, each restarted from the edited frame, and cross-faded.
        seg_max = size + (SEGMENT_WINDOWS - 1) * (size - overlap)
        chunks = []
        if HARD_CUTS and s.window_frames is None and frames > VIGGLE_WINDOW:
            # Hard cuts: independent single-window chunks (no sliding window, nothing carried
            # over, so nothing degrades), ALL referenced on the uploaded edited frame.
            # Neighbours overlap by CUT_MARGIN frames; the join picks the best frame there.
            a = 0
            while a + VIGGLE_WINDOW < frames:
                chunks.append((a, a + VIGGLE_WINDOW))
                a += VIGGLE_WINDOW - CUT_MARGIN
            chunks.append((max(frames - VIGGLE_WINDOW, chunks[-1][0] + 1), frames))
        if chunks:
            segments = chunks
        elif not CLEAN_HISTORY and s.window_frames is None and frames > seg_max:
            count = math.ceil((frames - SEGMENT_FADE) / (seg_max - SEGMENT_FADE))
            seg_len = math.ceil((frames + (count - 1) * SEGMENT_FADE) / count)
            starts = [i * (seg_len - SEGMENT_FADE) for i in range(count)]
            segments = [(a, min(a + seg_len, frames)) for a in starts]
        else:
            segments = [(0, frames)]
        prog.windows = sum(windows_for(b - a) for a, b in segments)
        prog.steps, prog.window = s.steps, 1
        window_re = re.compile(r"Sliding Window (\d+)/(\d+)")
        span = 90.0  # 8% .. 98%: generation; the rest is preparation and final mux
        done_windows = 0

        class Progress:
            def on_progress(self, update):
                stage = str(getattr(update, "status", "") or getattr(update, "phase", ""))
                within = float(getattr(update, "progress", 0) or 0)  # WanGP: % of the current window
                current, total = getattr(update, "current_step", None), getattr(update, "total_steps", None)
                found = window_re.search(stage)
                if found:
                    prog.window = done_windows + int(found.group(1))
                if current is not None and total and "Denois" in stage:
                    prog.step, prog.steps = int(current), int(total)
                done = (prog.window - 1 + min(within, 100) / 100) / max(prog.windows, 1)
                prog.percent = round(max(prog.percent, 8 + span * done), 1)
                prog.phase = stage.split(" | ")[0] if stage else prog.phase
                suffix = f" · step {current}/{total}" if current is not None and total else ""
                part = f"Segment {index + 1}/{len(segments)} · " if len(segments) > 1 else ""
                job.message = f"{part}{stage} · {int(within)}%{suffix}"

            def on_status(self, message):
                if message:
                    job.message = str(message)

        def generate(seg_control: Path, seg_frames: int, seg_audio: Path | None, ref: Path | None = None) -> Path:
            task = dict(settings, video_guide=str(seg_control), video_length=seg_frames)
            if ref is not None:
                task["image_refs"] = [str(ref)]
            if seg_audio is not None:
                task["audio_guide"] = str(seg_audio)
            wgp = sys.modules.get("wgp")
            cleaner = HistoryCleaner(seg_control, size - overlap, size) if CLEAN_HISTORY and wgp is not None and hasattr(wgp, "HISTORY_HOOK") else None
            if CLEAN_HISTORY and cleaner is None:
                logging.warning("WanGP history hook not found (wgp.py patch missing): long clips may drift")
            if cleaner is not None:
                wgp.HISTORY_HOOK = cleaner
            try:
                result = session.submit_task(task, callbacks=Progress()).result()
            finally:
                if cleaner is not None:
                    wgp.HISTORY_HOOK = None
                    logging.warning("Job %s history cleaning: %s", job.id, " | ".join(cleaner.log))
            if not result.success or not result.generated_files:
                errors = "; ".join(str(e.message) for e in result.errors)
                raise RuntimeError(errors or "Viggle produced no output file.")
            candidates = [Path(name) for name in result.generated_files if Path(name).is_file()]
            if not candidates:
                raise RuntimeError("The generated file does not exist.")
            return max(candidates, key=lambda path: float(probe(path)["format"]["duration"]))

        def cut(a: int, b: int, name: str) -> tuple[Path, Path | None]:
            seg_control = folder / f"{name}.mp4"
            subprocess.run([str(FFMPEG), "-y", "-v", "error", "-i", str(folder / "source.mp4"), "-vf",
                            f"trim=start_frame={a}:end_frame={b},setpts=PTS-STARTPTS", "-an", "-c:v", "libx264",
                            "-crf", "12", "-pix_fmt", "yuv420p", str(seg_control)], check=True)
            seg_audio = None
            if soundtrack is not None:
                seg_audio = folder / f"{name}.wav"
                n = b - a
                subprocess.run([str(FFMPEG), "-y", "-v", "error", "-ss", f"{a / fps:.4f}", "-i", str(soundtrack),
                                "-t", f"{n / fps:.4f}", "-af", f"apad=whole_dur={n / fps:.4f}", str(seg_audio)], check=True)
            return seg_control, seg_audio

        if chunks:
            logging.warning("Job %s: %d frames in %d hard-cut chunks %s", job.id, frames, len(chunks), chunks)
            pieces = []
            for index, (a, b) in enumerate(chunks):
                n = b - a
                seg_control, seg_audio = cut(a, b, f"chunk_{index}")
                prog.window = index + 1   # single-window chunks never report "Sliding Window n/m"
                piece, done_mark = folder / f"chunk_{index}_out.mp4", folder / f"chunk_{index}.done"
                if done_mark.is_file() and piece.is_file():   # resume: this chunk was already generated
                    logging.warning("Job %s chunk %d already done; reusing it", job.id, index)
                else:
                    raw = generate(seg_control, n, seg_audio, fitted)
                    seg_lag = measure_lag(seg_control, raw)
                    if seg_lag:
                        logging.warning("Job %s chunk %d trailed by %d frames; realigning", job.id, index, seg_lag)
                    # shifting drops frames at the end: clone the last one so the piece keeps n frames
                    subprocess.run([str(FFMPEG), "-y", "-v", "error"] + (["-ss", f"{seg_lag / fps:.4f}"] if seg_lag else [])
                                   + ["-i", str(raw), "-vf", f"tpad=stop_mode=clone:stop={max(seg_lag, 1)}",
                                      "-frames:v", str(n), "-an", "-c:v", "libx264", "-crf", "12",
                                      "-pix_fmt", "yuv420p", str(piece)], check=True)
                    done_mark.write_text(str(raw), encoding="utf-8")
                pieces.append(piece)
                done_windows += 1
            phase("Choosing the cut points and joining the chunks", 97)
            source = folder / "chunks_joined.mp4"
            report = join_hard_cuts(pieces, chunks, source, width, height)
            logging.warning("Job %s cuts: %s", job.id, report)
            lag = 0
        elif len(segments) == 1:
            index = 0
            source = generate(control, render_frames, soundtrack)
            lag = measure_lag(folder / "source.mp4", source)
        else:
            logging.info("Job %s: %d frames split into %d independent segments %s", job.id, frames, len(segments), segments)
            pieces = []
            for index, (a, b) in enumerate(segments):
                n = b - a
                seg_control = folder / f"segment_{index}.mp4"
                subprocess.run([str(FFMPEG), "-y", "-v", "error", "-i", str(folder / "source.mp4"), "-vf",
                                f"trim=start_frame={a}:end_frame={b},setpts=PTS-STARTPTS", "-an", "-c:v", "libx264",
                                "-crf", "12", "-pix_fmt", "yuv420p", str(seg_control)], check=True)
                seg_audio = None
                if soundtrack is not None:
                    seg_audio = folder / f"segment_{index}.wav"
                    subprocess.run([str(FFMPEG), "-y", "-v", "error", "-ss", f"{a / fps:.4f}", "-i", str(soundtrack),
                                    "-t", f"{n / fps:.4f}", "-af", f"apad=whole_dur={n / fps:.4f}", str(seg_audio)], check=True)
                raw = generate(seg_control, n, seg_audio)
                seg_lag = measure_lag(seg_control, raw)
                if seg_lag:
                    logging.warning("Job %s segment %d trailed by %d frames; realigning", job.id, index, seg_lag)
                piece = folder / f"segment_{index}_out.mp4"
                subprocess.run([str(FFMPEG), "-y", "-v", "error"] + (["-ss", f"{seg_lag / fps:.4f}"] if seg_lag else [])
                               + ["-i", str(raw), "-frames:v", str(n), "-an", "-c:v", "libx264", "-crf", "12",
                                  "-pix_fmt", "yuv420p", str(piece)], check=True)
                pieces.append((piece, n))
                done_windows += windows_for(n)
            phase("Cross-fading the segments", 97)
            source = folder / "segments_joined.mp4"
            inputs, graph, last, offset = [], [], "0:v", 0
            for i, (piece, n) in enumerate(pieces):
                inputs += ["-i", str(piece)]
                if i == 0:
                    offset = n - SEGMENT_FADE
                    continue
                label = f"v{i}"
                graph.append(f"[{last}][{i}:v]xfade=transition=fade:duration={SEGMENT_FADE / fps:.4f}"
                             f":offset={offset / fps:.4f}[{label}]")
                last, offset = label, offset + n - SEGMENT_FADE
            subprocess.run([str(FFMPEG), "-y", "-v", "error"] + inputs + ["-filter_complex", ";".join(graph), "-map",
                            f"[{last}]", "-c:v", "libx264", "-crf", "12", "-pix_fmt", "yuv420p", str(source)], check=True)
            lag = 0

        # WanGP may round the length up to its frame grid: trim to the real clip length.
        duration = frames / fps
        output = job.video_path.parent / "result.mp4"
        phase("Trimming to length and restoring the original audio", 98)
        if lag:   # safety net: realign if WanGP shifted the motion
            job.message = f"Realigning: output trailed the source by {lag} frames"
            logging.warning("Viggle output for %s trailed the source by %d frames; realigning", job.id, lag)
        command = [str(FFMPEG), "-y", "-v", "error"] + (["-ss", f"{lag / manifest['fps']:.4f}"] if lag else []) + ["-i", str(source)]
        if s.preserve_audio and manifest["has_audio"]:
            command += (["-ss", f"{s.start_seconds:.3f}"] if s.start_seconds else []) + ["-i", str(job.video_path)]
            command += ["-map", "0:v:0", "-map", "1:a:0", "-c:a", "aac", "-b:a", "192k"]
        else:
            command += ["-an"]
        command += ["-t", f"{duration:.3f}", "-c:v", "libx264", "-crf", "14", "-pix_fmt", "yuv420p", str(output)]
        subprocess.run(command, check=True)
        got = float(probe(output)["format"]["duration"])
        if abs(got - duration) > 0.8 + lag / manifest["fps"]:
            raise RuntimeError(f"Incomplete output: {got:.2f}s instead of {duration:.2f}s.")
        phase("Render complete", 100)
        return output
