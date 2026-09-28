"""Brand assets (product photos and logos), products in storyboard shots, screens, and the end card.

    assets     images a business uploads once: `product` photos, `logo` files and `screen` shots
    products   a project lists the products it features; a shot shows a product when its
               line or visual names it, or when a person picks products for that shot
    screens    app or website screenshots; a shot a person sets to a screen shows it exactly,
               framed in code, instead of a drawn scene: a tutorial step on the real interface
    end card   the closing frame of an ad: logo, call to action and URL on a brand colour

Product photos are sent to the image model as references with an instruction to
reproduce the product exactly. Screens never reach a model: an image model redraws
an interface with invented buttons and garbled labels, which in a how-to is worse
than no picture. Screens and the end card are drawn in code, so their text is exact.
"""
from __future__ import annotations

import io
import re
import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from youtube import projects
from youtube.images import inspect_image
from youtube.models import YTAsset, YTProject, YTShot
from youtube.projects import ProjectError
from youtube.storage import StoragePort, business_key

logger = __import__("logging").getLogger(__name__)

ASSET_KINDS = ("product", "logo", "music", "location", "screen")
# Assets that can appear in a shot, matched by name in its line or visual.
SCENE_KINDS = ("product", "location")
AUDIO_TYPES = {"mp3": "audio/mpeg", "wav": "audio/wav", "m4a": "audio/mp4", "ogg": "audio/ogg", "flac": "audio/flac"}


def audio_type(data: bytes) -> Optional[str]:
    """The extension of an audio file, from its first bytes, or None if it is not a supported one."""
    head = data[:12]
    if head[:3] == b"ID3" or (len(head) > 1 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0):
        return "mp3"
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "wav"
    if head[4:8] == b"ftyp":
        return "m4a"
    if head[:4] == b"OggS":
        return "ogg"
    if head[:4] == b"fLaC":
        return "flac"
    return None
MAX_ASSETS = 50
END_CARD_DEFAULTS = {"enabled": False, "headline": "", "url": "", "logo_asset_id": None,
                     "background": "#111111", "seconds": 3.0}


# ---------------------------------------------------------------------------
#  Assets
# ---------------------------------------------------------------------------
def list_assets(db: Session, business_id: str, kind: Optional[str] = None) -> list[YTAsset]:
    query = select(YTAsset).where(YTAsset.business_id == business_id)
    if kind:
        query = query.where(YTAsset.kind == kind)
    return db.execute(query.order_by(YTAsset.name)).scalars().all()


def get_asset(db: Session, business_id: str, asset_id: int) -> Optional[YTAsset]:
    return db.execute(select(YTAsset).where(YTAsset.id == asset_id, YTAsset.business_id == business_id)).scalar_one_or_none()


def create_asset(db: Session, business_id: str, name: str, kind: str, data: bytes, storage: StoragePort) -> YTAsset:
    name = re.sub(r"\s+", " ", (name or "")).strip()[:100]
    if not name:
        raise ProjectError("Give the asset a name, as it is called in scripts (e.g. the product name)")
    if kind not in ASSET_KINDS:
        raise ProjectError(f"kind must be one of {', '.join(ASSET_KINDS)}")
    if len(list_assets(db, business_id)) >= MAX_ASSETS:
        raise ProjectError(f"A business can have at most {MAX_ASSETS} assets")
    if db.execute(select(YTAsset.id).where(YTAsset.business_id == business_id, YTAsset.name == name,
                                           YTAsset.kind == kind)).first():
        raise ProjectError(f"A {kind} named {name!r} already exists")
    if kind == "music":
        ext = audio_type(data)
        if ext is None:
            raise ProjectError("Upload music as MP3, WAV, M4A, OGG or FLAC")
        mime = AUDIO_TYPES[ext]
    else:
        try:
            # Logos and screens (a cropped button bar, say) are often wide and short.
            mime, _, _ = inspect_image(data, wide_ok=kind in ("logo", "screen"))
        except ValueError as e:
            raise ProjectError(str(e)) from e
        from youtube.storyboard import EXTENSIONS
        ext = EXTENSIONS.get(mime, "png")
    key = business_key(business_id, "assets", f"{kind}-{uuid.uuid4().hex[:12]}.{ext}")
    storage.put(key, data, content_type=mime)
    asset = YTAsset(business_id=business_id, name=name, kind=kind, storage_key=key)
    db.add(asset)
    db.commit()
    db.refresh(asset)
    return asset


