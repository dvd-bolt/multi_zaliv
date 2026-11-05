# Статистика FrameFlow на сервере

> Копия серверных скриптов (живая версия — `/opt/frameflow/stats` на VPS). Для запуска положите рядом `backend/best_time.py` из этого репозитория и создайте `.venv` с `yt-dlp`.

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
