from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from pydantic import BaseModel, Field


class Resolution(StrEnum):
    p480 = "480p"
    p720 = "720p"


class JobStatus(StrEnum):
    ready = "ready"
    queued = "queued"
    planning = "planning"
    needs_engine = "needs_engine"
    running = "running"
    completed = "completed"
    failed = "failed"


class RenderSettings(BaseModel):
    resolution: Resolution = Resolution.p720
    steps: int = Field(default=3, ge=1, le=40)  # Viggle is DMD-distilled for 3 steps
    fast: bool = True  # Wan Animate only (lightx2v LoRA)
    seed: int = Field(default=0, ge=0)
    preserve_audio: bool = True
    no_lip_sync: bool = False  # Wan Animate only
    lip_sync: bool = True  # Viggle: condition the mouth on the clip's own soundtrack (WanGP audio_prompt_type A)
    # Viggle-Animate controls (None = WanGP's default for the model)
    flow_shift: float | None = Field(default=None, ge=1.0, le=20.0)
    sample_solver: str | None = None  # euler (default) / dpm++ / unipc
    window_frames: int | None = Field(default=None, ge=33, le=124)
    window_overlap: int | None = Field(default=None, ge=1, le=40)
    start_seconds: float = Field(default=0.0, ge=0.0)
    duration_seconds: float | None = Field(default=None, gt=0.0)  # None = until the end


class RenderProgress(BaseModel):
    """Structured progress for the front end (all optional; message stays the human text)."""
    phase: str = "Queued"
    percent: float = 0.0          # whole job, 0–100
    window: int = 0
    windows: int = 0
    step: int = 0
    steps: int = 0
    started_at: float | None = None   # epoch seconds when rendering began
    frames: int = 0                   # frames to generate
    width: int = 0
    height: int = 0


class RenderJob(BaseModel):
    id: str
    video_path: Path
    reference_path: Path | None = None
    edited_frame_path: Path | None = None  # set -> Viggle-Animate; unset -> Wan Animate with reference_path
    settings: RenderSettings
    prompt: str = ""
    status: JobStatus = JobStatus.queued
    message: str = "Queued"
    stages: list[str] = Field(default_factory=list)
    progress: RenderProgress = Field(default_factory=RenderProgress)
