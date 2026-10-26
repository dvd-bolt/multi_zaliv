# Использованные компоненты и источники

Фактические версии Python-зависимостей: `requirements.lock.txt`. Зависимости UI: `frontend/package-lock.json`. Не копируем приложения целиком: используем библиотеки и собственные адаптеры. Репозитории из исторической концепции не считаются автоматически включёнными в продукт.

| Компонент | Как используется | Исходники / документация |
|---|---|---|
| FFmpeg + ffprobe | Анализ, миниатюры, баннеры, фильтры, MP4 | [FFmpeg](https://github.com/FFmpeg/FFmpeg), [фильтры](https://ffmpeg.org/ffmpeg-filters.html) |
| Gyan Windows essentials | Локальная сборка FFmpeg; архив сверяется по SHA256 | [Сборки и лицензирование](https://www.gyan.dev/ffmpeg/builds/) |
| FastAPI | Локальный HTTP API | [Исходники](https://github.com/fastapi/fastapi) |
| SQLAlchemy | SQLite и модели | [Исходники](https://github.com/sqlalchemy/sqlalchemy) |
| APScheduler 3.11 | Последовательный отдельный worker и обслуживание | [Исходники](https://github.com/agronholm/apscheduler), [руководство 3.x](https://apscheduler.readthedocs.io/en/3.x/userguide.html) |
| google-api-python-client | YouTube Data API v3 | [Исходники](https://github.com/googleapis/google-api-python-client), [загрузка](https://developers.google.com/youtube/data/guides/uploading_a_video) |
| google-auth-oauthlib | OAuth Desktop | [Исходники](https://github.com/googleapis/google-auth-library-python-oauthlib) |
| tiktok-uploader 1.2.0 | Подготовленный непроверенный браузерный адаптер | [Исходники MIT](https://github.com/wkaisertexas/tiktok-uploader) |
| Playwright | Браузерный адаптер и UI-проверки | [Исходники](https://github.com/microsoft/playwright) |
| React / TypeScript / Vite | Рабочая классическая монтажная | [React](https://github.com/facebook/react), [TypeScript](https://github.com/microsoft/TypeScript), [Vite](https://github.com/vitejs/vite) |
| Pillow | Баннеры/миниатюры | [Исходники](https://github.com/python-pillow/Pillow) |
| Lucide | Иконки интерфейса | [Исходники](https://github.com/lucide-icons/lucide) |
| Manrope / IBM Plex Mono | Локальные файлы шрифтов через Fontsource | [Fontsource](https://github.com/fontsource/fontsource) |

## Ограничения внешних сервисов

- YouTube: [videos.insert](https://developers.google.com/youtube/data/docs/videos/insert), [videos.list](https://developers.google.com/youtube/data/docs/videos/list). Проверка Google-проекта и фактическая видимость проверяются отдельно на аккаунте пользователя.
- TikTok: [Display API](https://developers.tiktok.com/doc/display-api-get-started), [video.query](https://developers.tiktok.com/doc/tiktok-api-v2-video-query). Доступ к статистике не появляется от импорта браузерных cookies.
- TikTok Content Posting API имеет отдельные требования; [руководство Direct Post](https://developers.tiktok.com/doc/content-posting-api-get-started) не следует считать гарантированным способом для внутренней сетки аккаунтов.
- DPAPI: [CryptProtectData](https://learn.microsoft.com/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata).

Перед распространением установщика нужно сохранить лицензии всех включённых компонентов и выполнить условия конкретной сборки FFmpeg. Сейчас FFmpeg скачивается отдельно в локальную игнорируемую папку; его бинарники в исходники не включены. Это не завершённый юридический аудит поставки.


## Дополнение: отдельное окно и интерактивный вход

- [pywebview API](https://pywebview.flowrl.com/api/) и [Windows WebView2 renderer](https://pywebview.flowrl.com/guide/web_engine): отдельное окно, иконка, минимальный размер и сохранение профиля интерфейса.
- [Playwright Authentication](https://playwright.dev/python/docs/auth) и [BrowserContext](https://playwright.dev/python/docs/api/class-browsercontext): непостоянный контекст и получение состояния сессии в памяти. В нашем приложении состояние сохраняется только через DPAPI, не через plaintext storage_state.json.
- Страница входа — официальный адрес `https://www.tiktok.com/login`. Собственный профиль определяется по текущей разметке сайта и подтверждается пользователем; это браузерный адаптер, который нужно проверить на реальном аккаунте. Недокументированные HTTP API для определения пользователя не добавлялись.

- [Google: поддерживаемые браузеры и отказ управляемым браузерам](https://support.google.com/accounts/answer/7675428?hl=ru) — основание обработки Google rejection и перехода к другому способу входа TikTok.


26 октября: изучены актуальные исходники https://github.com/wkaisertexas/tiktok-uploader/blob/main/src/tiktok_uploader/upload.py и установленная версия 1.2.0. Независимый адаптер SurvivalFlow использует проверяемые селекторы DOM, не вызывает upstream stealth, _post_video с fallback-кликом или _set_visibility с проглатыванием ошибки. Playwright upload/response: https://playwright.dev/python/docs/input , https://playwright.dev/python/docs/network .

Дополнительно изучен https://github.com/dreammis/social-auto-upload — кандидат для сравнения сценариев TikTok. Его код не встроен и не заявлен проверенным решением нашей ошибки входа; основной адаптер SurvivalFlow независимый.

БУБА: https://mediabuba.ru/pravila (16 сентября 2026, прочитано 26 октября). YouTube comments: https://developers.google.com/youtube/data/guides/implementation/comments , https://developers.google.com/youtube/v3/docs/commentThreads/list . TikTok Research API не использован для коммерческого мониторинга: https://developers.tiktok.com/docs/en/research-api-faq .
