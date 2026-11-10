from __future__ import annotations

import asyncio
import shutil
import uuid
import logging
from pathlib import Path
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from .wangp_engine import WanGPAnimateEngine
from .models import JobStatus, RenderJob, RenderSettings, Resolution

ROOT = Path(__file__).resolve().parents[1]
UPLOADS = ROOT / "data" / "uploads"
UPLOADS.mkdir(parents=True, exist_ok=True)
app = FastAPI(title="Halfjutsu", docs_url=None)
app.mount("/static", StaticFiles(directory=ROOT / "app" / "static"), name="static")
jobs: dict[str, RenderJob] = {}
engine = WanGPAnimateEngine()
MODES = {"fast": (6, True), "full": (20, False)}  # quality mode -> (steps, accelerator LoRA)
render_lock = asyncio.Lock()


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return (ROOT / "app" / "templates" / "index.html").read_text(encoding="utf-8")


async def save_upload(upload: UploadFile, destination: Path) -> None:
    with destination.open("wb") as handle:
        shutil.copyfileobj(upload.file, handle)


@app.post("/api/jobs", status_code=201)
async def create_job(
    video: UploadFile = File(...),
    edited_frame: UploadFile | None = File(None),
    reference: UploadFile | None = File(None),
    resolution: Resolution = Form(Resolution.p720),
    mode: str = Form("fast"),
    seed: int = Form(0),
    prompt: str = Form(""),
    # Viggle-Animate controls (empty = model default)
    steps: int | None = Form(None),
    flow_shift: float | None = Form(None),
    sample_solver: str | None = Form(None),
    window_frames: int | None = Form(None),
    window_overlap: int | None = Form(None),
    start_seconds: float = Form(0.0),
    duration_seconds: float | None = Form(None),
    preserve_audio: bool = Form(True),
    lip_sync: bool = Form(True),
) -> dict:
    # An uploaded file input with nothing selected arrives as an empty upload.
    edited_frame = edited_frame if edited_frame is not None and edited_frame.filename else None
    reference = reference if reference is not None and reference.filename else None
    if mode not in MODES:
        raise HTTPException(400, "Unknown quality mode.")
    animate_steps, fast = MODES[mode]
    if sample_solver not in (None, "", "euler", "dpm++", "unipc"):
        raise HTTPException(400, "Unknown solver.")
    if window_frames and window_overlap and window_overlap >= window_frames - 8:
        raise HTTPException(400, "The overlap must be well below the window size.")
    viggle = {"flow_shift": flow_shift, "sample_solver": sample_solver or None, "window_frames": window_frames,
              "window_overlap": window_overlap, "start_seconds": start_seconds,
              "duration_seconds": duration_seconds or None, "preserve_audio": preserve_audio,
              "lip_sync": lip_sync}
    if not (video.content_type or "").startswith("video/"):
        raise HTTPException(400, "The source file must be a video.")
    if edited_frame is None:
        raise HTTPException(400, "Upload the edited frame.")
    for upload in (edited_frame, reference):
        if upload is not None and not (upload.content_type or "").startswith("image/"):
            raise HTTPException(400, "The edited frame must be an image.")
    job_id = uuid.uuid4().hex[:12]
    folder = UPLOADS / job_id
    folder.mkdir()
    video_path = folder / "source.mp4"
    await save_upload(video, video_path)
    edited_path = reference_path = None
    if edited_frame is not None:
        edited_path = folder / "edited_frame.png"
        await save_upload(edited_frame, edited_path)
    if reference is not None:
        reference_path = folder / "reference.png"
        await save_upload(reference, reference_path)
    job = RenderJob(id=job_id, video_path=video_path, reference_path=reference_path, edited_frame_path=edited_path,
                    settings=RenderSettings(resolution=resolution, fast=fast, seed=seed, **viggle,
                                            steps=(steps or 3) if edited_frame is not None else animate_steps),
                    prompt=prompt)
    if edited_path is not None:
        job.stages = ["Check local weights", "Normalise the video and fit the edited frame",
                      "Propagate the edited frame with Viggle-Animate",
                      "Trim to length and restore the original audio"]
        job.message = "Ready to render with local Viggle-Animate."
    else:
        job.stages = ["Check local weights", "Track the person and build masks (SAM 3.1)",
                      "Extract pose and face (DWPose)", "Replace the person with Wan Animate",
                      "Restore audio and check length"]
        job.message = "Ready to render with local Wan Animate."
    job.status = JobStatus.ready
    jobs[job_id] = job
    return job.model_dump(mode="json")


@app.get("/api/system")
async def system_status() -> dict:
    """Live GPU/RAM figures for the front end (best effort; empty values if unavailable)."""
    import subprocess

    info = {"gpu": None, "vram_used": None, "vram_total": None, "gpu_util": None, "ram_free_gb": None}
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.used,memory.total,utilization.gpu",
                              "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=3).stdout
        name, used, total, util = [part.strip() for part in out.splitlines()[0].split(",")]
        info.update(gpu=name, vram_used=int(used), vram_total=int(total), gpu_util=int(util))
    except Exception:
        pass
    try:
        import ctypes

        class MemoryStatus(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

        status = MemoryStatus()
        status.dwLength = ctypes.sizeof(MemoryStatus)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
        info["ram_free_gb"] = round(status.ullAvailPhys / 1024**3, 1)
        info["ram_total_gb"] = round(status.ullTotalPhys / 1024**3, 1)
    except Exception:
        pass
    return info


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str) -> dict:
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found.")
    return job.model_dump(mode="json")


@app.get("/api/jobs/{job_id}/output")
async def get_output(job_id: str) -> FileResponse:
    job = jobs.get(job_id)
    output = job.video_path.parent / "result.mp4" if job else None
    if not output or not output.is_file() or job.status != JobStatus.completed:
        raise HTTPException(404, "The result is not available yet.")
    return FileResponse(output, media_type="video/mp4", filename=f"pythonjutsu-{job.id}.mp4")


@app.get('/api/jobs/{job_id}/mask-preview')
async def mask_preview(job_id: str) -> FileResponse:
    job = jobs.get(job_id)
    path = job.video_path.parent / 'prepared_swap' / 'tracking_preview.jpg' if job else None
    if not path or not path.is_file():
        raise HTTPException(404, 'Tracking has not finished yet.')
    return FileResponse(path, media_type='image/jpeg')


async def render_job(job: RenderJob) -> None:
    try:
        if not engine.available():
            raise RuntimeError("No CUDA GPU or WanGP environment found: run install.bat.")
        async with render_lock:
            job.status = JobStatus.running
            await engine.render(job)
            job.status = JobStatus.completed
            job.message = "Render complete."
    except Exception as exc:
        logging.exception('Job %s failed', job.id)
        job.status = JobStatus.failed
        job.message = str(exc)


@app.post("/api/jobs/{job_id}/run", status_code=202)
async def run_job(job_id: str, background: BackgroundTasks) -> dict:
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found.")
    if job.status in (JobStatus.running, JobStatus.queued):
        raise HTTPException(409, "The job is already running.")
    if job.status == JobStatus.completed:
        raise HTTPException(409, "The job has already finished.")
    job.status = JobStatus.queued
    job.message = "Queued for the GPU…"
    background.add_task(render_job, job)
    return {"id": job.id, "status": "queued"}