def delete_asset(db: Session, asset: YTAsset, storage: Optional[StoragePort]) -> None:
    """Delete an asset and take it out of every project, shot and end card that used it."""
    for project in db.execute(select(YTProject).where(YTProject.business_id == asset.business_id)).scalars().all():
        changed = False
        if asset.id in (project.asset_ids or []):
            project.asset_ids = [i for i in project.asset_ids if i != asset.id]
            changed = True
        for shot in projects._shots(db, project):
            if shot.asset_ids and asset.id in shot.asset_ids:
                shot.asset_ids = [i for i in shot.asset_ids if i != asset.id]
                changed = True
            if shot.screen_asset_id == asset.id:
                _clear_screen(shot, storage)
                changed = True
        audio = project.audio or {}
        if audio.get("music_asset_id") == asset.id:
            project.audio = {**audio, "music_asset_id": None}
            projects.settle_status(db, project)
        card = project.end_card or {}
        if card.get("logo_asset_id") == asset.id:
            project.end_card = {**card, "logo_asset_id": None}
        if changed:
            projects.clear_approval(project)
            projects.settle_status(db, project)
        elif card.get("logo_asset_id") == asset.id:
            projects.settle_status(db, project)
    if storage:
        try:
            storage.delete(asset.storage_key)
        except Exception:
            pass
    db.delete(asset)
    db.commit()


def serialize_asset(asset: YTAsset) -> dict:
    return {"id": asset.id, "name": asset.name, "kind": asset.kind,
            "version": asset.storage_key.rsplit("/", 1)[-1]}


# ---------------------------------------------------------------------------
#  Products in a project and its shots
# ---------------------------------------------------------------------------
def project_products(db: Session, project: YTProject) -> list[YTAsset]:
    """The products and locations this project features, in the order they were added."""
    ids = project.asset_ids or []
    if not ids:
        return []
    assets = {a.id: a for a in list_assets(db, project.business_id) if a.kind in SCENE_KINDS}
    return [assets[i] for i in ids if i in assets]


def _names(text: str, products: list[YTAsset]) -> list[YTAsset]:
    return [p for p in products if re.search(r"(?<!\w)" + re.escape(p.name) + r"(?!\w)", text or "", re.IGNORECASE)]


def shot_products(db: Session, project: YTProject, shot: YTShot,
                  products: Optional[list[YTAsset]] = None) -> list[YTAsset]:
    """Products this shot shows: the shot's own choice if a person made one, else those its line or visual names."""
    products = project_products(db, project) if products is None else products
    if shot.asset_ids is not None:
        return [p for p in products if p.id in shot.asset_ids]
    return _names(f"{shot.text}\n{shot.visual_prompt or ''}", products)


def set_project_products(db: Session, project: YTProject, asset_ids: list[int]) -> YTProject:
    projects._require_not_busy(project)
    valid = {a.id for a in list_assets(db, project.business_id) if a.kind in SCENE_KINDS}
    unknown = [i for i in asset_ids if i not in valid]
    if unknown:
        raise ProjectError("Only this business's product and location assets can be used")
    ids = list(dict.fromkeys(asset_ids))
    if ids != (project.asset_ids or []):
        project.asset_ids = ids or None
        projects.clear_approval(project)
        projects.settle_status(db, project)
    db.commit()
    db.refresh(project)
    return project


def set_shot_products(db: Session, project: YTProject, shot_id: int, asset_ids: Optional[list[int]]) -> YTShot:
    """Pick the products a shot shows, or None to go back to matching by name."""
    projects._require_not_busy(project)
    shot = db.execute(select(YTShot).where(YTShot.id == shot_id, YTShot.project_id == project.id)).scalar_one_or_none()
    if shot is None:
        raise ProjectError("No such shot in this project")
    if asset_ids is not None:
        allowed = set(project.asset_ids or [])
        if any(i not in allowed for i in asset_ids):
            raise ProjectError("Add the product to the project first")
        asset_ids = list(dict.fromkeys(asset_ids))
    if asset_ids != shot.asset_ids:
        shot.asset_ids = asset_ids
        projects.clear_approval(project)
        projects.settle_status(db, project)
    db.commit()
    db.refresh(shot)
    return shot


# ---------------------------------------------------------------------------
#  Screens in shots
# ---------------------------------------------------------------------------
# Where the screenshot sits in the frame, as fractions of its height: clear of the
# burned-in captions below it (see youtube/captions.py), which in a Short sit high
# to stay clear of the Shorts interface.
SCREEN_BOX = {"short": (0.05, 0.64), "long_form": (0.05, 0.80)}
SCREEN_WIDTH = 0.90


