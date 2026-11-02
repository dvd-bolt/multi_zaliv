"""Read the authenticated channel's complete uploads playlist, without creating upload jobs."""

from sqlalchemy import select

from .db import Account, ChannelVideo, Job, Metric, Setting, Variant, now


def owned_service(account):
    from .platforms import youtube_service

    if account.platform != "youtube" or not account.connected or not account.external_id:
        raise ValueError("Сначала подключите YouTube-канал")
    service = youtube_service(account)
    channels = service.channels().list(part="id,contentDetails", mine=True).execute()["items"]
    channel = next((c for c in channels if c["id"] == account.external_id), None)
    if channel is None:
        raise ValueError("Авторизован другой YouTube-канал. Подключите нужный канал заново")
    return service, channel


def video_values(item):
    stats = item.get("statistics", {})
    return {
        key: int(stats[remote]) if remote in stats else None
        for key, remote in (("views", "viewCount"), ("likes", "likeCount"), ("comments", "commentCount"))
    }


def sync(db, account):
    # Fetch every page and every batch before changing the local snapshot. An incomplete
    # response or a quota/auth failure must never turn existing videos into deleted ones.
    service, channel = owned_service(account)
    playlist = channel["contentDetails"]["relatedPlaylists"]["uploads"]
    ids, tokens, token = [], set(), None
    while True:
        params = {"part": "contentDetails", "playlistId": playlist, "maxResults": 50}
        if token:
            params["pageToken"] = token
        page = service.playlistItems().list(**params).execute()
        ids.extend(item["contentDetails"]["videoId"] for item in page["items"])
        token = page.get("nextPageToken")
        if not token:
            break
        if token in tokens:
            raise ValueError("YouTube повторил страницу. Список сохранён без изменений")
        tokens.add(token)
    ids = list(dict.fromkeys(ids))
    videos = {}
    for start in range(0, len(ids), 50):
        page = (
            service.videos()
            .list(part="snippet,status,statistics,contentDetails", id=",".join(ids[start : start + 50]))
            .execute()
        )
        for item in page["items"]:
            if item["snippet"]["channelId"] != account.external_id:
                raise ValueError("YouTube вернул видео другого канала")
            videos[item["id"]] = item
    captured = now()
    linked = {
        j.external_id: j
        for j in db.scalars(
            select(Job).join(Variant).where(Variant.account_id == account.id, Job.external_id.is_not(None))
        ).all()
    }
    old = {
        v.external_id: v
        for v in db.scalars(select(ChannelVideo).where(ChannelVideo.account_id == account.id)).all()
    }
    for video_id, item in videos.items():
        row = old.get(video_id)
        if row is None:
            row = ChannelVideo(account_id=account.id, external_id=video_id, history=[])
            db.add(row)
        snippet, status = item["snippet"], item.get("status", {})
        row.details = {
            "title": snippet["title"],
            "published_at": snippet["publishedAt"],
            "visibility": status.get("privacyStatus"),
            "upload_status": status.get("uploadStatus"),
            "thumbnails": snippet.get("thumbnails", {}),
            "values": video_values(item),
        }
        row.present, row.updated = True, captured
        row.history = (row.history + [{"captured": captured, "values": row.details["values"]}])[-48:]
        if video_id in linked:
            db.add(
                Metric(
                    job_id=linked[video_id].id,
                    captured=captured,
                    values=row.details["values"],
                    source="YouTube Data API",
                )
            )
    for video_id, row in old.items():
        if video_id not in videos:
            row.present, row.updated = False, captured
    for video_id, job in linked.items():
        if video_id not in videos and video_id not in old:
            variant = db.get(Variant, job.variant_id)
            db.add(
                ChannelVideo(
                    account_id=account.id,
                    external_id=video_id,
                    present=False,
                    updated=captured,
                    history=[],
                    details={
                        "title": variant.config.get("title") or video_id,
                        "published_at": job.upload_started_at or job.created,
                        "visibility": variant.config.get("visibility"),
                        "values": {},
                    },
                )
            )
    key = "youtube_sync_" + account.id
    state = {"captured": captured, "count": len(videos), "error": None}
    store = db.get(Setting, key)
    if store:
        store.value = state
    else:
        db.add(Setting(key=key, value=state))
    return len(videos)


def library(db, account):
    linked = {
        j.external_id: j.id
        for j in db.scalars(
            select(Job).join(Variant).where(Variant.account_id == account.id, Job.external_id.is_not(None))
        ).all()
    }
    store = db.get(Setting, "youtube_sync_" + account.id)
    return {
        "sync": store.value if store else None,
        "videos": [
            {
                "external_id": v.external_id,
                "present": v.present,
                "updated": v.updated,
                "job_id": linked.get(v.external_id),
                "url": "https://www.youtube.com/watch?v=" + v.external_id,
                **v.details,
            }
            for v in db.scalars(select(ChannelVideo).where(ChannelVideo.account_id == account.id)).all()
        ],
    }


def external_statistics(db):
    """Project library rows to statistics only; they never enter the publishing queue."""
    from .db import public_account

    jobs, metrics, missing = [], [], []
    for account in db.scalars(select(Account).where(Account.platform == "youtube")).all():
        linked = {
            j.external_id: j.id
            for j in db.scalars(
                select(Job)
                .join(Variant)
                .where(Variant.account_id == account.id, Job.external_id.is_not(None))
            ).all()
        }
        for row in db.scalars(select(ChannelVideo).where(ChannelVideo.account_id == account.id)).all():
            if row.external_id in linked:
                if not row.present:
                    missing.append(linked[row.external_id])
                continue
            if not row.present:
                continue
            key = f"channel:{account.id}:{row.external_id}"
            jobs.append(
                {
                    "id": key,
                    "external_id": row.external_id,
                    "account": public_account(account),
                    "asset_name": row.details["title"],
                    "asset_id": "",
                    "status": "published",
                    "config": {"title": row.details["title"], "visibility": row.details.get("visibility")},
                    "updated": row.updated,
                    "created": row.details["published_at"],
                    "upload_started_at": row.details["published_at"],
                    "url": "https://www.youtube.com/watch?v=" + row.external_id,
                    "has_output": False,
                    "external_only": True,
                }
            )
            metrics.extend(
                {"id": key + ":" + m["captured"], "job_id": key, "source": "YouTube Data API", **m}
                for m in row.history
            )
    return {"jobs": jobs, "metrics": metrics, "missing": missing}
