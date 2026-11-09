import base64
import io
import json
import wave
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from backend import stories, story_captions, story_tts
from backend.app import app
from backend.config import DATA, binary
from backend.db import Asset, Job, Session, Story, StoryTask, uid
from backend.media import command, probe


def wav_bytes(seconds=2):
    out = io.BytesIO()
    with wave.open(out, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(24000)
        f.writeframes(b"\0\0" * int(seconds * 24000))
    return out.getvalue()


@pytest.fixture
def draft():
    client = TestClient(app)
    s = client.post('/api/stories').json()
    yield client, s
    with Session.begin() as db:
        row = db.get(Story, s['id'])
        assets = [p.get('asset_id') for p in row.document['parts']] if row else []
        db.execute(delete(StoryTask).where(StoryTask.story_id == s['id']))
        db.execute(delete(Story).where(Story.id == s['id']))
        db.execute(delete(Asset).where(Asset.id.in_([a for a in assets if a])))


def test_parse_bom_crlf_and_never_speak_metadata():
    text = '\ufeffНазвание: История\r\nЧастей: 2\r\nФон: Мойка машин\r\n[Часть 1]\r\nПривет.\r\n[Часть 2]\r\nФинал.'
    parsed = stories.parse_script(text)
    assert parsed['parts'] == [{'text': 'Привет.'}, {'text': 'Финал.'}]
    assert parsed['categories'] == ['cars']
    for broken in [text.replace('[Часть 2]', '[Часть 3]'), text.replace('Частей: 2', 'Частей: 3'),
                   text.replace('Привет.', ''), text.replace('Мойка машин', 'unknown')]:
        with pytest.raises(ValueError):
            stories.parse_script(broken)


def test_revision_guard_busy_and_queued_cancellation(draft):
    client, s = draft
    s['parts'][0]['text'] = 'Первая часть.'
    saved = client.put('/api/stories/' + s['id'], json=s)
    assert saved.status_code == 200
    assert client.put('/api/stories/' + s['id'], json=s).status_code == 409
    s = saved.json()
    response = client.post(f"/api/stories/{s['id']}/tasks", json={
        'kind': 'voice', 'parts': [s['parts'][0]['id']], 'revision': s['revision'], 'max_requests': 0})
    assert response.status_code == 400  # A paid call requires an explicit allowance.
    task = client.post(f"/api/stories/{s['id']}/tasks", json={
        'kind': 'voice', 'parts': [s['parts'][0]['id']], 'revision': s['revision'], 'max_requests': 1}).json()
    assert client.put('/api/stories/' + s['id'], json=s).status_code == 409
    assert client.post('/api/stories/tasks/' + task['id'] + '/cancel').json()['status'] == 'cancelled'


def test_tts_exact_text_single_call_cache_and_no_secret_error(monkeypatch):
    profile = {**story_tts.DEFAULT_PROFILE, 'style': uid()}
    calls = []
    def post(url, **kwargs):
        calls.append(kwargs)
        return httpx.Response(200, json={'steps': [{'type': 'model_output', 'content': [
            {'type': 'audio', 'mime_type': 'audio/wav', 'data': base64.b64encode(wav_bytes()).decode()}]}]})
    monkeypatch.setattr(story_tts, 'key', lambda: 'secret-not-for-logs')
    monkeypatch.setattr(story_tts.httpx, 'post', post)
    text = 'Ровно этот текст, без заголовков.'
    result = story_tts.generate(text, profile)
    assert result.is_file()
    assert story_tts.generate(text, profile) == result and len(calls) == 1
    assert calls[0]['json']['input'][0]['content'][0]['text'] == text
    assert calls[0]['json']['store'] is False
    def failed(*a, **kw):
        return httpx.Response(429, text='secret-not-for-logs')
    monkeypatch.setattr(story_tts.httpx, 'post', failed)
    with pytest.raises(ValueError, match='HTTP 429') as error:
        story_tts.generate('Другой текст', profile)
    assert 'secret' not in str(error.value)


def test_srt_validation_and_ass_escape():
    cues = story_captions.parse_srt('1\n00:00:00,000 --> 00:00:01,000\nПривет.\n', 2)
    assert cues[0]['text'] == 'Привет.'
    with pytest.raises(ValueError):
        story_captions.parse_srt('1\n00:00:00,000 --> 00:00:03,000\nТекст', 2)
    ass = story_captions.ass([{'start': 0, 'end': 1, 'text': r'{\pos(1,1)}Hello'}], 1, 2, 720, 1280)
    assert r'{\pos' not in ass and 'Часть 1 из 2' in ass


def test_known_script_uses_observed_word_times_without_rewriting():
    from types import SimpleNamespace

    words = [SimpleNamespace(word=w, start=i*.4, end=(i+1)*.4) for i, w in
             enumerate(['Задень', 'до', 'свадьбы', 'я', 'нашёл', 'записку.'])]
    reference = 'За день до свадьбы я нашёл записку.'
    cues = story_captions.align_reference(words, reference)
    assert ' '.join(c['text'] for c in cues) == reference
    assert cues[0]['start'] == 0 and cues[-1]['end'] == 2.4
    assert story_captions.align_reference(words, 'Совершенно другая история о путешествии.') is None


@pytest.mark.parametrize("part_count", [2, 3])
def test_real_local_render_slices_reuse_and_editor_no_publication(draft, tmp_path, part_count):
    client, s = draft
    bg = tmp_path / 'background.mp4'
    command([binary('ffmpeg'), '-v', 'error', '-y', '-f', 'lavfi', '-i',
             'testsrc2=size=90x160:rate=10:duration=5', '-c:v', 'libx264', str(bg)])
    with bg.open('rb') as f:
        response = client.post('/api/stories/files?kind=background&category=cars', files={'file': ('bg.mp4', f, 'video/mp4')})
    assert response.status_code == 200, response.text
    audio = client.post('/api/stories/files?kind=audio', files={'file': ('voice.wav', wav_bytes(part_count*2), 'audio/wav')}).json()
    if part_count == 3:
        s['parts'].append({'id': None, 'text': ''})
    for i, p in enumerate(s['parts']):
        p.update(audio_id=audio['id'], start=i*2, end=i*2+2, text='Импортированная речь')
    s['subtitles'] = False
    s = client.put('/api/stories/' + s['id'], json=s).json()
    task = client.post(f"/api/stories/{s['id']}/tasks", json={
        'kind': 'render', 'parts': [p['id'] for p in s['parts']], 'revision': s['revision']}).json()
    assert stories.process_one()
    with Session() as db:
        assert db.get(StoryTask, task['id']).status == 'succeeded', db.get(StoryTask, task['id']).error
        saved = db.get(Story, s['id'])
        for p in saved.document['parts']:
            info = probe(Path(p['output_path']))
            assert info['width'] == 1080 and abs(info['duration'] - 2) < .3 and info['has_audio']
        first_bg = saved.document['parts'][0]['background']
        assert first_bg['sha256']
        before = len(db.scalars(select(Job)).all())
    url = f"/api/stories/{s['id']}/parts/{s['parts'][0]['id']}/editor"
    a, b = client.post(url).json(), client.post(url).json()
    assert a['id'] == b['id'] and a['kind'] == 'video' and a['edit']['genre'] == 'story'
    with Session() as db:
        assert len(db.scalars(select(Job)).all()) == before
    # Text correction for imported audio retains the source, but invalidates rendered output.
    current = next(x for x in client.get('/api/stories').json()['stories'] if x['id'] == s['id'])
    current['parts'][0]['text'] = 'Уточнённый текст'
    updated = client.put('/api/stories/' + s['id'], json=current).json()
    assert updated['parts'][0]['has_audio'] and not updated['parts'][0]['has_output']


def test_worker_partial_tts_failure_preserves_other_part_and_recovery(draft, monkeypatch):
    client, s = draft
    for p in s['parts']:
        p['text'] = 'Текст ' + uid()
    s = client.put('/api/stories/' + s['id'], json=s).json()
    count = 0
    def generate(*args):
        nonlocal count
        count += 1
        if count == 2:
            raise ValueError('TTS temporarily unavailable')
        path = DATA / (uid() + '.wav')
        path.write_bytes(wav_bytes())
        return path
    monkeypatch.setattr(story_tts, 'generate', generate)
    t = client.post(f"/api/stories/{s['id']}/tasks", json={
        'kind': 'voice', 'parts': [p['id'] for p in s['parts']], 'revision': s['revision'], 'max_requests': 2}).json()
    stories.process_one()
    with Session.begin() as db:
        assert db.get(StoryTask, t['id']).status == 'failed'
        document = db.get(Story, s['id']).document
        assert document['parts'][0]['audio_path'] and not document['parts'][1].get('audio_path')
        db.get(StoryTask, t['id']).status = 'running'
    stories.recover()
    with Session() as db:
        assert db.get(StoryTask, t['id']).status == 'failed'
        assert 'прервана' in db.get(StoryTask, t['id']).error
    assert count == 2  # No automatic paid retry.


def test_word_captions_highlight_each_word_hook_and_next_part():
    cue = {"start": 0.0, "end": 2.0, "text": "Я нашёл {ключ} в подвале",
           "words": [{"start": 0.0, "end": .4, "text": "Я"}, {"start": .4, "end": .9, "text": "нашёл"},
                     {"start": .9, "end": 1.3, "text": "{ключ}"}, {"start": 1.3, "end": 1.5, "text": "в"},
                     {"start": 1.5, "end": 2.0, "text": "подвале"}]}
    ass = story_captions.ass([cue], 1, 2, 1080, 1920, style="words", hook="Не открывай {дверь}",
                             next_hint=True, duration=6.0)
    words = [line for line in ass.splitlines() if ",Words," in line]
    assert len(words) == 5  # one event per word, groups of three
    assert "{\\c&H0000E5FF&\\fscx112\\fscy112}НАШЁЛ{\\r}" in words[1] and "ПОДВАЛЕ" not in words[1]
    assert words[3].endswith("ПОДВАЛЕ") and "{\\c" in words[3]
    assert "{ключ}" not in ass and "{дверь}" not in ass  # user braces never become override tags
    assert ",Hook,,0,0,0,,Не открывай  дверь " in ass
    assert "0:00:03.80,0:00:06.00,Next,,0,0,0,,Часть 2 — уже в профиле" in ass
    last = story_captions.ass([cue], 2, 2, 1080, 1920, style="words", next_hint=True, duration=6.0)
    assert ",Next," not in last.split("[Events]")[1]


def test_reference_words_get_heard_times_or_letter_split():
    heard = [type("W", (), {"start": s, "end": e, "word": w})() for s, e, w in
             ((0, .5, " Привет"), (.5, 1.0, " мир"))]
    cue = {"start": 0, "end": 1.0, "text": "Привет, мир!"}
    assert story_captions.word_times(cue, heard)[1] == {"start": .5, "end": 1.0, "text": "мир!"}
    split = story_captions.word_times({"start": 0, "end": 1.0, "text": "аа бббб"}, heard[:1])
    assert split[0]["end"] == pytest.approx(1 / 3, abs=.01)


def test_story_2_settings_saved_and_old_documents_keep_classic_look(draft):
    client, s = draft
    assert s['width'] == 1080 and s['caption_style'] == 'words' and s['next_hint'] is True
    s.update(hook='Записка под дверью', caption_style='classic', next_hint=False)
    saved = client.put('/api/stories/' + s['id'], json=s).json()
    assert (saved['hook'], saved['caption_style'], saved['next_hint']) == ('Записка под дверью', 'classic', False)
    from backend.stories import public
    old = type('S', (), {'id': 'x', 'title': 't', 'revision': 1, 'updated': '', 'document': {'parts': []}})()
    assert public(old)['caption_style'] == 'classic' and public(old)['next_hint'] is False


def test_gemini_script_draft_follows_the_scenario_contract(draft, monkeypatch):
    from backend import gemini
    client, _ = draft
    body = "Слово " * 150
    answer = {"title": "Записка", "hook": "Не верь фотографиям", "description": "История о записке.",
              "parts": ["[Часть 1] " + body + "Я открыл дверь.", body + "Конец."]}
    calls = []

    def fake(system, parts, progress=None, **kwargs):
        calls.append(parts[0]["text"])
        return json.dumps(answer, ensure_ascii=False), "gemini-test", ["gemini-test"], []

    monkeypatch.setattr(gemini, "request_json", fake)
    r = client.post('/api/stories/script/draft', json={'theme': 'записка перед свадьбой', 'parts': 2,
                                                        'category': 'cars'})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data['hook'] == 'Не верь фотографиям' and data['model'] == 'gemini-test'
    assert '[Часть 1]\nСлово' in data['script'] and 'Фон: Машины' in data['script']
    parsed = client.post('/api/stories/parse', json={'text': data['script']}).json()
    assert len(parsed['parts']) == 2 and parsed['categories'] == ['cars']
    assert 'записка перед свадьбой' in calls[0]
    answer['parts'] = answer['parts'][:1] + ['коротко']
    assert client.post('/api/stories/script/draft', json={'theme': 'тема', 'parts': 2}).status_code >= 400


def test_background_by_link_downloads_into_library(draft, monkeypatch, tmp_path):
    import sys
    import time as _time
    import types

    from backend import story_backgrounds
    client, _ = draft
    sample = tmp_path / 'sample.mp4'
    command([binary('ffmpeg'), '-v', 'error', '-y', '-f', 'lavfi', '-i',
             'testsrc2=size=90x160:rate=10:duration=3', '-c:v', 'libx264', str(sample)])

    class FakeYDL:
        def __init__(self, options):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, download):
            target = self.options['outtmpl'].replace('%(ext)s', 'mp4')
            Path(target).write_bytes(sample.read_bytes())
            return {'title': 'Паркур', 'duration': 3}

    monkeypatch.setitem(sys.modules, 'yt_dlp', types.SimpleNamespace(YoutubeDL=FakeYDL))
    assert client.post('/api/stories/files/link', json={'url': 'ftp://x', 'category': 'cars'}).status_code >= 400
    task = client.post('/api/stories/files/link', json={'url': 'https://youtu.be/abc', 'category': 'balls'}).json()
    for _ in range(100):
        state = next(t for t in story_backgrounds.listing() if t['id'] == task['id'])
        if state['status'] != 'running':
            break
        _time.sleep(.05)
    assert state['status'] == 'done', state
    files = client.get('/api/stories').json()['files']
    added = next(f for f in files if f['id'] == state['file_id'])
    assert added['kind'] == 'background' and added['category'] == 'balls' and added['name'] == 'Паркур.mp4'


