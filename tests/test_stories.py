import base64
import io
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
            assert info['width'] == 720 and abs(info['duration'] - 2) < .3 and info['has_audio']
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
