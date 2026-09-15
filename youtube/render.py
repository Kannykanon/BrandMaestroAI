"""Render an approved storyboard into a finished MP4.

    timeline   every shot's start and length, from its audio and the gaps between lines
    avatars    dialogue shots are animated for at most YT_MAX_TALKING_SECONDS; the rest of a
               long line plays over the shot's still image (a cutaway)
    budget     avatar cost is estimated first; above YT_AVATAR_BUDGET_USD a render needs
               explicit confirmation
    composer   ffmpeg joins still and talking segments, lays the voice track under them,
               burns captions and normalises loudness

    YT_MAX_TALKING_SECONDS  default 6
    YT_AVATAR_BUDGET_USD    default 5
    YT_RENDER_SCALE         render at a fraction of full size, e.g. 0.5 for quick drafts
    YT_KEEP_RENDERS         how many renders to keep per project (default 3)
"""
from __future__ import annotations

import logging
import os
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session, object_session

from youtube import projects, storyboard
from youtube.audio import Audio, SAMPLE_RATE, concatenate, silence
from youtube.avatar import AvatarPort, AvatarRegistry
from youtube.captions import build_cues, to_ass, word_timings
from youtube.media import (
    RenderSettings,
    clip_segment,
    ffmpeg_available,
    join_and_finish,
    media_info,
    still_segment,
    thumbnail,
)
from youtube.models import YTCost, YTProject, YTRender, YTShot
from youtube.projects import ProjectError
from youtube.script_parser import NARRATOR
from youtube.storage import StoragePort, business_key

logger = logging.getLogger(__name__)

TALKING_SHOT_TYPES = {"dialogue", "two_character"}
END_TAIL_S = 0.6  # a moment of the last image after the final word


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name, "").strip()
    return float(value) if value else default


def max_talking_seconds() -> float:
    return _env_float("YT_MAX_TALKING_SECONDS", 6.0)


def avatar_budget_usd() -> float:
    return _env_float("YT_AVATAR_BUDGET_USD", 5.0)


@dataclass
class TimedShot:
    shot: YTShot
    start: float
    speech_s: float
    gap_s: float
    talk_s: float  # animated seconds at the start of the shot; 0 for a still shot

    @property
    def length_s(self) -> float:
        return self.speech_s + self.gap_s


def timeline(shots: list[YTShot], port: AvatarPort) -> list[TimedShot]:
    """Shot timings matching the voice track built by projects.voice_project."""
    cap = max_talking_seconds()
    timed, cursor = [], 0.0
    for i, shot in enumerate(shots):
        if i + 1 < len(shots):
            gap = (projects.GAP_SAME_SPEAKER_S if shots[i + 1].speaker_label == shot.speaker_label
                   else projects.GAP_SPEAKER_CHANGE_S)
        else:
            gap = END_TAIL_S
        speech = float(shot.duration_s or 0.0)
        talks = shot.shot_type in TALKING_SHOT_TYPES and shot.speaker_label != NARRATOR
        talk = min(speech, cap, port.max_seconds) if talks else 0.0
        timed.append(TimedShot(shot, cursor, speech, gap, talk))
        cursor += speech + gap
    return timed


def _needs_clip(item: TimedShot, port: AvatarPort) -> bool:
    shot = item.shot
    if item.talk_s <= 0:
        return False
    if shot.clip_provider != port.name:
        return True
    return abs(float(shot.clip_duration_s or 0.0) - item.talk_s) > 0.05


def estimate(db: Session, project: YTProject, port: Optional[AvatarPort] = None) -> dict:
    """What rendering would animate and cost. Clips already made with this provider are reused."""
    port = port or AvatarRegistry.get()
    shots = projects._shots(db, project)
    items = timeline(shots, port)
    new = [i for i in items if _needs_clip(i, port)]
    cost = sum(port.cost_usd(i.talk_s) for i in new)
    budget = avatar_budget_usd()
    return {
        "avatar_provider": port.name,
        "lip_sync": port.lip_sync,
        "provider_configured": not port.missing_env(),
        "talking_shots": sum(1 for i in items if i.talk_s > 0),
        "talking_seconds": round(sum(i.talk_s for i in items), 1),
        "new_clips": len(new),
        "new_talking_seconds": round(sum(i.talk_s for i in new), 1),
        "avatar_cost_usd": round(cost, 2),
        "budget_usd": budget,
        "over_budget": cost > budget,
        "max_talking_seconds": max_talking_seconds(),
        "video_seconds": round(sum(i.length_s for i in items) + _card_seconds(project), 1) if items else 0.0,
        "end_card": _card_seconds(project) > 0,
    }


