import math
from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field, model_validator

from .buba import with_hashtag


class BubaReview(BaseModel):
    profile_link: bool = False
    voice: bool = False
    rights: bool = False
    audience: bool = False
    clean: bool = False
    no_promotion: bool = False


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
    banner_audio: bool = False
    banner_height: float | None = Field(None, ge=5, le=100, allow_inf_nan=False)
    buba_mode: bool = False
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
        if self.buba_mode:
            self.caption = with_hashtag(self.caption)
            if len(self.caption) > 2200:
                raise ValueError("Освободите место в описании для #бубавпн")
        return self

    def effective(self, length: float):
        data = self.model_dump()
        data["start"] = length / 2 if self.timing == "middle" else self.start
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


class JobAction(BaseModel):
    action: Literal["retry", "cancel", "approve", "resume", "reconcile", "test_upload"]
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
