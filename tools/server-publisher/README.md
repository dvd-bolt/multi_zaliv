# FrameFlow — серверная очередь публикаций (сериалы → YouTube)

Готовая серия ставится в очередь → в Telegram приходит видео с кнопками «Залить в ЧЧ:ММ / Сейчас / Перенести / Отмена». **Без нажатия ничего не загружается.** Кнопки обрабатывает cloude-bot (`ffp:` → файл в `data/decisions/`), таймер `frameflow-publisher.timer` раз в минуту применяет решения и загружает.

```
P=/opt/frameflow/publisher/publisher.py
python3 $P add "<серия>.mp4" --channel labs|journal --title "…" --description "…" --tags "мультик,пельмень"   [--when best|now|2026-11-10T18:00] [--dry-run]
python3 $P list
python3 $P approve|now|postpone|cancel <id>     # то же, что кнопки
python3 $P auth start journal → ссылка; после входа: python3 $P auth finish journal '<URL из адресной строки>'
```

- Время по умолчанию — лучший час канала (та же формула, что в FrameFlow и сборщике `/opt/frameflow/stats`), не ближе 3 ч к другим роликам канала в очереди. Не нажали до времени — карточка переносится на следующий слот.
- «Залить» заранее → загрузка сразу как приватный ролик с `publishAt` (YouTube опубликует сам). Меньше 30 мин до слота или «Сейчас» → сразу публично.
- Прерванная загрузка → `unknown`, повторов нет (иначе дубль). Тот же файл на тот же канал второй раз не ставится без `--force`.
- Каналы и пути к доступам — `config.json` (600). labs clode может загружать; journal — нужен `auth` с правом загрузки.
- Тесты: `python3 -m unittest test_publisher`. Копия кода для Git — `tools/server-publisher/` в репозитории multi_zaliv.