def _mix_beds(db: Session, project: YTProject, storage: StoragePort, items: list[TimedShot], voice: Path,
              total_s: float, work: Path) -> Path:
    """Music and ambience under the voice, when the project has either. Otherwise the voice alone."""
    from youtube.brand_assets import get_asset
    from youtube.media import mix_audio
    from youtube.sound import ambience_beds, audio_settings

    settings = audio_settings(project)
    music = get_asset(db, project.business_id, settings["music_asset_id"]) if settings["music_asset_id"] else None
    beds = ambience_beds(db, project, items, storage)
    if music is None and not beds:
        return voice
    music_path = None
    if music is not None:
        music_path = work / f"music.{music.storage_key.rsplit('.', 1)[-1]}"
        music_path.write_bytes(storage.get(music.storage_key))
    bed_files = []
    for n, bed in enumerate(beds):
        path = work / f"ambience-{bed.key.rsplit('/', 1)[-1]}"
        if not path.exists():
            path.write_bytes(storage.get(bed.key))
        bed_files.append((path, bed.start_s, bed.length_s))
    mixed = work / "mixed.wav"
    mix_audio(voice, total_s, mixed, music_path, settings["music_volume"], bed_files, settings["ambience_volume"])
    return mixed


def _card_seconds(project: YTProject) -> float:
    from youtube.brand_assets import active_end_card
    card = active_end_card(project)
    return float(card["seconds"]) if card else 0.0


def render_problems(db: Session, project: YTProject, port: Optional[AvatarPort] = None,
                    check_ffmpeg: bool = True) -> list[str]:
    """What must be fixed before rendering.

    ffmpeg is only checked where the render runs: the API queues renders but
    has no ffmpeg of its own, so it passes check_ffmpeg=False.
    """
    port = port or AvatarRegistry.get()
    problems = []
    if not project.storyboard_approved_at:
        problems.append("Approve the storyboard first")
    shots = projects._shots(db, project)
    if not shots:
        problems.append("Plan the shots first")
    if any(not s.audio_key for s in shots) or not project.audio_key:
        problems.append("Every shot needs its voice audio")
    if any(not s.image_key for s in shots):
        problems.append("Every shot needs a storyboard image")
    if port.missing_env():
        problems.append(f"The avatar provider {port.name} is not configured (missing {', '.join(port.missing_env())})")
    try:
        from youtube.sound import SoundRegistry
        sound = SoundRegistry.get()
        if sound.enabled and sound.missing_env() and any(s.sound for s in shots):
            problems.append(f"The sound provider {sound.name} is not configured (missing {', '.join(sound.missing_env())})")
    except ValueError as e:
        problems.append(str(e))
    if check_ffmpeg and not ffmpeg_available():
        problems.append("ffmpeg is not available on the render worker")
    return problems


def _talk_audio(shot_audio: Audio, talk_s: float, min_s: float) -> Audio:
    """The first talk_s seconds of a line, padded with silence up to the provider's minimum."""
    samples = shot_audio.samples[: int(round(talk_s * SAMPLE_RATE))]
    audio = Audio(samples, SAMPLE_RATE)
    if audio.duration_s < min_s:
        audio = concatenate([audio, silence(min_s - audio.duration_s)])
    return audio


