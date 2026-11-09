# Статистика FrameFlow на сервере

Публичные счётчики TikTok/YouTube через yt-dlp, без ключей и ноутбука. Каналы — `config.json`.

- Каждый час: `frameflow-stats.timer` → `collect.py`.
- Вручную: `.venv/bin/python collect.py` (~15 с; если сбор уже идёт — сообщит).
- Результат: `data/latest.md` (таблица, МСК), `data/latest.json`, история `data/YYYY-MM-DD.jsonl` (~1,7 КБ за запуск, хранится 90 дней), `data/videos.json`.
- Комментарии: `data/comments.md` (🆕 — новые за 24 ч) и `data/comments.json`; качаются только у роликов, где изменилось число комментариев.
- Обновить yt-dlp при поломке: `.venv/bin/pip install -U yt-dlp`.
- Новый канал: добавить в `sources` (`{"platform","account","url"}`; YouTube — `https://www.youtube.com/@<handle>/shorts`) и запустить collect.py. Правило пользователя (03.11.2026): каждый подключённый канал добавляется сразу.
- Лучшее время публикации: `data/best_time.md` — пересчитывается каждым `collect.py` (просмотры за первые сутки ÷ медиана канала, по часам МСК). Формула общая с FrameFlow: `best_time.py` — копия `backend/best_time.py` из репозитория multi_zaliv.
- Конкуренты: `competitors.py`, таймер `frameflow-competitors.timer` каждые 3 ч (~35 с). Каналы — `competitors` в `config.json`, поисковые запросы ниши — `niche_queries`. Результат: `data/competitors.md` («Растёт сейчас», медианы каналов, часы публикаций хитов, кандидаты в конкуренты), `data/competitors.json`, история `data/competitors/YYYY-MM-DD.jsonl`.
- Копия скриптов для Git: `tools/server-stats/` в репозитории multi_zaliv (живая версия — здесь).
- Зрители (с 09.11.2026): `audience.py` — голосования по комментариям (`poll add <видео> --question … --option "Лампочку=лампоч"`, `poll show <видео> [--send]`, `words <видео>` — что чаще называют) и сводка `digest [--send]`: вопросы, просьбы, идеи, самые залайканные и идущие голосования. Таймер `frameflow-digest.timer` шлёт сводку в Telegram каждый день в 10:00 МСК. Голос — один на автора (последний), коммент с несколькими вариантами — «спорный», не считается. Тесты: `test_audience.py`.
- Удержание (с 09.11.2026): `retention.py [--send]` — кривые YouTube Analytics (ролики ≥ 100 просмотров и старше 36 ч) → провалы по секундам → сцена/реплика серии из `/opt/video-projects` → `data/retention.md`, раздел «Повторяющиеся провалы». Связь ролик → серия: `data/retention_map.json` + очередь публикаций. Пересчитывается каждый день перед сводкой комментариев.
- Календарь (с 09.11.2026): `calendar_view.py [--send]` — очередь сервера + очередь FrameFlow с ноутбука (поле `upcoming` снимка статистики) + вышедшие ролики. `frameflow-calendar.timer` в 20:00 МСК шлёт напоминание, только если на завтра у канала пусто.
