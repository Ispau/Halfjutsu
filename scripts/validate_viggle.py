"""Short supervised test of Viggle-Animate (H3-derived character replacement).

Inputs: a prepared control video (from app.swap_prepare) and ONE edited frame of
that video in which the person has already been replaced by the character
(made with any image editor; same pose, framing, background and size).
The prompt is ignored by the model: the edited frame carries the appearance.
"""
import argparse
import faulthandler
import json
import os
import shutil
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WANGP_ROOT = ROOT / "vendor" / "WanGP"

parser = argparse.ArgumentParser()
parser.add_argument("--prepared", type=Path, required=True, help="folder made by app.swap_prepare")
parser.add_argument("--edited-frame", type=Path, required=True, help="edited frame of the control video")
parser.add_argument("--output", default="viggle_result.mp4")
parser.add_argument("--steps", type=int, default=None, help="default: WanGP's 3")
parser.add_argument("--attention", default="sage2")
parser.add_argument("--audio", type=Path, default=None, help="soundtrack synced to the control video: conditions the mouth (WanGP audio_prompt_type A)")
parser.add_argument("--seed", type=int, default=0)
ARGS = parser.parse_args()
PREPARED = ARGS.prepared.resolve()
EDITED = ARGS.edited_frame.resolve()
AUDIO = ARGS.audio.resolve() if ARGS.audio else None
FOLDER = PREPARED.parent
OUTPUT = FOLDER / ARGS.output
STARTED = time.monotonic()
faulthandler.enable()
faulthandler.dump_traceback_later(900, repeat=True)


def report(message: str) -> None:
    line = f"{time.monotonic() - STARTED:.0f}s | {message}"
    print(line, file=sys.__stdout__, flush=True)
    (FOLDER / "viggle_status.txt").write_text(line, encoding="utf-8")


def main() -> int:
    if not (ROOT / "data" / "viggle_ready.txt").is_file():
        raise RuntimeError("Viggle weights are missing: run install.bat.")
    if not EDITED.is_file():
        raise RuntimeError(f"Edited frame not found: {EDITED}")
    manifest = json.loads((PREPARED / "manifest.json").read_text(encoding="utf-8"))
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.chdir(WANGP_ROOT)
    sys.path.insert(0, str(WANGP_ROOT))
    from shared.api import init

    session = init(root=WANGP_ROOT, output_dir=ROOT / "data" / "outputs",
                   cli_args=["--attention", ARGS.attention, "--profile", "5", "--max-ram", "24"],
                   console_output=False)
    if not session.get_model_availability("viggle_animate")["available"]:
        raise RuntimeError("WanGP cannot find all Viggle weights.")

    state = {"message": "starting"}

    class Progress:
        def on_progress(self, update):
            stage = str(getattr(update, "status", "") or getattr(update, "phase", ""))
            percent = int(getattr(update, "progress", 0) or 0)
            current, total = getattr(update, "current_step", None), getattr(update, "total_steps", None)
            suffix = f" · step {current}/{total}" if current is not None and total else ""
            state["message"] = f"{stage} · {percent}%{suffix}"

        def on_status(self, message):
            if message:
                state["message"] = str(message)

    done = threading.Event()
    threading.Thread(target=lambda: [report(state["message"]) for _ in iter(lambda: done.wait(10), True)],
                     daemon=True).start()

    settings = session.get_default_settings("viggle_animate")
    settings.update({
        "image_refs": [str(EDITED)],
        "video_guide": str(PREPARED / "source.mp4"),
        "resolution": f"{manifest['width']}x{manifest['height']}",
        "video_length": manifest["frames"],
        "seed": ARGS.seed,
    })
    if ARGS.audio:
        settings["audio_guide"] = str(AUDIO)
        settings["audio_prompt_type"] = "A"
    if ARGS.steps:
        settings["num_inference_steps"] = ARGS.steps
    report(f"settings: steps={settings['num_inference_steps']} solver={settings.get('sample_solver')} "
           f"shift={settings.get('flow_shift')} window={settings.get('sliding_window_size')} "
           f"overlap={settings.get('sliding_window_overlap')} mode={settings.get('video_prompt_type')} audio={settings.get('audio_prompt_type')!r}")
    try:
        result = session.submit_task(settings, callbacks=Progress()).result()
    finally:
        done.set()
    if not result.success or not result.generated_files:
        raise RuntimeError("; ".join(str(e.message) for e in result.errors) or "Viggle no produjo salida.")
    sys.path.insert(0, str(ROOT))
    from app.swap_prepare import probe
    final = max((Path(f) for f in result.generated_files if Path(f).is_file()),
                key=lambda p: float(probe(p)["format"]["duration"]))
    shutil.copy2(final, OUTPUT)
    report(f"COMPLETED {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