def screen_frame(data: bytes, width: int, height: int, video_format: str) -> bytes:
    """A screenshot as one video frame (PNG): whole and unaltered, over a blurred, darkened copy of itself.

    It is scaled to fit, never cropped, so no part of the interface is lost.
    """
    from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

    with Image.open(io.BytesIO(flatten_on_white(data))) as source:
        screen = source.convert("RGB")
    scale = max(width / screen.width, height / screen.height)
    ground = screen.resize((max(int(screen.width * scale), width), max(int(screen.height * scale), height)))
    left, top = (ground.width - width) // 2, (ground.height - height) // 2
    ground = ground.crop((left, top, left + width, top + height))
    ground = ImageEnhance.Brightness(ground.filter(ImageFilter.GaussianBlur(min(width, height) * 0.04))).enhance(0.4)

    box_top, box_bottom = SCREEN_BOX.get(video_format, SCREEN_BOX["long_form"])
    box_w, box_h = int(width * SCREEN_WIDTH), int(height * (box_bottom - box_top))
    fit = min(box_w / screen.width, box_h / screen.height)
    size = (max(int(screen.width * fit), 1), max(int(screen.height * fit), 1))
    screen = screen.resize(size, Image.LANCZOS)
    x, y = (width - size[0]) // 2, int(height * box_top) + (box_h - size[1]) // 2

    radius = int(min(size) * 0.035)
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius, fill=255)
    offset = max(int(min(width, height) * 0.01), 2)
    shadow = Image.new("L", (width, height), 0)
    shadow.paste(mask, (x, y + offset))
    ground.paste((0, 0, 0), (0, 0, width, height), shadow.filter(ImageFilter.GaussianBlur(offset * 2)).point(lambda v: int(v * 0.6)))
    ground.paste(screen, (x, y), mask)

    out = io.BytesIO()
    ground.save(out, format="PNG")
    return out.getvalue()


def draw_screen_shot(db: Session, project: YTProject, shot: YTShot, storage: StoragePort):
    """Make a screen shot's frame from its screenshot. Costs nothing and involves no model."""
    from youtube import storyboard
    from youtube.images import GeneratedImage
    from youtube.media import SIZES

    screen = get_asset(db, project.business_id, shot.screen_asset_id)
    if screen is None or screen.kind != "screen":
        raise ProjectError(f"Shot {shot.position} shows a screen that no longer exists; choose another")
    width, height = SIZES[project.format]
    data = screen_frame(storage.get(screen.storage_key), width, height, project.format)
    key = storyboard._store_image(storage, (project.business_id, "projects", str(project.id), "images",
                                            f"shot-{shot.position:04d}"), data, "image/png")
    storyboard._delete(storage, shot.image_key)
    shot.image_key, shot.image_error, shot.image_issues = key, None, None
    projects.clear_clip(shot)
    return GeneratedImage(data, "image/png", width, height)


def _clear_screen(shot: YTShot, storage: Optional[StoragePort]) -> None:
    """Stop showing a screen. Its frame goes too: the shot now needs a drawn scene."""
    from youtube import storyboard

    shot.screen_asset_id = None
    if shot.image_key:
        storyboard._delete(storage, shot.image_key)
        shot.image_key = None
    projects.clear_clip(shot)


def set_shot_screen(db: Session, project: YTProject, shot_id: int, asset_id: Optional[int],
                    storage: StoragePort) -> YTShot:
    """Show a screen in a shot, framed at once, or None to go back to a drawn scene."""
    projects._require_not_busy(project)
    shot = db.execute(select(YTShot).where(YTShot.id == shot_id, YTShot.project_id == project.id)).scalar_one_or_none()
    if shot is None:
        raise ProjectError("No such shot in this project")
    if asset_id is not None:
        screen = get_asset(db, project.business_id, asset_id)
        if screen is None or screen.kind != "screen":
            raise ProjectError("Choose one of this business's screen assets")
    if asset_id == shot.screen_asset_id:
        return shot
    if asset_id is None:
        _clear_screen(shot, storage)
    else:
        shot.screen_asset_id = asset_id
        draw_screen_shot(db, project, shot, storage)
    projects.clear_approval(project)
    projects.settle_status(db, project)
    db.commit()
    db.refresh(shot)
    return shot


LOCATION_PROMPT = ("A photograph of this place, empty of people, for use as a location reference in a video: "
                   "{description}\nShow the whole space clearly in even, natural light. "
                   "No text, no logos, no watermarks.")


def generate_asset_image(db: Session, business_id: str, name: str, kind: str, description: str,
                         storage: StoragePort, port=None) -> YTAsset:
    """Draw an asset (a location, usually) from a description instead of uploading a photo."""
    from youtube.images import ImageRegistry

    description = " ".join((description or "").split())[:1000]
    if not description:
        raise ProjectError("Describe the place to draw")
    port = port or ImageRegistry.get()
    prompt = LOCATION_PROMPT.format(description=description) if kind == "location" else description
    try:
        image = port.generate(prompt, [], "16:9")
    except Exception as e:
        raise ProjectError(f"Drawing {name!r} failed: {e}") from e
    asset = create_asset(db, business_id, name, kind, image.data, storage)
    from youtube.models import YTCost
    logger.info("Generated %s asset %r with %s", kind, name, port.name)
    return asset