def animate_shots(db: Session, project: YTProject, storage: StoragePort, port: AvatarPort) -> None:
    """Make the talking clip for every dialogue shot that lacks a current one. Stops at the first failure."""
    for item in timeline(projects._shots(db, project), port):
        if not _needs_clip(item, port):
            continue
        shot = item.shot
        audio = _talk_audio(Audio.from_wav(storage.get(shot.audio_key)), item.talk_s, port.min_seconds)
        image = storage.get(shot.image_key)
        prompt = f"{shot.speaker_label.title()} speaks naturally. {shot.delivery or ''}".strip()
        try:
            clip = port.animate(image, storyboard.mime_for_key(shot.image_key), audio.to_wav(), audio.duration_s, prompt)
        except Exception as e:
            raise ProjectError(f"Animating shot {shot.position} failed: {e}") from e

        old_key = shot.clip_key
        if clip.video is not None:
            shot.clip_key = business_key(project.business_id, "projects", str(project.id), "clips",
                                         f"shot-{shot.position:04d}-{uuid.uuid4().hex[:12]}.mp4")
            storage.put(shot.clip_key, clip.video, content_type="video/mp4")
        else:
            shot.clip_key = None
        if old_key and old_key != shot.clip_key:
            storyboard._delete(storage, old_key)
        shot.clip_provider, shot.clip_duration_s = port.name, item.talk_s
        cost = port.cost_usd(item.talk_s)
        if cost:
            db.add(YTCost(project_id=project.id, stage="avatar", provider=port.name,
                          units=round(max(item.talk_s, port.min_seconds), 2), unit="second", cost_usd=cost))
        db.commit()  # keep each paid-for clip even if a later one fails


def captions_for(items: list[TimedShot], settings: RenderSettings) -> str:
    cues = []
    for item in items:
        cues += build_cues(word_timings(item.shot.text, item.start, item.speech_s))
    return to_ass(cues, settings.width, settings.height)


def compose(db: Session, project: YTProject, storage: StoragePort, port: AvatarPort,
            scale: Optional[float] = None) -> tuple[bytes, float, bytes]:
    """Build the MP4. Returns (video bytes, duration, thumbnail JPEG)."""
    settings = RenderSettings.for_format(project.format, scale or _env_float("YT_RENDER_SCALE", 1.0))
    shots = projects._shots(db, project)
    items = timeline(shots, port)

    with tempfile.TemporaryDirectory(prefix="yt-render-") as tmp:
        work = Path(tmp)
        segments: list[Path] = []
        frame_cursor, time_cursor = 0, 0.0

        def next_frames(seconds: float) -> int:
            nonlocal frame_cursor, time_cursor
            time_cursor += seconds
            end = int(round(time_cursor * settings.fps))
            count = max(end - frame_cursor, 1)
            frame_cursor += count
            return count

        for index, item in enumerate(items):
            shot = item.shot
            ext = shot.image_key.rsplit(".", 1)[-1]
            image = work / f"image-{shot.position:04d}.{ext}"
            image.write_bytes(storage.get(shot.image_key))

            talk_s = item.talk_s if shot.clip_key else 0.0
            if talk_s > 0:
                clip = work / f"clip-{shot.position:04d}.mp4"
                clip.write_bytes(storage.get(shot.clip_key))
                out = work / f"seg-{len(segments):04d}.mp4"
                clip_segment(clip, next_frames(talk_s), out, settings, match_to=image)
                segments.append(out)
            remaining = item.length_s - talk_s
            if remaining > 0.01:
                out = work / f"seg-{len(segments):04d}.mp4"
                still_segment(image, next_frames(remaining), out, settings, direction=index)
                segments.append(out)

        card_s = _card_seconds(project)
        if card_s:
            from youtube.brand_assets import render_end_card
            card = work / "end-card.png"
            card.write_bytes(render_end_card(db, project, storage, settings.width, settings.height))
            out = work / f"seg-{len(segments):04d}.mp4"
            still_segment(card, next_frames(card_s), out, settings, pan=False)
            segments.append(out)

        # The voice track plus a short silent tail (and silence under the end card).
        track = Audio.from_wav(storage.get(project.audio_key))
        audio = work / "voice.wav"
        voice_track = concatenate([track, silence(END_TAIL_S + card_s)])
        audio.write_bytes(voice_track.to_wav())
        audio = _mix_beds(db, project, storage, items, audio, voice_track.duration_s, work)

        final = work / "final.mp4"
        join_and_finish(segments, audio, captions_for(items, settings), final, settings, work)
        info = media_info(final)
        first_dialogue = next((i.shot for i in items if i.talk_s > 0), shots[0])
        thumb = thumbnail(storage.get(first_dialogue.image_key), project.format)
        return final.read_bytes(), float(info["duration_s"] or 0.0), thumb