def test_trend_ideas_come_from_the_bot_server(draft, monkeypatch):
    import httpx

    from backend import notify
    client, _ = draft
    monkeypatch.setattr(notify, 'connection', lambda: None)
    assert '/frameflow' in client.get('/api/stories/ideas').json().get('detail', '') or \
        client.get('/api/stories/ideas').status_code >= 400
    seen = {}

    def fake_get(url, headers, timeout):
        seen.update(url=url, auth=headers['Authorization'])
        return httpx.Response(200, json={'ideas': [{'title': 'Не оборачивайся', 'channel': 'ВЫЖИВИ',
                                                    'ratio': 1.8, 'views': 50649}]})

    monkeypatch.setattr(notify, 'connection', lambda: {'url': 'https://bot.example/api/ext/frameflow', 'secret': 'k'})
    monkeypatch.setattr(stories.httpx, 'get', fake_get)
    r = client.get('/api/stories/ideas')
    assert r.status_code == 200 and r.json()['ideas'][0]['title'] == 'Не оборачивайся'
    assert seen == {'url': 'https://bot.example/api/ext/frameflow/ideas', 'auth': 'Bearer k'}


def test_voice_probe_spends_at_most_one_request_and_reuses_cache(draft, monkeypatch):
    client, s = draft
    calls = []

    def fake_generate(text, profile):
        calls.append(text)
        path = story_tts.cache_file(text, profile)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(wav_bytes(1))
        return path

    monkeypatch.setattr(story_tts, 'generate', fake_generate)
    profile = {**s['profile'], 'voice': 'Kore'}
    assert client.post('/api/stories/voice/probe', json=profile).json()['exists'] is False
    assert calls == []  # only checking never spends a request
    made = client.post('/api/stories/voice/probe?generate=true', json=profile).json()
    again = client.post('/api/stories/voice/probe?generate=true', json=profile).json()
    assert made['exists'] and again['key'] == made['key'] and len(calls) == 1
    audio = client.get('/api/stories/voice/probe/' + made['key'])
    assert audio.status_code == 200 and audio.content.startswith(b'RIFF')
    assert client.get('/api/stories/voice/probe/../../x').status_code == 404


