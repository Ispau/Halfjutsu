# Long videos: what we measured

Viggle-Animate generates at most **124 frames (5.2 s at 24 fps)** per pass. WanGP's default
for longer clips is a *sliding window*: each new window starts from the last 18 frames the
previous window **generated**. Those frames carry a little over-sharpening and noise, and the
next window builds on them, so the error compounds.

Metric: mean absolute Laplacian of the luma at 176×320 ("detail noise"). The source video
sits at ~14 throughout.

## Sliding window, no correction (64 s clip, 15 windows)

| Second | 0–12 | 20 | 30 | 40 | 50 | 64 |
|---|---|---|---|---|---|---|
| Detail noise | 13.5–15 | 17.9 | 21 | 25 | 35 | **45** |

Visually the user saw degradation from ~8 s (inside the second window). By the end the image
breaks into blocky mosaics and over-saturated colour.

Proof that it is the chaining and not the content: the same 5 s at second 50, rendered as a
fresh single window from the edited frame, measured 13–14 (clean).

## Attempts

| Approach | Result | Verdict |
|---|---|---|
| Independent segments of ~13 s, 0.5 s cross-fade | Clean, but a visible double contour during each fade | Rejected |
| Clean the 18 carried-over frames (softening + contrast match to window 1), via a hook in WanGP's window loop | 45 → 28 at 64 s: slower, still drifts | Rejected |
| Chained reference: 5.2 s chunks, each using the previous chunk's last generated frame as its reference | Smooth joins, but "photocopy" drift: 28 at 64 s, saturation 33 → 43, hair shape changed | Rejected |
| **Independent 5.2 s chunks, all on the uploaded edited frame, hard cut at the best frame** | Flat 12–14 over the whole clip | **Adopted** |

## Why independent chunks never join perfectly

Each chunk starts from noise. The edited frame shows the character in *one* pose; for every
other pose the model invents details (hair strands, shading, teeth, folds). Two chunks
invent them differently, so at the same instant they differ. A cut shows that difference
for one frame; a cross-fade shows both at once (ghosting).

## The join

Chunks overlap by `CUT_MARGIN` = 24 frames. For each frame of the overlap both chunks have
their own version; `join_hard_cuts()` (in `app/wangp_engine.py`) computes the mean colour
difference between the two versions on 88×160 thumbnails and cuts at the minimum. After the
cut, the new chunk's per-channel mean/contrast starts matched to the last frame of the old
chunk and eases back over 24 frames (a colour correction, not a blend).

20 s test, 4 cuts: chosen jumps 4.4–6.4, versus 6.5–8.0 when cutting at the first shared
frame and up to 12.8 at the worst one. For reference, a normal frame-to-frame change in that
clip is 2.5 (median) and 6.7 for fast motion (95th percentile).
