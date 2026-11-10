"""Download and verify the Viggle-Animate weights that Halfjutsu needs (~26 GB).

WanGP decides the exact file variant (pruned INT8 for 16 GB cards) and where each
file goes, so WanGP finds them later without downloading again. Large files are
fetched with resumable curl transfers (a cut connection resumes where it stopped)
and checked against the SHA-256 that Hugging Face publishes for LFS files.
Everything else WanGP needs (VAE, small companion files, ffmpeg) is fetched by
WanGP's own downloader.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WANGP_ROOT = ROOT / "vendor" / "WanGP"
MODEL_TYPE = "viggle_animate"


def remote_meta(url: str) -> tuple[int, str | None]:
    """Size and LFS SHA-256 of a Hugging Face file, from a HEAD request."""
    result = subprocess.run(["curl.exe", "-sIL", url], capture_output=True, text=True, check=True)
    size, sha = None, None
    for line in result.stdout.splitlines():
        key, _, value = line.partition(":")
        key, value = key.strip().lower(), value.strip().strip('"')
        if key == "x-linked-size":
            size = int(value)
        elif key == "x-linked-etag":
            sha = value
    if size is None:
        for line in result.stdout.splitlines():
            key, _, value = line.partition(":")
            if key.strip().lower() == "content-length":
                size = int(value.strip())
    if size is None:
        raise RuntimeError(f"Could not read the size of {url}")
    return size, sha


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(16 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch(url: str, destination: Path) -> None:
    size, sha = remote_meta(url)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file() and destination.stat().st_size == size:
        print(f"Already present: {destination.name}", flush=True)
    else:
        existing = destination.stat().st_size if destination.exists() else 0
        if existing > size:
            raise RuntimeError(f"Local file is larger than the published one: {destination}")
        if shutil.disk_usage(destination.parent).free < size - existing + 10 * 1024**3:
            raise RuntimeError("Not enough disk space (10 GB of headroom is kept).")
        command = ["curl.exe", "--location", "--fail", "--silent", "--show-error",
                   "--continue-at", "-", "--output", str(destination), url]
        for attempt in range(1, 21):
            previous = destination.stat().st_size if destination.exists() else 0
            print(f"Downloading {destination.name}: {previous / 1e9:.2f}/{size / 1e9:.2f} GB (attempt {attempt})",
                  flush=True)
            proc = subprocess.Popen(command)
            while proc.poll() is None:
                time.sleep(30)
                current = destination.stat().st_size if destination.exists() else 0
                print(f"  {destination.name}: {current / 1e9:.2f}/{size / 1e9:.2f} GB", flush=True)
            current = destination.stat().st_size if destination.exists() else 0
            if current == size:
                break
            if current == previous and attempt >= 3:
                raise RuntimeError(f"The download of {destination.name} is not progressing.")
            time.sleep(10)
        else:
            raise RuntimeError(f"Incomplete download: {destination.name}")
    if sha:
        print(f"Verifying {destination.name} (SHA-256)...", flush=True)
        if sha256(destination) != sha:
            destination.unlink()
            raise RuntimeError(f"Checksum mismatch, file deleted: {destination.name}. Run the installer again.")
        print(f"  OK {destination.name}", flush=True)


def main() -> int:
    os.chdir(WANGP_ROOT)
    sys.path.insert(0, str(WANGP_ROOT))
    from shared.api import init

    session = init(root=WANGP_ROOT, cli_args=["--attention", "sdpa", "--profile", "5", "--max-ram", "24"],
                   console_output=False)
    import wgp
    from shared.utils import files_locator as fl

    main_url = wgp.get_model_filename(MODEL_TYPE, wgp.transformer_quantization, wgp.transformer_dtype_policy)
    lora_dir = wgp.get_lora_dir(MODEL_TYPE)
    large = [(main_url, Path(fl.get_local_model_filename(main_url) or fl.get_smart_download_location(main_url)))]
    for url in wgp.get_model_recursive_prop(MODEL_TYPE, "loras", return_list=True):
        large.append((url, Path(wgp.get_lora_local_path(lora_dir, url))))

    print("Download plan:", flush=True)
    for url, path in large:
        print(f"  {url.rsplit('/', 1)[-1]} -> {path}", flush=True)
    for url, path in large:
        fetch(url, path)

    wgp.download_models(main_url, MODEL_TYPE)   # VAE and the small companion files
    availability = session.get_model_availability(MODEL_TYPE)
    print(f"Final status: {availability['status']}", flush=True)
    if not availability["available"]:
        print("WanGP still reports missing files. Run the installer again.", file=sys.stderr)
        return 1
    (ROOT / "data").mkdir(exist_ok=True)
    (ROOT / "data" / "viggle_ready.txt").write_text("viggle_animate\nsha256 verified\n", encoding="utf-8")
    print("MODELS_READY", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