@pytest.mark.parametrize("matching", [True, False])
def test_one_button_pipeline_voices_captions_and_renders(draft, monkeypatch, tmp_path, matching):
    client, s = draft
    bg = tmp_path / 'bg.mp4'
    command([binary('ffmpeg'), '-v', 'error', '-y', '-f', 'lavfi', '-i',
             'testsrc2=size=90x160:rate=10:duration=6', '-c:v', 'libx264', str(bg)])
    with bg.open('rb') as f:
        assert client.post('/api/stories/files?kind=background&category=cars',
                           files={'file': ('bg.mp4', f, 'video/mp4')}).status_code == 200
    requests = []

    def fake_generate(text, profile):
        requests.append(text)
        path = story_tts.cache_file(text, profile)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(wav_bytes(2))
        return path

    def fake_transcribe(path, progress, cancelled, reference=''):
        text = reference if matching else 'совсем другое'
        return [{'start': 0, 'end': 1.8, 'text': text}]

    monkeypatch.setattr(story_tts, 'generate', fake_generate)
    monkeypatch.setattr(story_captions, 'transcribe', fake_transcribe)
    for i, p in enumerate(s['parts']):
        p['text'] = f'Часть номер {i + 1}. Я открыл дверь {matching}.'
    s['hook'] = 'Дверь'
    s = client.put('/api/stories/' + s['id'], json=s).json()
    body = {'kind': 'pipeline', 'parts': [p['id'] for p in s['parts']], 'revision': s['revision']}
    refused = client.post(f"/api/stories/{s['id']}/tasks", json=body)
    assert refused.status_code >= 400 and 'разрешено: 0' in refused.text  # TTS spending must be allowed
    task = client.post(f"/api/stories/{s['id']}/tasks", json={**body, 'max_requests': 2}).json()
    assert task['new_requests'] == 2
    assert stories.process_one()
    with Session() as db:
        result = db.get(StoryTask, task['id'])
        document = db.get(Story, s['id']).document
    assert len(requests) == (2 if matching else 1)  # a failed part stops the chain before the next request
    if matching:
        assert result.status == 'succeeded', result.error
        for p in document['parts']:
            assert p['captions_reviewed'] and Path(p['output_path']).is_file()
            assert probe(Path(p['output_path']))['width'] == 1080
    else:
        assert result.status == 'failed' and 'разошлись со сценарием' in result.error
        assert document['parts'][0].get('audio_path') and not document['parts'][0].get('output_path')
