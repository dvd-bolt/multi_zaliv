"""Local speech timestamps. Text never receives invented evenly spaced timings."""

import re
from difflib import SequenceMatcher
from functools import lru_cache

import numpy as np

from .config import DATA, ROOT, binary
from .media import command


@lru_cache(maxsize=1)
def model():
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise ValueError("Установите зависимости субтитров через scripts/setup.ps1") from None
    local = DATA / "tools" / "whisper-base"
    if not (local / "model.bin").is_file():
        local = ROOT / "resources" / "models" / "whisper-base"
    selected = str(local) if (local / "model.bin").is_file() else "base"
    return WhisperModel(selected, device="cpu", compute_type="int8", cpu_threads=4,
                        download_root=str(DATA / "tools" / "whisper"))


def align_reference(words, reference):
    """Anchor known phrases to observed word times; decline when texts differ too much."""
    def normalize(s):
        return re.sub(r"[^\w]", "", s.casefold().replace("ё", "е"))
    heard, owners = "", []
    for i, word in enumerate(words):
        token = normalize(word.word)
        heard += token
        owners.extend([i] * len(token))
    chunks, chunk, planned, ranges = [], [], "", []
    for token in reference.split():
        chunk.append(token)
        if len(" ".join(chunk)) >= 32 or re.search(r"[.!?]$", token):
            chunks.append(" ".join(chunk))
            chunk = []
    if chunk:
        chunks.append(" ".join(chunk))
    for text in chunks:
        begin = len(planned)
        planned += normalize(text)
        ranges.append((begin, len(planned), text))
    matcher = SequenceMatcher(None, planned, heard, autojunk=False)
    if not planned or not heard or matcher.ratio() < .72:
        return None
    mapping = {}
    for block in matcher.get_matching_blocks():
        mapping.update({block.a+i: block.b+i for i in range(block.size)})
    result = []
    for begin, end, text in ranges:
        indices = [owners[mapping[i]] for i in range(begin, end) if i in mapping]
        if len(indices) < max(1, (end-begin)*.6):
            return None
        start, finish = words[min(indices)].start, words[max(indices)].end
        if result and start < result[-1]["end"]:
            start = result[-1]["end"]
        if finish <= start:
            if not result:
                return None
            result[-1]["text"] += " " + text
        else:
            result.append({"start": round(start, 3), "end": round(finish, 3), "text": text})
    for cue in result:
        heard_words = [w for w in words if w.start >= cue["start"] - .05 and w.end <= cue["end"] + .05]
        cue["words"] = word_times(cue, heard_words)
    return result


def word_times(cue, heard):
    """Times of the cue's own words: the heard words when they match one-to-one, else split by letters."""
    tokens = cue["text"].split()
    if heard and len(heard) == len(tokens):
        return [{"start": round(w.start, 3), "end": round(w.end, 3), "text": t} for w, t in zip(heard, tokens, strict=True)]
    return split_by_letters(cue["start"], cue["end"], tokens)


def split_by_letters(start, end, tokens):
    total = sum(len(t) for t in tokens) or 1
    out, at = [], start
    for t in tokens:
        span = (end - start) * len(t) / total
        out.append({"start": round(at, 3), "end": round(at + span, 3), "text": t})
        at += span
    return out


def transcribe(path, progress, cancelled, reference=""):
    # Decode with the bundled FFmpeg: PyAV 19 removed metadata_errors used by
    # faster-whisper's path decoder. Array input also gives an explicit sample rate.
    pcm = command([binary("ffmpeg"), "-v", "error", "-i", str(path), "-ac", "1",
                   "-ar", "16000", "-f", "f32le", "pipe:1"])
    audio = np.frombuffer(pcm, dtype=np.float32).copy()
    segments, _ = model().transcribe(audio, language="ru", word_timestamps=True,
                                    vad_filter=True, condition_on_previous_text=False)
    cues, words, all_words = [], [], []
    for segment in segments:
        if cancelled():
            raise InterruptedError("Обработка отменена")
        for word in segment.words or []:
            all_words.append(word)
            words.append(word)
            text = " ".join(w.word.strip() for w in words)
            if len(text) >= 32 or word.end - words[0].start >= 2.5 or re.search(r"[.!?]$", text):
                cues.append({"start": round(words[0].start, 3), "end": round(word.end, 3), "text": text,
                             "words": [{"start": round(w.start, 3), "end": round(w.end, 3), "text": w.word.strip()}
                                       for w in words]})
                words = []
        progress(segment.end)
    if words:
        cues.append({"start": round(words[0].start, 3), "end": round(words[-1].end, 3),
                     "text": " ".join(w.word.strip() for w in words),
                     "words": [{"start": round(w.start, 3), "end": round(w.end, 3), "text": w.word.strip()}
                               for w in words]})
    if not cues:
        raise ValueError("Не удалось распознать речь; загрузите или исправьте SRT вручную")
    return align_reference(all_words, reference) or cues if reference.strip() else cues


