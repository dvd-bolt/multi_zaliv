from datetime import UTC, datetime, timedelta

from backend.best_time import hour_table, recommend, views_at

T0 = datetime(2026, 11, 1, 16, 0, tzinfo=UTC)  # 19:00 Moscow


def iso(date):
    return date.isoformat()


def test_views_at_interpolates_around_one_day():
    points = [(iso(T0 + timedelta(hours=20)), 100), (iso(T0 + timedelta(hours=28)), 300)]
    assert views_at(iso(T0), points) == 200


def test_views_at_skips_videos_without_data_near_one_day():
    assert views_at(iso(T0), [(iso(T0 + timedelta(hours=3)), 50)]) is None
    assert views_at(iso(T0), [(iso(T0 + timedelta(hours=60)), 900)]) is None
    assert views_at(iso(T0), [(iso(T0 + timedelta(hours=26)), 900)]) == 900


def test_hour_table_is_relative_to_channel_median_in_moscow_time():
    videos = [
        {"published": iso(T0), "views24": 3000},  # 19:00 MSK
        {"published": iso(T0 + timedelta(days=1)), "views24": 3000},
        {"published": iso(T0 - timedelta(hours=10)), "views24": 1000},  # 09:00 MSK
        {"published": iso(T0 - timedelta(hours=10, days=1)), "views24": 1000},
        {"published": iso(T0 - timedelta(hours=10, days=2)), "views24": None},
    ]
    table = hour_table(videos)
    assert table[19]["n"] == 2 and table[9]["n"] == 2
    assert table[19]["score"] > 1 > table[9]["score"]
    assert table[20]["n"] == 0 and table[20]["weight"] == 1.0  # neighbours count half


def test_recommend_best_explore_and_default():
    videos = [{"published": iso(T0 + timedelta(days=d)), "views24": 3000} for d in range(5)] + [
        {"published": iso(T0 - timedelta(hours=10, days=d)), "views24": 1000} for d in range(5)
    ]
    table = hour_table(videos)
    now = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)  # 12:00 MSK
    best = recommend(table, "youtube", 10, 10, now)
    assert best["mode"] == "best" and best["hour"] == 19 and best["confident"]
    assert best["time"] == "2026-11-10T16:00:00+00:00"
    explore = recommend(table, "youtube", 11, 10, now)
    assert explore["mode"] == "explore" and explore["hour"] in range(8, 24)
    assert explore["hour"] not in (8, 9, 10, 18, 19, 20)
    default = recommend({}, "tiktok", 0, 0, now)
    assert default["mode"] == "default" and default["hour"] == 19


def test_recommend_moves_a_passed_hour_to_tomorrow():
    late = datetime(2026, 11, 10, 15, 50, tzinfo=UTC)  # 18:50 MSK: too late to render for 19:00
    result = recommend({}, "tiktok", 0, 0, late)
    assert result["time"] == "2026-11-11T16:00:00+00:00"


def test_tiny_channels_give_no_hour_table():
    videos = [{"published": iso(T0 + timedelta(days=d)), "views24": 2} for d in range(10)]
    assert hour_table(videos) == {}
