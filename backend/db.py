from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from .config import DATA


def uid():
    return uuid4().hex


def now():
    return datetime.now(UTC).isoformat()


class Base(DeclarativeBase):
    pass


class Asset(Base):
    __tablename__ = "assets"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    kind: Mapped[str] = mapped_column(String)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    name: Mapped[str] = mapped_column(String)
    path: Mapped[str] = mapped_column(String)
    sha256: Mapped[str] = mapped_column(String, index=True)
    media: Mapped[dict] = mapped_column(JSON, default=dict)
    edit: Mapped[dict] = mapped_column(JSON, default=dict)
    created: Mapped[str] = mapped_column(String, default=now)


class Account(Base):
    __tablename__ = "accounts"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    platform: Mapped[str] = mapped_column(String)
    name: Mapped[str] = mapped_column(String)
    external_id: Mapped[str | None] = mapped_column(String, nullable=True)
    connected: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    method: Mapped[str] = mapped_column(String, default="")
    created: Mapped[str] = mapped_column(String, default=now)


class Variant(Base):
    __tablename__ = "variants"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"))
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    banner_id: Mapped[str | None] = mapped_column(ForeignKey("assets.id"), nullable=True)
    fingerprint: Mapped[str] = mapped_column(String, unique=True)
    config: Mapped[dict] = mapped_column(JSON)
    output: Mapped[str | None] = mapped_column(String, nullable=True)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    variant_id: Mapped[str] = mapped_column(ForeignKey("variants.id"), unique=True)
    status: Mapped[str] = mapped_column(String, default="queued", index=True)
    progress: Mapped[float] = mapped_column(Float, default=0)
    scheduled: Mapped[str | None] = mapped_column(String, nullable=True)
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_publish: Mapped[bool] = mapped_column(Boolean, default=False)
    validation_upload: Mapped[bool] = mapped_column(Boolean, default=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(String, nullable=True)
    external_id: Mapped[str | None] = mapped_column(String, nullable=True)
    url: Mapped[str | None] = mapped_column(String, nullable=True)
    created: Mapped[str] = mapped_column(String, default=now)
    updated: Mapped[str] = mapped_column(String, default=now)
    upload_started_at: Mapped[str | None] = mapped_column(String, nullable=True)
    notified_status: Mapped[str | None] = mapped_column(String, nullable=True)


class Metric(Base):
    __tablename__ = "metrics"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), index=True)
    captured: Mapped[str] = mapped_column(String, default=now)
    values: Mapped[dict] = mapped_column(JSON)
    source: Mapped[str] = mapped_column(String)


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)


class BubaSubmission(Base):
    __tablename__ = "buba_submissions"
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), primary_key=True)
    source_sha: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    published_at: Mapped[str] = mapped_column(String)
    state: Mapped[str] = mapped_column(String)
    updated: Mapped[str] = mapped_column(String, default=now)


class CommentScan(Base):
    __tablename__ = "comment_scans"
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), primary_key=True)
    status: Mapped[str] = mapped_column(String)
    detail: Mapped[str] = mapped_column(String)
    checked: Mapped[str] = mapped_column(String, default=now)


class Comment(Base):
    __tablename__ = "comments"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), index=True)
    remote_id: Mapped[str] = mapped_column(String)
    author: Mapped[str] = mapped_column(String)
    text: Mapped[str] = mapped_column(String)
    reason: Mapped[str] = mapped_column(String)
    reviewed: Mapped[bool] = mapped_column(Boolean, default=False)
    seen: Mapped[str] = mapped_column(String, default=now)


engine = create_engine(f"sqlite:///{DATA / 'survivalflow.sqlite3'}", connect_args={"timeout": 30})


@event.listens_for(engine, "connect")
def sqlite_options(connection, _):
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=30000")


Session = sessionmaker(engine, expire_on_commit=False)
DEFAULTS = {
    "buba_required": True,
    "paused": True,
    "encoder": "libx264",
    "quality": "high",
    "timezone": "Europe/Moscow",
    "interval": 30,
    "retention_days": 30,
    "notifications": True,
    "project_name": "Банерка / Новый пакет",
}


def migrate():
    # Initial migration is additive; subsequent schema changes get a separate numbered migration.
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY)"))
        version = conn.execute(text("SELECT MAX(version) FROM schema_migrations")).scalar() or 0
        if version < 1:
            Base.metadata.create_all(conn)
            conn.execute(text("INSERT INTO schema_migrations VALUES (1)"))
        if version < 2:
            columns = [row[1] for row in conn.execute(text("PRAGMA table_info(assets)"))]
            if "archived" not in columns:
                conn.execute(text("ALTER TABLE assets ADD COLUMN archived BOOLEAN NOT NULL DEFAULT 0"))
            conn.execute(text("INSERT INTO schema_migrations VALUES (2)"))
        if version < 3:
            columns = [row[1] for row in conn.execute(text("PRAGMA table_info(jobs)"))]
            if "upload_started_at" not in columns:
                conn.execute(text("ALTER TABLE jobs ADD COLUMN upload_started_at VARCHAR"))
            conn.execute(text("INSERT INTO schema_migrations VALUES (3)"))
        if version < 4:
            columns = [row[1] for row in conn.execute(text("PRAGMA table_info(jobs)"))]
            if "validation_upload" not in columns:
                conn.execute(text("ALTER TABLE jobs ADD COLUMN validation_upload BOOLEAN NOT NULL DEFAULT 0"))
            conn.execute(text("INSERT INTO schema_migrations VALUES (4)"))
        if version < 5:
            for table in (BubaSubmission.__table__, CommentScan.__table__, Comment.__table__):
                table.create(conn, checkfirst=True)
            conn.execute(text("INSERT INTO schema_migrations VALUES (5)"))
        if version < 6:
            columns = [row[1] for row in conn.execute(text("PRAGMA table_info(jobs)"))]
            if "auto_publish" not in columns:
                conn.execute(text("ALTER TABLE jobs ADD COLUMN auto_publish BOOLEAN NOT NULL DEFAULT 0"))
            conn.execute(text("INSERT INTO schema_migrations VALUES (6)"))
        if version < 7:
            columns = [row[1] for row in conn.execute(text("PRAGMA table_info(jobs)"))]
            if "notified_status" not in columns:
                conn.execute(text("ALTER TABLE jobs ADD COLUMN notified_status VARCHAR"))
            # Existing outcomes are history: do not send a burst of old notifications after the update.
            conn.execute(text("UPDATE jobs SET notified_status = status"))
            conn.execute(text("INSERT INTO schema_migrations VALUES (7)"))
    with Session.begin() as db:
        if not db.get(Setting, "application"):
            db.add(Setting(key="application", value=DEFAULTS.copy()))


def settings(db):
    return {**DEFAULTS, **db.get(Setting, "application").value}


def public_account(a):
    return {
        k: getattr(a, k) for k in ("id", "platform", "name", "external_id", "connected", "enabled", "method")
    }


migrate()