def _keep_latest(db: Session, project: YTProject, storage: StoragePort) -> None:
    """Delete renders beyond the newest YT_KEEP_RENDERS. A render that was uploaded is kept with its record."""
    from youtube.models import YTUpload

    keep = int(os.getenv("YT_KEEP_RENDERS", "").strip() or 3)
    renders = db.execute(select(YTRender).where(YTRender.project_id == project.id)
                         .order_by(YTRender.id.desc())).scalars().all()
    uploaded = set(db.execute(select(YTUpload.render_id).where(
        YTUpload.render_id.in_([r.id for r in renders]))).scalars().all()) if renders else set()
    for old in renders[keep:]:
        if old.id in uploaded:
            continue
        storyboard._delete(storage, old.video_key)
        storyboard._delete(storage, old.thumbnail_key)
        db.delete(old)


def render_project(db: Session, project: YTProject, storage: StoragePort, port: Optional[AvatarPort] = None,
                   confirm_over_budget: bool = False, scale: Optional[float] = None) -> YTRender:
    port = port or AvatarRegistry.get()
    problems = render_problems(db, project, port)
    if problems:
        raise ProjectError("; ".join(problems))
    cost = estimate(db, project, port)
    if cost["over_budget"] and not confirm_over_budget:
        raise ProjectError(f"Animating would cost about ${cost['avatar_cost_usd']:.2f}, over the "
                           f"${cost['budget_usd']:.2f} budget. Confirm to render anyway.")

    animate_shots(db, project, storage, port)
    video, duration, thumb = compose(db, project, storage, port, scale=scale)

    stem = uuid.uuid4().hex[:12]
    video_key = business_key(project.business_id, "projects", str(project.id), "renders", f"render-{stem}.mp4")
    thumb_key = business_key(project.business_id, "projects", str(project.id), "renders", f"thumbnail-{stem}.jpg")
    storage.put(video_key, video, content_type="video/mp4")
    storage.put(thumb_key, thumb, content_type="image/jpeg")

    # created_at is set here rather than by the database: it is compared with
    # the storyboard approval time, and some databases store whole seconds only.
    render = YTRender(project_id=project.id, format=project.format, video_key=video_key, thumbnail_key=thumb_key,
                      status="completed", duration_s=duration, size_bytes=len(video), avatar_provider=port.name,
                      end_card=_active_card(project), mix=_mix_snapshot(db, project),
                      created_at=datetime.now(timezone.utc))
    db.add(render)
    db.flush()
    _keep_latest(db, project, storage)
    project.error = None
    projects.settle_status(db, project)
    db.commit()
    db.refresh(render)
    return render


def latest_render(db: Session, project: YTProject) -> Optional[YTRender]:
    return db.execute(select(YTRender).where(YTRender.project_id == project.id, YTRender.status == "completed")
                      .order_by(YTRender.id.desc())).scalars().first()


def _naive_utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value


def _mix_snapshot(db: Session, project: YTProject) -> Optional[dict]:
    from youtube.sound import mix_snapshot
    try:
        return mix_snapshot(db, project)
    except ValueError:
        return None


def _active_card(project: YTProject) -> Optional[dict]:
    from youtube.brand_assets import active_end_card
    return active_end_card(project)


def render_is_current(render: Optional[YTRender], project: YTProject) -> bool:
    """Whether a render reflects the storyboard as it is approved now, and the end card as it is set now."""
    if render is None or not project.storyboard_approved_at or not render.created_at:
        return False
    if (render.end_card or None) != _active_card(project):
        return False
    if getattr(render, "mix", None) is not None and render.mix != _mix_snapshot(object_session(project), project):
        return False
    return _naive_utc(render.created_at) >= _naive_utc(project.storyboard_approved_at)


def serialize_render(render: YTRender, project: YTProject) -> dict:
    return {
        "id": render.id,
        "format": render.format,
        "duration_s": round(render.duration_s, 1) if render.duration_s else None,
        "size_bytes": render.size_bytes,
        "avatar_provider": render.avatar_provider,
        "created_at": render.created_at.isoformat() if render.created_at else None,
        "current": render_is_current(render, project),
        "version": render.video_key.rsplit("/", 1)[-1] if render.video_key else None,
    }


def render_summary(db: Session, project: YTProject) -> dict:
    renders = db.execute(select(YTRender).where(YTRender.project_id == project.id, YTRender.status == "completed")
                         .order_by(YTRender.id.desc())).scalars().all()
    try:
        est = estimate(db, project)
    except ValueError as e:
        est = {"error": str(e)}
    return {"renders": [serialize_render(r, project) for r in renders], "estimate": est}