def stamp(seconds, ass=False):
    ms = max(0, round(seconds * (100 if ass else 1000)))
    unit = 100 if ass else 1000
    h, rem = divmod(ms, 3600 * unit)
    m, rem = divmod(rem, 60 * unit)
    s, frac = divmod(rem, unit)
    return f"{h}:{m:02d}:{s:02d}.{frac:02d}" if ass else f"{h:02d}:{m:02d}:{s:02d},{frac:03d}"


def srt(cues):
    return "\n\n".join(f"{i}\n{stamp(c['start'])} --> {stamp(c['end'])}\n{c['text']}"
                       for i, c in enumerate(cues, 1)) + "\n"


def parse_srt(value, length):
    cues = []
    for block in re.split(r"\n\s*\n", value.replace("\r", "").strip()):
        lines = block.splitlines()
        if len(lines) < 3:
            raise ValueError("SRT: нужны номер, таймкод и текст каждого блока")
        match = re.fullmatch(r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3}) --> (\d{2}):(\d{2}):(\d{2})[,.](\d{3})", lines[1])
        if not match:
            raise ValueError("SRT: неверный таймкод")
        n = list(map(int, match.groups()))
        begin, end = (n[i] * 3600 + n[i+1] * 60 + n[i+2] + n[i+3] / 1000 for i in (0, 4))
        text = " ".join(lines[2:]).strip()
        if not 0 <= begin < end <= length + .05 or not text or len(text) > 180:
            raise ValueError("SRT: проверьте длину текста и границы аудио")
        if cues and begin < cues[-1]["end"]:
            raise ValueError("SRT: блоки пересекаются или нарушен порядок")
        cues.append({"start": begin, "end": end, "text": text})
    return cues


HIGHLIGHT = "&H0000E5FF&"  # warm yellow (ASS colours are BGR)


def clean(text):
    return re.sub(r"[{}\\\r\n]", " ", text)


def groups(cue, size=3):
    """Words of a cue in groups of up to `size`, each word with its own time."""
    words = cue.get("words")
    tokens = cue["text"].split()
    if not words or [w["text"] for w in words] != tokens:
        words = split_by_letters(cue["start"], cue["end"], tokens)
    return [words[i:i + size] for i in range(0, len(words), size)]


def ass(cues, part_number, total, width, height, style="classic", hook="", next_hint=False, duration=None):
    # ASS control sequences are stripped from user text; no arbitrary override tags.
    big = int(width * .078)
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Speech,Arial,{int(width*.047)},&H00FFFFFF,&H00FFFFFF,&H00202020,&H80000000,-1,0,0,0,100,100,0,0,3,2,0,2,{int(width*.09)},{int(width*.09)},{int(height*.24)},1
Style: Words,Arial,{big},&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,-1,0,0,0,100,100,0,0,1,{max(3, width // 160)},{max(2, width // 360)},2,{int(width*.07)},{int(width*.07)},{int(height*.30)},1
Style: Part,Arial,{int(width*.035)},&H00FFFFFF,&H00FFFFFF,&H00202020,&H80000000,-1,0,0,0,100,100,0,0,3,2,0,8,40,40,{int(height*.12)},1
Style: Hook,Arial,{int(width*.064)},&H00FFFFFF,&H00FFFFFF,&H00000000,&HC0000000,-1,0,0,0,100,100,0,0,3,{max(10, width // 60)},0,8,{int(width*.08)},{int(width*.08)},{int(height*.18)},1
Style: Next,Arial,{int(width*.055)},&H0000E5FF,&H00FFFFFF,&H00000000,&HC0000000,-1,0,0,0,100,100,0,0,3,{max(10, width // 60)},0,5,{int(width*.08)},{int(width*.08)},0,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []
    for c in cues:
        if style != "words":
            events.append(f"Dialogue: 0,{stamp(c['start'], True)},{stamp(c['end'], True)},Speech,,0,0,0,,{clean(c['text'])}")
            continue
        for number, group in enumerate(groups(c)):
            for i, word in enumerate(group):
                begin = c["start"] if number == 0 and i == 0 else word["start"]
                finish = group[i + 1]["start"] if i + 1 < len(group) else word["end"]
                if finish <= begin:
                    continue
                text = " ".join((f"{{\\c{HIGHLIGHT}\\fscx112\\fscy112}}{clean(w['text']).upper()}{{\\r}}" if j == i
                                 else clean(w["text"]).upper()) for j, w in enumerate(group))
                events.append(f"Dialogue: 1,{stamp(begin, True)},{stamp(finish, True)},Words,,0,0,0,,{text}")
    end = duration or (cues[-1]["end"] if cues else 0)
    hook_end = min(2.8, end)
    if cues:
        start_part = hook_end if hook.strip() else 0
        events.append(f"Dialogue: 0,{stamp(start_part, True)},{stamp(cues[-1]['end'], True)},Part,,0,0,0,,"
                      f"Часть {part_number} из {total}")
    if hook.strip():
        events.append(f"Dialogue: 2,0:00:00.00,{stamp(hook_end, True)},Hook,,0,0,0,,{clean(hook.strip())}")
    if next_hint and part_number < total and end > 3:
        events.append(f"Dialogue: 2,{stamp(end - 2.2, True)},{stamp(end, True)},Next,,0,0,0,,"
                      f"Часть {part_number + 1} — уже в профиле")
    return header + "\n".join(events) + "\n"
