import math
from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field, model_validator

from .buba import with_hashtag
from .hashtags import with_defaults


class BubaReview(BaseModel):
    profile_link: bool = False
    voice: bool = False
    rights: bool = False
    audience: bool = False
    clean: bool = False
    no_promotion: bool = False


def pause_window(length: float) -> float:
    """How far the ad may move from the exact middle to land between phrases."""
    return min(3.0, max(0.0, length) * 0.05)


def ad_start(length: float, pauses) -> tuple[float, bool]:
    """Start the ad at the point of a speech pause closest to the middle.

    Returns (start, found). Without a pause inside the window the exact middle is kept.
    """
    middle, window, edge = length / 2, pause_window(length), 0.08
    best = None
    for begin, end in pauses or []:
        if end - begin < 2 * edge:
            continue
        point = min(max(middle, begin + edge), end - edge)
        if abs(point - middle) <= window and (best is None or abs(point - middle) < abs(best - middle)):
            best = point
    return (round(best, 3), True) if best is not None else (middle, False)


class Filters(BaseModel):
    brightness: float = Field(0, ge=-15, le=15, allow_inf_nan=False)
    contrast: float = Field(100, ge=80, le=120, allow_inf_nan=False)
    saturation: float = Field(100, ge=70, le=130, allow_inf_nan=False)
    warmth: float = Field(4, ge=-15, le=15, allow_inf_nan=False)
    zoom: float = Field(100, ge=100, le=110, allow_inf_nan=False)
    mirror: bool = False


class Edit(BaseModel):
    overrides: dict[str, Filters] = Field(default_factory=dict)
    banner_text: str = Field("Продолжение в нашем канале", max_length=120)
    banner_id: str | None = None
    timing: Literal["middle", "manual"] = "middle"
    start: float = Field(0, ge=0)
    duration: float = Field(2.5, gt=0, le=120)
    freeze_video: bool = False
    freeze_motion: bool = False
    ad_pause: bool = False
    banner_audio: bool = False
    banner_height: float | None = Field(None, ge=5, le=100, allow_inf_nan=False)
    buba_mode: bool = False
    auto_hashtags: bool = False
    genre: Literal["auto", "horror", "mystery", "comedy", "drama", "adventure", "scifi", "story"] = "auto"
    buba_review: BubaReview = Field(default_factory=BubaReview)
    x: float = Field(50, ge=0, le=100)
    y: float = Field(77, ge=0, le=100)
    width: float = Field(84, ge=10, le=100)
    opacity: float = Field(100, ge=0, le=100)
    brightness: float = Field(0, ge=-15, le=15)
    contrast: float = Field(100, ge=80, le=120)
    saturation: float = Field(100, ge=70, le=130)
    warmth: float = Field(4, ge=-15, le=15)
    zoom: float = Field(100, ge=100, le=110)
    mirror: bool = False
    format: Literal["1080", "720"] = "1080"
    fit: Literal["pad", "crop"] = "pad"
    banner_end: Literal["hold", "loop"] = "hold"
    caption: str = Field("", max_length=2200)
    title: str = Field("", max_length=100)
    visibility: Literal["private", "unlisted", "public"] = "private"
    synthetic: bool = False
    made_for_kids: bool = False
    targets: list[str] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def campaign_caption(self):
        if self.auto_hashtags:
            self.caption = with_defaults(self.caption, self.genre, self.title)
        if self.buba_mode:
            self.caption = with_hashtag(self.caption)
        if len(self.caption) > 2200:
            raise ValueError("Освободите место в описании для автоматических хэштегов")
        return self

    def effective(self, length: float, pauses=None):
        data = self.model_dump()
        data["start"] = length / 2 if self.timing == "middle" else self.start
        if self.timing == "middle" and self.ad_pause:
            data["start"], data["pause_found"] = ad_start(length, pauses)
        if not all(math.isfinite(v) for v in (length, data["start"], self.duration)):
            raise ValueError("Некорректная длительность видео")
        if self.freeze_video and not 0 <= data["start"] < length:
            raise ValueError("Стоп-кадр должен начинаться до конца исходника.")
        if not self.freeze_video and data["start"] + self.duration > length + 0.02:
            raise ValueError("Баннер выходит за конец ролика. Уменьшите время или длительность показа.")
        return data


class AccountCreate(BaseModel):
    platform: Literal["youtube", "tiktok"]
    name: str = Field(min_length=1, max_length=80)


class Enqueue(BaseModel):
    asset_ids: list[str] = Field(min_length=1, max_length=30)
    account_ids: list[str] = Field(min_length=1, max_length=30)
    scheduled: str | None = None
    # Per-channel time (best-time advice); a channel without an entry uses `scheduled`.
    schedules: dict[str, str] = Field(default_factory=dict, max_length=30)
    auto_publish: bool = False
    buba_confirmed: bool = False
    # Author comment posted after publication; {название} is replaced with the video title.
    comment: str | None = Field(None, max_length=500)
    comment_pin: bool = True
    # TikTok: name of a sound from the account's Favorites added quietly under the voice.
    tiktok_sound: str | None = Field(None, max_length=100)


class Preflight(BaseModel):
    account_ids: list[str] = Field(min_length=1, max_length=30)


class JobAction(BaseModel):
    action: Literal["retry", "cancel", "approve", "resume", "reconcile", "test_upload", "reupload"]
    external_id: str | None = Field(None, max_length=120)


class BatchApproval(BaseModel):
    job_ids: list[str] = Field(min_length=1, max_length=300)


class SettingsUpdate(BaseModel):
    buba_required: bool | None = None
    paused: bool | None = None
    encoder: Literal["libx264", "h264_qsv", "h264_nvenc"] | None = None
    quality: Literal["high", "balanced"] | None = None
    timezone: Literal["Europe/Moscow", "UTC"] | None = None
    interval: int | None = Field(None, ge=0, le=180)
    retention_days: int | None = Field(None, ge=1, le=365)
    notifications: bool | None = None
    project_name: str | None = Field(None, min_length=1, max_length=80)
    youtube_category: Literal["1", "24", "23", "22"] | None = None
    one_per_platform: bool | None = None
    youtube_thumbnail: bool | None = None
    tiktok_copyright: Literal["off", "warn", "strict"] | None = None
    thumbnail_model: (
        Literal["gemini-3.1-flash-image", "gemini-2.5-flash-image", "gemini-3-pro-image"] | None
    ) = None


class TikTokConfirmation(BaseModel):
    handle: str = Field(pattern=r"^[A-Za-z0-9_.]{2,24}$")


class BubaCheck(BaseModel):
    asset_id: str
    edit: Edit


class BubaTracking(BaseModel):
    published_at: AwareDatetime
    public_confirmed: bool = False
    state: Literal["tracking", "submitted", "paid", "rejected"] = "tracking"


class CommentReview(BaseModel):
    reviewed: bool


class GeminiKey(BaseModel):
    api_key: str = Field(min_length=15, max_length=300)


class GeminiOptions(BaseModel):
    auto: bool


class GeminiGenerate(BaseModel):
    force: bool = False