def flatten_on_white(data: bytes) -> bytes:
    """Transparent PNGs (typical for logos and cut-out products) lose their shape as JPEG; put them on white."""
    from PIL import Image

    with Image.open(io.BytesIO(data)) as image:
        if image.mode not in ("RGBA", "LA", "P") or (image.mode == "P" and "transparency" not in image.info):
            return data
        rgba = image.convert("RGBA")
        ground = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        ground.alpha_composite(rgba)
        out = io.BytesIO()
        ground.convert("RGB").save(out, format="PNG")
        return out.getvalue()


# ---------------------------------------------------------------------------
#  End card
# ---------------------------------------------------------------------------
def normalize_end_card(db: Session, business_id: str, data: dict) -> dict:
    card = {**END_CARD_DEFAULTS, **{k: v for k, v in (data or {}).items() if k in END_CARD_DEFAULTS}}
    card["enabled"] = bool(card["enabled"])
    card["headline"] = re.sub(r"\s+", " ", str(card["headline"] or "")).strip()[:60]
    card["url"] = str(card["url"] or "").strip()[:80]
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", str(card["background"] or "")):
        raise ProjectError("The background must be a colour like #1A2B3C")
    try:
        card["seconds"] = min(max(float(card["seconds"]), 2.0), 6.0)
    except (TypeError, ValueError) as e:
        raise ProjectError("The end card length must be a number of seconds") from e
    if card["logo_asset_id"] is not None:
        logo = get_asset(db, business_id, int(card["logo_asset_id"]))
        if logo is None or logo.kind != "logo":
            raise ProjectError("Choose one of this business's logo assets")
        card["logo_asset_id"] = logo.id
    if card["enabled"] and not (card["headline"] or card["url"] or card["logo_asset_id"]):
        raise ProjectError("An end card needs a logo, a call to action or a URL")
    return card


def active_end_card(project: YTProject) -> Optional[dict]:
    card = project.end_card or {}
    return card if card.get("enabled") else None


def set_end_card(db: Session, project: YTProject, data: dict) -> YTProject:
    projects._require_not_busy(project)
    project.end_card = normalize_end_card(db, project.business_id, data)
    projects.settle_status(db, project)  # a different end card makes the last render out of date
    db.commit()
    db.refresh(project)
    return project


def _font(size: int, bold: bool = True):
    from PIL import ImageFont

    for name in (("DejaVuSans-Bold.ttf", "arialbd.ttf") if bold else ("DejaVuSans.ttf", "arial.ttf")):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def _wrap(draw, text: str, font, max_width: int) -> list[str]:
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if draw.textlength(trial, font=font) <= max_width or not line:
            line = trial
        else:
            lines.append(line)
            line = word
    return lines + ([line] if line else [])


def end_card_image(card: dict, logo: Optional[bytes], width: int, height: int) -> bytes:
    """The end card as a PNG: logo, call to action and URL centred on the brand colour."""
    from PIL import Image, ImageDraw

    background = tuple(int(card["background"][i:i + 2], 16) for i in (1, 3, 5))
    luminance = 0.2126 * background[0] + 0.7152 * background[1] + 0.0722 * background[2]
    ink = (17, 17, 17) if luminance > 150 else (255, 255, 255)
    image = Image.new("RGB", (width, height), background)
    draw = ImageDraw.Draw(image)
    short_side = min(width, height)
    y = int(height * (0.30 if height > width else 0.22))

    if logo:
        with Image.open(io.BytesIO(logo)) as mark:
            mark = mark.convert("RGBA")
            mark.thumbnail((int(width * 0.55), int(height * (0.22 if height > width else 0.30))))
            image.paste(mark, ((width - mark.width) // 2, y), mark)
            y += mark.height + int(short_side * 0.08)
    else:
        y = int(height * 0.38)

    if card.get("headline"):
        font = _font(int(short_side * 0.085))
        for line in _wrap(draw, card["headline"], font, int(width * 0.84)):
            w = draw.textlength(line, font=font)
            draw.text(((width - w) / 2, y), line, font=font, fill=ink)
            y += int(font.size * 1.2)
        y += int(short_side * 0.03)
    if card.get("url"):
        font = _font(int(short_side * 0.05), bold=False)
        w = draw.textlength(card["url"], font=font)
        draw.text(((width - w) / 2, y), card["url"], font=font, fill=ink)

    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def render_end_card(db: Session, project: YTProject, storage: StoragePort, width: int, height: int) -> Optional[bytes]:
    card = active_end_card(project)
    if card is None:
        return None
    logo = get_asset(db, project.business_id, card["logo_asset_id"]) if card.get("logo_asset_id") else None
    return end_card_image(card, storage.get(logo.storage_key) if logo else None, width, height)


def serialize_end_card(project: YTProject) -> dict:
    return {**END_CARD_DEFAULTS, **(project.end_card or {})}
