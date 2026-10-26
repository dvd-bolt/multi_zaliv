# Техническое задание: Модернизация пайплайна автозалива контента

Данный документ описывает комплекс программных изменений для устранения теневых блокировок, фризов рекомендаций (0–5 просмотров) и предотвращения дублирующих загрузок на платформах YouTube Shorts и TikTok.

---

## 1. Разделение ответственности сервисов (Архитектура)

### 1.1. Роль YouTube Data API v3
* **Ограничение:** Использовать YouTube Data API **исключительно в режиме чтения** (Read-Only):
  * Периодический поллинг метрик (просмотры, лайки, комментарии).
  * Парсинг статуса публикации.
* **Запрет:** Полностью исключить метод `videos.insert` для публикации коротких видео со свежих/неверифицированных Google Cloud проектов.

### 1.2. Модуль загрузки (Uploader)
* Перевести загрузку видео на автоматизацию браузера с эмуляцией реального пользователя:
  * **Стек:** `Playwright` / `Patchright` / `Camoufox`.
  * **Профили:** Использование постоянной директории данных пользователя (`user_data_dir`) для сохранения сессий, кук и локального хранилища.
  * **Поведение:**
    * Случайные интервалы между действиями (`1.5 – 4.0` сек).
    * Посимвольный ввод заголовков и тегов с эмуляцией опечаток и задержек (`delay=50..120ms`).
    * Загрузка файлов строго через нативное системное событие `setInputFiles()`.

---

## 2. Модуль уникализации медиа (FFmpeg Pipeline)

Каждая выгрузка должна получать физически и спектрально уникальный бинарный файл. Повторная отправка идентичных файлов запрещена.

### 2.1. Требования к препроцессингу
1. **Снятие метаданных сборщика:** Полное удаление меток `Lavf` / `FFmpeg` (`-map_metadata -1`).
2. **Спуфинг мобильного устройства:** Внедрение метаданных мобильной камеры (Apple QuickTime / iPhone).
3. **Рандомизация аудио- и видеоряда:**
   * Нелинейный микромасштаб (Crop/Zoom) в диапазоне `1.01x – 1.03x`.
   * Динамическая коррекция контраста ($\pm 1\%$) и яркости ($\pm 0.5\%$).
   * Наложение слабого шума (пленочное зерно / noise filter).
   * Микромодуляция частоты дискретизации звука ($\pm 0.5–1\%$) для изменения акустического хеша без слышимого искажения тональности.
4. **Рандомизация имени выходного файла:** Именование по паттерну мобильных ОС (`IMG_XXXX.MP4` или `VID_YYYYMMDD_XXXXXX.mp4`).

### 2.2. Пример команды FFmpeg
```bash
ffmpeg -y -i "input.mp4" \
  -map_metadata -1 \
  -vf "scale=iw*1.02:ih*1.02,crop=iw/1.02:ih/1.02,eq=contrast=1.01:brightness=0.005,noise=alls=1:allf=t" \
  -af "asetrate=44100*1.008,aresample=44100,atempo=0.992" \
  -metadata com.apple.quicktime.make="Apple" \
  -metadata com.apple.quicktime.model="iPhone 15 Pro" \
  -metadata:s:v:0 handler_name="Core Media Video" \
  -metadata:s:a:0 handler_name="Core Media Audio" \
  -c:v libx264 -preset slow -crf 19 -pix_fmt yuv420p \
  -c:a aac -b:a 192k \
  "IMG_4921.MP4"
```

---

## 3. Идемпотентность и защита от повторных публикаций

### 3.1. Структура базы данных (Таблица контроля публикаций)
Необходимо внедрить составной уникальный ключ, исключающий запуск двух одинаковых задач:

```sql
CREATE TABLE IF NOT EXISTS upload_tasks (
    id VARCHAR(64) PRIMARY KEY,
    video_source_id VARCHAR(64) NOT NULL,
    platform VARCHAR(32) NOT NULL,       -- 'youtube_shorts', 'tiktok'
    account_id VARCHAR(64) NOT NULL,     -- 'labs_clode', '@labsclode'
    target_publish_time TIMESTAMP NOT NULL,
    status VARCHAR(32) NOT NULL,         -- 'pending', 'processing', 'completed', 'failed'
    video_path VARCHAR(255),
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_platform_account_source UNIQUE (video_source_id, platform, account_id)
);
```

### 3.2. Логика валидатора перед заливом
Перед постановкой задачи в очередь воркер выполняет проверку:
```python
def can_schedule_upload(source_id: str, platform: str, account_id: str) -> bool:
    task = db.query(UploadTask).filter_by(
        video_source_id=source_id,
        platform=platform,
        account_id=account_id
    ).first()
    
    if task and task.status in ["completed", "processing"]:
        return False  # Защита от дублей
    return True
```

---

## 4. Очередь задач и планировщик с джиттером

### 4.1. Разнесение публикаций по времени
* **Лимит:** Не более 1–2 роликов в сутки на один аккаунт.
* **Временное окно:** Публикация только в интервале активного потребления контента целевого ГЕО (например, с 14:00 до 21:00).
* **Случайный джиттер:** Каждое запланированное время публикации $T_{target}$ модифицируется псевдослучайной величиной $\Delta t$:
  $$T_{actual} = T_{target} + \Delta t, \quad \Delta t \in [-25 \text{ мин}, +40 \text{ мин}]$$
* **Разнесение кросспостинга:** Интервал между отправкой одной темы в разные соцсети (например, YouTube $\rightarrow$ TikTok) должен составлять не менее 30–60 минут.

---

## 5. Эталонная реализация генератора копий (Python)

```python
import os
import random
import subprocess
from pathlib import Path


class VideoProcessor:
    @staticmethod
    def randomize_video(input_path: Path, output_dir: Path) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        random_id = random.randint(1000, 9999)
        output_filename = f"IMG_{random_id}.MP4"
        output_path = output_dir / output_filename

        # Случайные коэффициенты модификации
        scale_val = round(random.uniform(1.012, 1.025), 4)
        contrast_val = round(random.uniform(1.005, 1.020), 4)
        brightness_val = round(random.uniform(0.002, 0.008), 4)
        
        # Модификация аудиосемплов
        audio_speed = round(random.uniform(0.992, 1.008), 5)
        inverted_speed = round(1.0 / audio_speed, 5)

        cmd = [
            "ffmpeg", "-y",
            "-i", str(input_path),
            "-map_metadata", "-1",
            "-vf", (
                f"scale=iw*{scale_val}:ih*{scale_val},"
                f"crop=iw/{scale_val}:ih/{scale_val},"
                f"eq=contrast={contrast_val}:brightness={brightness_val},"
                "noise=alls=1:allf=t"
            ),
            "-af", f"asetrate=44100*{audio_speed},aresample=44100,atempo={inverted_speed}",
            "-metadata", 'com.apple.quicktime.make="Apple"',
            "-metadata", 'com.apple.quicktime.model="iPhone 15 Pro"',
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "20",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "192k",
            str(output_path)
        ]

        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg error: {result.stderr.decode('utf-8')}")

        return output_path
```