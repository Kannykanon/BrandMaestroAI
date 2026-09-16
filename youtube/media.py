"""ffmpeg building blocks for rendering: still shots, fitted clips, joining, captions and audio.

    YT_FFMPEG          path to ffmpeg (default: ffmpeg on PATH, else the imageio-ffmpeg binary)
    YT_RENDER_FPS      frames per second (default 30)
    YT_RENDER_PRESET   x264 preset (default veryfast)

Every segment is cut to an exact number of frames computed from the audio
timeline, so video and voice stay in sync however many shots a story has.
"""
from __future__ import annotations

import io
import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

SIZES = {"long_form": (1920, 1080), "short": (1080, 1920)}
# How much larger than the frame a still image is scaled, leaving room to pan.
PAN_MARGIN = 1.12


class MediaError(RuntimeError):
    """ffmpeg failed or is not available."""


def _env(name: str) -> str:
    return os.getenv(name, "").strip()


def ffmpeg_exe() -> str:
    configured = _env("YT_FFMPEG")
    if configured:
        return configured
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as e:
        raise MediaError("ffmpeg is not installed (install it, or set YT_FFMPEG)") from e


def ffmpeg_available() -> bool:
    try:
        ffmpeg_exe()
        return True
    except MediaError:
        return False


def run_ffmpeg(args: list[str], cwd: Optional[Path] = None) -> None:
    command = [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", *args]
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise MediaError(f"ffmpeg failed: {result.stderr.strip()[-800:]}")


def media_info(path: Path) -> dict:
    """Duration and stream types of a media file, read from ffmpeg's own report."""
    result = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", str(path)], capture_output=True, text=True)
    report = result.stderr
    match = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", report)
    duration = int(match.group(1)) * 3600 + int(match.group(2)) * 60 + float(match.group(3)) if match else None
    size = re.search(r"Video: .*?, (\d{2,5})x(\d{2,5})", report)
    return {
        "duration_s": duration,
        "has_video": "Video:" in report,
        "has_audio": "Audio:" in report,
        "width": int(size.group(1)) if size else None,
        "height": int(size.group(2)) if size else None,
    }


@dataclass(frozen=True)
class RenderSettings:
    width: int
    height: int
    fps: int = 30
    preset: str = "veryfast"
    crf: int = 20

    @classmethod
    def for_format(cls, video_format: str, scale: float = 1.0) -> "RenderSettings":
        width, height = SIZES[video_format]
        # Even dimensions: yuv420p requires them.
        width, height = int(width * scale) // 2 * 2, int(height * scale) // 2 * 2
        return cls(width, height, int(_env("YT_RENDER_FPS") or 30), _env("YT_RENDER_PRESET") or "veryfast")

    def frames(self, seconds: float) -> int:
        return max(int(round(seconds * self.fps)), 1)

    def _encode(self) -> list[str]:
        return ["-c:v", "libx264", "-preset", self.preset, "-crf", str(self.crf), "-pix_fmt", "yuv420p",
                "-r", str(self.fps), "-an"]


def still_segment(image: Path, frames: int, out: Path, settings: RenderSettings, direction: int = 0,
                  pan: bool = True) -> None:
    """Hold an image for `frames` frames with a slow pan across it (or perfectly still, for an end card)."""
    w, h = settings.width, settings.height
    if not pan:
        filters = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1"
        run_ffmpeg(["-loop", "1", "-i", str(image), "-vf", filters, "-frames:v", str(frames),
                    *settings._encode(), str(out)])
        return
    big_w, big_h = int(w * PAN_MARGIN) // 2 * 2, int(h * PAN_MARGIN) // 2 * 2
    span = max(frames - 1, 1)
    # Alternate the pan direction between shots so consecutive stills do not all drift the same way.
    x = f"(iw-{w})*n/{span}" if direction % 2 == 0 else f"(iw-{w})*(1-n/{span})"
    y = f"(ih-{h})*n/{span}"
    filters = (f"scale={big_w}:{big_h}:force_original_aspect_ratio=increase,crop={big_w}:{big_h},"
               f"crop={w}:{h}:x='{x}':y='{y}',setsar=1")
    run_ffmpeg(["-loop", "1", "-i", str(image), "-vf", filters, "-frames:v", str(frames),
                *settings._encode(), str(out)])


# A clip is generated from the shot's still, so most of the frame is unchanged
# and only what the avatar model redrew (the head, mostly) shifts in colour.
# Correcting the whole frame dilutes that shift; these find the changed part.
DIFF_SAMPLE_WIDTH = 256
MIN_CHANGED_FRACTION = 0.02


def _as_array(image, size):
    import numpy as np

    return np.asarray(image.convert("RGB").resize(size), dtype=float)


def changed_region(reference, sample):
    """(mask over what the clip redrew, per-channel stats of both there), or None if nothing moved."""
    import numpy as np
    from PIL import Image, ImageFilter

    size = (DIFF_SAMPLE_WIDTH, max(int(DIFF_SAMPLE_WIDTH * reference.height / reference.width), 1))
    ref, smp = _as_array(reference, size), _as_array(sample, size)
    difference = np.abs(ref - smp).max(axis=2)
    # Relative to the biggest change, so a shift over the whole frame is corrected
    # everywhere while a redrawn head is corrected only there.
    threshold = max(8.0, float(difference.max()) * 0.35)
    changed = difference >= threshold
    if changed.mean() < MIN_CHANGED_FRACTION:
        return None
    stats = []
    for channel in range(3):
        target, source = ref[..., channel][changed], smp[..., channel][changed]
        stats.append(((float(target.mean()), float(target.std())), (float(source.mean()), float(source.std()))))
    mask = Image.fromarray((changed * 255).astype("uint8")).filter(ImageFilter.GaussianBlur(size[0] * 0.02))
    return mask, stats


def color_match_filter(reference: Path, clip: Path, workdir: Path):
    """(lutrgb filter, mask file) that give a clip the colours of the still it was animated from.

    Avatar models return the redrawn face softer and cooler than the image they
    were given, which shows at the cut into a talking shot. The part of the frame
    the model changed is found by comparing the clip with the still; each channel's
    mean and spread are matched there, with the correction limited, and applied
    through a soft mask so the untouched background keeps the still's own colours.
    """
    from PIL import Image

    sample = workdir / f"{clip.stem}-sample.png"
    try:
        run_ffmpeg(["-ss", "0.5", "-i", str(clip), "-frames:v", "1", str(sample)])
        with Image.open(reference) as ref, Image.open(sample) as frame:
            found = changed_region(ref, frame)
            if found is None:
                return None, None
            mask, stats = found
            mask_path = workdir / f"{clip.stem}-mask.png"
            mask.convert("RGB").save(mask_path)
    except Exception as e:
        logger.warning("Colour match skipped for %s: %s", clip.name, e)
        return None, None
    parts = []
    for name, ((t_mean, t_std), (s_mean, s_std)) in zip("rgb", stats):
        gain = min(max(t_std / s_std, 0.75), 1.35) if s_std > 1 else 1.0
        parts.append(f"{name}='clip((val-{s_mean:.2f})*{gain:.3f}+{t_mean:.2f},0,255)'")
    return "lutrgb=" + ":".join(parts), mask_path


def clip_segment(clip: Path, frames: int, out: Path, settings: RenderSettings,
                 match_to: Optional[Path] = None) -> None:
    """Fit a talking clip to the frame (cover, centred) and cut it to `frames` frames.

    Upscaled with Lanczos and lightly sharpened, then colour-matched to match_to (the
    shot's still) when given. If the clip is shorter than needed, its last frame is held.
    """
    w, h = settings.width, settings.height
    fit = [f"scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos", f"crop={w}:{h}",
           "unsharp=5:5:0.6:5:5:0.0"]
    tail = ["setsar=1", f"fps={settings.fps}", "tpad=stop_mode=clone:stop_duration=10"]
    correction, mask = color_match_filter(match_to, clip, out.parent) if match_to is not None else (None, None)
    if correction is None:
        run_ffmpeg(["-i", str(clip), "-vf", ",".join(fit + tail), "-frames:v", str(frames),
                    *settings._encode(), str(out)])
        return
    # The corrected copy is laid over the original through the mask as its alpha.
    # (maskedmerge blends the whole frame towards the overlay even where the mask
    # is black, which moved background colours that were already right.)
    graph = (f"[0:v]{','.join(fit)},format=rgb24,split=2[base][raw];"
             f"[raw]{correction}[corrected];"
             f"[1:v]scale={w}:{h},format=gray[mask];"
             f"[corrected][mask]alphamerge[face];"
             f"[base][face]overlay=format=rgb,format=yuv420p,{','.join(tail)}[v]")
    run_ffmpeg(["-i", str(clip), "-loop", "1", "-i", str(mask), "-filter_complex", graph, "-map", "[v]",
                "-frames:v", str(frames), *settings._encode(), str(out)])


def mix_audio(voice: Path, total_s: float, out: Path, music: Optional[Path] = None, music_volume: float = 0.15,
              beds: Optional[list[tuple[Path, float, float]]] = None, ambience_volume: float = 0.35) -> None:
    """Lay music and ambience beds under the voice, ducked whenever someone speaks.

    beds: (file, start seconds, length seconds). The result is as long as the voice track.
    """
    beds = beds or []
    args, chains, labels = ["-i", str(voice)], [], []
    fmt = "aformat=sample_rates=48000:channel_layouts=stereo"
    index = 1
    if music is not None:
        args += ["-stream_loop", "-1", "-i", str(music)]
        fade_start = max(total_s - 2.0, 0.0)
        chains.append(f"[{index}:a]{fmt},atrim=0:{total_s:.3f},asetpts=PTS-STARTPTS,volume={music_volume:.3f},"
                      f"afade=t=in:d=1,afade=t=out:st={fade_start:.3f}:d=2[m]")
        labels.append("[m]")
        index += 1
    for n, (path, start, length) in enumerate(beds):
        args += ["-stream_loop", "-1", "-i", str(path)]
        fade = min(0.4, length / 3)
        chains.append(f"[{index}:a]{fmt},atrim=0:{length:.3f},asetpts=PTS-STARTPTS,volume={ambience_volume:.3f},"
                      f"afade=t=in:d={fade:.3f},afade=t=out:st={max(length - fade, 0):.3f}:d={fade:.3f},"
                      f"adelay=delays={int(start * 1000)}:all=1[b{n}]")
        labels.append(f"[b{n}]")
        index += 1
    if not labels:
        raise MediaError("Nothing to mix under the voice")
    chains.append(f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0:duration=longest,apad,"
                  f"atrim=0:{total_s:.3f}[bed]")
    chains.append(f"[0:a]{fmt},asplit=2[voice][key]")
    chains.append("[bed][key]sidechaincompress=threshold=0.02:ratio=10:attack=15:release=400[ducked]")
    chains.append("[voice][ducked]amix=inputs=2:normalize=0:duration=first[out]")
    run_ffmpeg([*args, "-filter_complex", ";".join(chains), "-map", "[out]", "-ar", "48000", "-ac", "2", str(out)])


def join_and_finish(segments: list[Path], audio: Path, captions_ass: Optional[str], out: Path,
                    settings: RenderSettings, workdir: Path) -> None:
    """Join segments, lay the voice track under them, burn captions, and normalise loudness."""
    listing = workdir / "segments.txt"
    listing.write_text("".join(f"file '{p.name}'\n" for p in segments), encoding="utf-8")
    filters = ["setsar=1"]
    if captions_ass:
        # A relative name avoids escaping drive letters and backslashes in the filter syntax.
        (workdir / "captions.ass").write_text(captions_ass, encoding="utf-8")
        filters.insert(0, "subtitles=captions.ass")
    run_ffmpeg([
        "-f", "concat", "-safe", "0", "-i", listing.name,
        "-i", str(audio.resolve()),
        "-map", "0:v:0", "-map", "1:a:0",
        "-vf", ",".join(filters),
        "-c:v", "libx264", "-preset", settings.preset, "-crf", str(settings.crf), "-pix_fmt", "yuv420p",
        "-r", str(settings.fps),
        "-af", "loudnorm=I=-14:TP=-1.5:LRA=11", "-ar", "48000",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest", "-movflags", "+faststart",
        str(out.resolve()),
    ], cwd=workdir)


def thumbnail(image_bytes: bytes, video_format: str) -> bytes:
    """A 1280x720 (or 720x1280 for Shorts) JPEG cropped from a shot image."""
    from PIL import Image

    size = (1280, 720) if video_format == "long_form" else (720, 1280)
    with Image.open(io.BytesIO(image_bytes)) as image:
        image = image.convert("RGB")
        scale = max(size[0] / image.width, size[1] / image.height)
        resized = image.resize((max(round(image.width * scale), size[0]), max(round(image.height * scale), size[1])))
        left, top = (resized.width - size[0]) // 2, (resized.height - size[1]) // 2
        out = io.BytesIO()
        resized.crop((left, top, left + size[0], top + size[1])).save(out, format="JPEG", quality=90)
    return out.getvalue()
