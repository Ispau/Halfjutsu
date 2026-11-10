"""Offline media preparation and SAM 3.1 tracking; no H3 generation here."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / 'vendor' / 'WanGP'
FFMPEG = VENDOR / 'ffmpeg_bins' / 'ffmpeg.exe'
FFPROBE = VENDOR / 'ffmpeg_bins' / 'ffprobe.exe'
SAM_ASSETS = {
    'sam3.1_multiplex_bf16.safetensors': (1746597176, '4e2cd82698b64b7d32349c8bbb4f43422e199cd1bfc9ca6b88bbef562af7e99e'),
    'bpe_simple_vocab_16e6.txt.gz': (1356917, '924691ac288e54409236115652ad4aa250f48203de50a9e4722a6ecd48d6804a'),
}


def check_tracking_assets(full_hash=False):
    for name, (size, expected) in SAM_ASSETS.items():
        path = VENDOR / 'ckpts' / 'sam3' / name
        if not path.is_file() or path.stat().st_size != size:
            raise RuntimeError(f'SAM 3.1 is not prepared: {name}. It will not be downloaded during a job.')
        if full_hash:
            with path.open('rb') as stream:
                actual = hashlib.file_digest(stream, 'sha256').hexdigest()
            if actual != expected:
                raise RuntimeError(f'Integridad incorrecta: {name}')


def probe(path):
    result = subprocess.run([str(FFPROBE), '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path)], capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def normalize_only(video: Path, folder: Path, short_side=720, start=0.0, duration=None):
    """Orientation/fps/size normalisation without SAM tracking (Viggle needs no mask).

    start/duration (seconds) select the part of the clip to use.
    """
    video, folder = video.resolve(), folder.resolve()
    folder.mkdir(parents=True, exist_ok=True)
    metadata = probe(video)
    stream = next(s for s in metadata['streams'] if s['codec_type'] == 'video')
    width, height = stream['width'], stream['height']
    rotation = next((s['rotation'] for s in stream.get('side_data_list', []) if 'rotation' in s), 0)
    if round(rotation) % 180:
        width, height = height, width
    scale = short_side / min(width, height)
    width, height = (max(32, round(d * scale / 32) * 32) for d in (width, height))
    normalized = folder / 'source.mp4'
    cut = (['-ss', f'{start:.3f}'] if start else []) + ['-i', str(video)]
    if duration:
        cut += ['-t', f'{duration:.3f}']
    subprocess.run([str(FFMPEG), '-y', '-v', 'error', *cut, '-vf',
                    f'fps=24,scale={width}:{height},setsar=1', '-an', '-c:v', 'libx264', '-crf', '16',
                    '-pix_fmt', 'yuv420p', str(normalized)], check=True)
    counted = subprocess.run([str(FFPROBE), '-v', 'error', '-count_packets', '-select_streams', 'v:0',
                              '-show_entries', 'stream=nb_read_packets', '-of', 'csv=p=0', str(normalized)],
                             capture_output=True, text=True, check=True)
    # ffprobe's csv can carry extra empty fields (e.g. '278,' with rotation side data): keep the number
    frames = int(re.search(r'\d+', counted.stdout).group())
    return {'frames': frames, 'fps': 24, 'width': width, 'height': height,
            'start': start, 'source_duration': float(metadata['format']['duration']),
            'has_audio': any(s['codec_type'] == 'audio' for s in metadata['streams'])}


def prepare(video: Path, folder: Path, report=print, seconds=None, short_side=480, mask_style='wide'):
    import numpy as np
    import cv2
    check_tracking_assets()
    video, folder = video.resolve(), folder.resolve()
    folder.mkdir(parents=True, exist_ok=True)
    fingerprint = {'source': str(video), 'bytes': video.stat().st_size,
                   'mtime_ns': video.stat().st_mtime_ns, 'seconds': seconds, 'version': 2}
    if short_side != 480:  # keeps existing 480p caches valid
        fingerprint['short_side'] = short_side
    if mask_style != 'wide':
        fingerprint['mask_style'] = mask_style
    manifest_path = folder / 'manifest.json'
    if manifest_path.is_file():
        cached = json.loads(manifest_path.read_text(encoding='utf-8'))
        if cached.get('fingerprint') == fingerprint and all((folder / name).is_file()
                for name in ('source.mp4', 'edit_mask.mp4', 'motion.mp4', 'tracking_preview.jpg')):
            report('Tracking: reusing the verified preparation of this video.')
            return cached
    metadata = probe(video)
    stream = next(s for s in metadata['streams'] if s['codec_type'] == 'video')
    width, height = stream['width'], stream['height']
    rotation = next((s['rotation'] for s in stream.get('side_data_list', []) if 'rotation' in s), 0)
    if round(rotation) % 180:
        width, height = height, width
    scale = short_side / min(width, height)
    width, height = (max(32, round(d * scale / 32) * 32) for d in (width, height))
    normalized = folder / 'source.mp4'
    args = [str(FFMPEG), '-y', '-v', 'error', '-i', str(video)]
    if seconds is not None:
        args += ['-t', str(seconds)]
    args += ['-vf', f'fps=24,scale={width}:{height},setsar=1', '-an', '-c:v', 'libx264', '-crf', '16', '-pix_fmt', 'yuv420p', str(normalized)]
    report('Normalising the video orientation and timing…')
    subprocess.run(args, check=True)
    cap = cv2.VideoCapture(str(normalized))
    frames = []
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        frames.append(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    cap.release()
    if not frames:
        raise ValueError('Could not decode the frames.')
    frames = np.stack(frames)
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    sys.path.insert(0, str(VENDOR))
    from shared.utils import files_locator
    files_locator.set_checkpoints_paths([str(VENDOR / 'ckpts')])
    from preprocessing.sam3.preprocessor import run_sam3_video
    report(f'SAM 3.1: tracking the person over {len(frames)} frames…')
    masks = run_sam3_video(frames, ['person'], max_colored_objects=1,
        keep_video_frames_on_cuda=False, batched_grounding_batch_size=2,
        progress_callback=lambda done, total: report(f'Tracking: {done}/{total} frames'))
    coverage = masks.reshape(len(masks), -1).mean(axis=1)
    if not np.any(coverage > 0):
        raise RuntimeError('SAM found no person. H3 will not start with an empty mask.')
    np.savez_compressed(folder / 'tracking.npz', person=masks)
    # The sharp silhouette is distinct from the expanded area H3 may regenerate.
    edit = np.empty_like(masks)
    if mask_style == 'tight':
        # Official Wan2.2-Animate replacement preprocessing: --iterations 3 --k 7,
        # no headroom and no frame holding, so far more real background survives.
        tight_kernel = np.ones((7, 7), np.uint8)
        for i, mask in enumerate(masks):
            edit[i] = cv2.dilate(mask.astype(np.uint8), tight_kernel, iterations=3) > 0
    radius = max(1, round(40 * max(width, height) / 1008))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (radius * 2 + 1,) * 2)
    for i, mask in enumerate(masks if mask_style != 'tight' else []):
        held = np.logical_or(mask, masks[max(0, i - 1)]).astype(np.uint8)
        ys, xs = np.where(held)
        if len(ys):
            headroom = max(1, int((ys.max() - ys.min()) * .1))
            head_end = min(height, int(ys.min() + (ys.max() - ys.min()) * .25))
            upper = held.copy()
            upper[head_end:] = 0
            for offset in range(1, headroom + 1):
                held[:-offset] |= upper[offset:]
        edit[i] = cv2.dilate(held, kernel) > 0
    for name, is_mask in [('edit_mask.mp4', True), ('motion.mp4', False)]:
        process = subprocess.Popen([str(FFMPEG), '-y', '-v', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{width}x{height}', '-r', '24', '-i', 'pipe:0', '-an', '-c:v', 'libx264', '-crf', '0', '-pix_fmt', 'yuv420p', str(folder / name)], stdin=subprocess.PIPE)
        try:
            for frame, mask, expanded in zip(frames, masks, edit):
                output = np.repeat((expanded * 255).astype(np.uint8)[..., None], 3, axis=2) if is_mask else frame.copy()
                if not is_mask:
                    output[mask] = 128
                process.stdin.write(output.tobytes())
        finally:
            process.stdin.close()
        if process.wait() != 0:
            raise RuntimeError(f'Could not save {name}')
    previews = []
    for index in np.linspace(0, len(frames) - 1, 4).astype(int):
        frame = frames[index].copy()
        overlay = frame.copy()
        overlay[edit[index]] = (255, 40, 40)
        overlay = cv2.addWeighted(frame, .55, overlay, .45, 0)
        mannequin = frame.copy()
        mannequin[masks[index]] = 128
        previews.append(np.concatenate([frame, overlay, mannequin], axis=1))
    cv2.imwrite(str(folder / 'tracking_preview.jpg'), cv2.cvtColor(np.concatenate(previews), cv2.COLOR_RGB2BGR))
    manifest = {'fingerprint': fingerprint, 'source': str(video), 'source_duration': float(metadata['format']['duration']),
        'frames': len(frames), 'fps': 24, 'width': width, 'height': height,
        'has_audio': any(s['codec_type'] == 'audio' for s in metadata['streams']),
        'empty_mask_frames': int((coverage == 0).sum()), 'status': 'tracking_prepared_not_rendered'}
    (folder / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    report(json.dumps(manifest))
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--video', type=Path, default=ROOT / 'video.mp4')
    parser.add_argument('--output', type=Path, default=ROOT / 'data' / 'swap_preflight')
    parser.add_argument('--seconds', type=float)
    parser.add_argument('--short-side', type=int, default=480, help='480 or 720')
    parser.add_argument('--mask-style', choices=['wide', 'tight'], default='wide',
                        help='wide: original H3-era dilation; tight: official Wan-Animate (k=7, 3 iterations)')
    args = parser.parse_args()
    check_tracking_assets(full_hash=True)
    prepare(args.video, args.output, lambda message: print(message, flush=True), args.seconds,
            args.short_side, args.mask_style)
