import { useEffect, useRef, useState } from 'react';
import { Plus, Upload, Save, Download, Play, RefreshCw } from 'lucide-react';
import { api, type Asset } from './api';

type Profile = { model: string; voice: string; style: string; language: string };
type Cue = { start: number; end: number; text: string };
type Part = {
  id: string;
  text: string;
  audio_id?: string | null;
  start?: number;
  end?: number | null;
  duration?: number;
  has_audio?: boolean;
  has_output?: boolean;
  cues?: Cue[];
  captions_reviewed?: boolean;
  background?: { name: string; start: number; end: number };
};
type Story = {
  id: string;
  title: string;
  revision: number;
  parts: Part[];
  profile: Profile;
  categories: string[];
  loop: boolean;
  subtitles: boolean;
  width: number;
  caption_style?: 'words' | 'classic';
  hook?: string;
  next_hint?: boolean;
  updated: string;
  source_script?: string;
};
type MediaFile = {
  id: string;
  name: string;
  kind: string;
  category: string;
  media: { duration: number };
};
type Task = {
  id: string;
  story_id: string;
  kind: string;
  status: string;
  progress: number;
  detail: string;
  error: string | null;
  updated: string;
};
type Catalog = {
  stories: Story[];
  files: MediaFile[];
  tasks: Task[];
  models: string[];
  voices: string[];
  downloads?: {
    id: string;
    status: string;
    progress: number;
    error: string;
    name?: string;
    url: string;
  }[];
};
const categories = [
  ['balls', 'Шарики'],
  ['carpets', 'Ковры'],
  ['cars', 'Машины'],
];
const active = (t: Task) => ['queued', 'running', 'cancelling'].includes(t.status);
const time = (seconds: number) => {
  const n = Math.round(seconds * 1000),
    h = Math.floor(n / 3600000),
    m = Math.floor(n / 60000) % 60,
    s = Math.floor(n / 1000) % 60;
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')},${String(n % 1000).padStart(3, '0')}`;
};
const toSrt = (cues: Cue[]) =>
  cues.map((c, i) => `${i + 1}\n${time(c.start)} --> ${time(c.end)}\n${c.text}`).join('\n\n');
const inputPayload = (s: Story) => ({
  title: s.title,
  revision: s.revision,
  profile: s.profile,
  categories: s.categories,
  loop: s.loop,
  subtitles: s.subtitles,
  width: s.width,
  caption_style: s.caption_style ?? 'classic',
  hook: s.hook ?? '',
  next_hint: s.next_hint ?? false,
  source_script: s.source_script ?? '',
  parts: s.parts.map((p) => ({
    id: p.id || null,
    text: p.text,
    audio_id: p.audio_id || null,
    start: p.start || 0,
    end: p.end ?? null,
  })),
});

export default function Stories({
  paused,
  onReady,
  onDirtyChange,
}: {
  paused: boolean;
  onReady: (asset: Asset) => void;
  onDirtyChange: (dirty: boolean) => void;
}) {
  const [catalog, setCatalog] = useState<Catalog | null>(null),
    [story, setStory] = useState<Story | null>(null);
  const [partIndex, setPartIndex] = useState(0),
    [tab, setTab] = useState('script'),
    [dirty, setDirty] = useState(false),
    [busy, setBusy] = useState(false);
  const [error, setError] = useState(''),
    [notice, setNotice] = useState(''),
    [script, setScript] = useState(''),
    [importOpen, setImportOpen] = useState(false),
    [theme, setTheme] = useState(''),
    [themeParts, setThemeParts] = useState(3),
    [draftHook, setDraftHook] = useState(''),
    [link, setLink] = useState(''),
    [ideas, setIdeas] = useState<
      { title: string; channel: string; ratio: number; views: number }[]
    >([]);
  const [scope, setScope] = useState('part'),
    [category, setCategory] = useState('cars'),
    [filePage, setFilePage] = useState(0),
    [storyPage, setStoryPage] = useState(0);
  const [srtText, setSrtText] = useState(''),
    [reviewed, setReviewed] = useState(false),
    [srtDirty, setSrtDirty] = useState(false);
  const [checkedModels, setCheckedModels] = useState<string[] | null>(null);
  const [voiceMode, setVoiceMode] = useState('generate');
  useEffect(() => onDirtyChange(dirty || srtDirty), [dirty, srtDirty, onDirtyChange]);
  useEffect(() => () => onDirtyChange(false), [onDirtyChange]);
  const selectedRef = useRef(''),
    dirtyRef = useRef(false),
    importDialog = useRef<HTMLDialogElement>(null);
  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    setError('');
    setNotice('');
    try {
      await action();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Не удалось выполнить действие');
    } finally {
      setBusy(false);
    }
  };
  const accept = (s: Story) => {
    selectedRef.current = s.id;
    dirtyRef.current = false;
    setStory(s);
    setDirty(false);
  };
  const load = async () => {
    const next = await api<Catalog>('/stories');
    setCatalog(next);
    if (!dirtyRef.current) {
      const s = next.stories.find((s) => s.id === selectedRef.current) ?? next.stories[0];
      if (s) accept(s);
    }
  };
  useEffect(() => {
    let alive = true;
    const controller = new AbortController();
    const update = async () => {
      try {
        const r = await fetch('/api/stories', { signal: controller.signal });
        if (!r.ok) throw new Error('Не удалось загрузить истории');
        const next = (await r.json()) as Catalog;
        if (!alive) return;
        setCatalog(next);
        if (!dirtyRef.current) {
          const s = next.stories.find((s) => s.id === selectedRef.current) ?? next.stories[0];
          if (s) accept(s);
        }
      } catch {
        if (alive) setError('Не удалось обновить истории. Проверьте соединение с программой.');
      }
    };
    void update();
    const timer = setInterval(() => void update(), 2500);
    return () => {
      alive = false;
      controller.abort();
      clearInterval(timer);
    };
  }, []);
  const part = story?.parts[Math.min(partIndex, story.parts.length - 1)];
  useEffect(() => {
    if (!srtDirty) {
      setSrtText(toSrt(part?.cues ?? []));
      setReviewed(part?.captions_reviewed ?? false);
    }
  }, [part?.id, part?.cues, part?.captions_reviewed, srtDirty]);
  useEffect(() => {
    if (importOpen) importDialog.current?.showModal();
    else importDialog.current?.close();
  }, [importOpen]);
  const edit = (patch: Partial<Story>) => {
    if (!story) return;
    dirtyRef.current = true;
    setDirty(true);
    setStory({ ...story, ...patch });
  };
  const editPart = (patch: Partial<Part>) => {
    if (story && part)
      edit({ parts: story.parts.map((p) => (p.id === part.id ? { ...p, ...patch } : p)) });
  };
  const save = async () => {
    if (!story) return null;
    const saved = await api<Story>(`/stories/${story.id}`, 'PUT', inputPayload(story));
    accept(saved);
    return saved;
  };
  const currentTasks = catalog?.tasks.filter((t) => t.story_id === story?.id) ?? [],
    task = currentTasks.find(active),
    lastTask = currentTasks[0];
  const locked = busy || !!task;
  const prefix = story && part ? `/api/stories/${story.id}/parts/${part.id}` : '';
  const start = (kind: string) =>
    void run(async () => {
      const saved = dirty ? await save() : story;
      if (!saved) return;
      const targets =
        scope === 'all' ? saved.parts : [saved.parts[Math.min(partIndex, saved.parts.length - 1)]];
      await api(`/stories/${saved.id}/tasks`, 'POST', {
        kind,
        parts: targets.map((p) => p.id),
        revision: saved.revision,
        max_requests: kind === 'voice' ? targets.length : 0,
      });
      await load();
    });
  const uploadFiles = (files: FileList | null, kind: string) => {
    if (!files) return;
    const selected = [...files];
    void run(async () => {
      for (const file of selected) {
        const form = new FormData();
        form.append('file', file);
        const uploaded = await api<MediaFile>(
          `/stories/files?kind=${kind}&category=${category}`,
          'POST',
          form,
        );
        if (kind === 'audio')
          editPart({ audio_id: uploaded.id, start: 0, end: uploaded.media.duration });
      }
      await load();
      setNotice(`Загружено файлов: ${selected.length}`);
    });
  };
  return (
    <section className="e-content stories-view">
      <div className="e-page-head">
        <div>
          <h1>Истории</h1>
          <p>Сценарий → голос → фон и субтитры → готовые части</p>
        </div>
        <button
          className="e-button primary"
          disabled={busy || dirty || srtDirty}
          onClick={() =>
            void run(async () => {
              const s = await api<Story>('/stories', 'POST');
              accept(s);
              setPartIndex(0);
              setTab('script');
              await load();
            })
          }
        >
          <Plus /> Новая история
        </button>
      </div>
      {(error || notice) && (
        <div className="story-message" role={error ? 'alert' : 'status'}>
          {error || notice}
        </div>
      )}
      <div className="stories-layout">
        <aside className="stories-list">
          <h3>Мои истории</h3>
          {!catalog ? (
            <p className="e-muted">Загрузка…</p>
          ) : !catalog.stories.length ? (
            <p className="e-muted">Создайте историю и вставьте готовый сценарий.</p>
          ) : (
            catalog.stories.slice(storyPage * 5, storyPage * 5 + 5).map((s) => (
              <button
                className={'story-list-item' + (story?.id === s.id ? ' on' : '')}
                key={s.id}
                disabled={dirty || srtDirty || busy}
                onClick={() => {
                  accept(s);
                  setPartIndex(0);
                  setTab('script');
                }}
              >
                <b>{s.title}</b>
                <small>
                  {s.parts.length} части · MP4: {s.parts.filter((p) => p.has_output).length}
                </small>
              </button>
            ))
          )}
          {catalog && catalog.stories.length > 5 && (
            <div className="e-row">
              <button
                className="e-button"
                disabled={!storyPage || dirty}
                onClick={() => setStoryPage((p) => p - 1)}
              >
                Назад
              </button>
              <button
                className="e-button"
                disabled={(storyPage + 1) * 5 >= catalog.stories.length || dirty}
                onClick={() => setStoryPage((p) => p + 1)}
              >
                Далее
              </button>
            </div>
          )}
        </aside>
        {!story ? (
          <div className="story-empty">
            <h2>История из двух или трёх частей</h2>
            <p>Добавьте сценарий или готовую озвучку. Фон выбирается из вашей библиотеки.</p>
          </div>
        ) : (
          <>
            <div className="story-workspace">
              <div className="e-row">
                <input
                  className="e-field"
                  aria-label="Название истории"
                  value={story.title}
                  disabled={locked}
                  onChange={(e) => edit({ title: e.target.value })}
                />
                <button
                  className="e-button"
                  disabled={locked || !dirty}
                  onClick={() =>
                    void run(async () => {
                      await save();
                    })
                  }
                >
                  <Save /> Сохранить
                </button>
              </div>
              <div className="story-partbar">
                <div className="st-tabs small" aria-label="Части истории">
                  {story.parts.map((p, i) => (
                    <button
                      key={p.id || i}
                      className={part?.id === p.id ? 'on' : ''}
                      disabled={srtDirty}
                      onClick={() => setPartIndex(i)}
                    >
                      Часть {i + 1}
                    </button>
                  ))}
                </div>
                <select
                  aria-label="Количество частей"
                  value={story.parts.length}
                  disabled={locked || srtDirty}
                  onChange={(e) => {
                    const n = Number(e.target.value);
                    edit({
                      parts:
                        n === 3 ? [...story.parts, { id: '', text: '' }] : story.parts.slice(0, 2),
                    });
                    setPartIndex(0);
                  }}
                >
                  <option value={2}>2 части</option>
                  <option value={3}>3 части</option>
                </select>
              </div>
              <div className="st-tabs story-tabs" role="tablist" aria-label="Настройки истории">
                {[
                  ['script', 'Сценарий'],
                  ['voice', 'Голос'],
                  ['background', 'Фон'],
                  ['captions', 'Субтитры'],
                  ['release', 'Выпуск'],
                ].map(([v, label]) => (
                  <button
                    key={v}
                    role="tab"
                    aria-selected={tab === v}
                    className={tab === v ? 'on' : ''}
                    disabled={srtDirty && v !== 'captions'}
                    onClick={() => setTab(v)}
                  >
                    {label}
                  </button>
                ))}
              </div>
              <div className="story-panel">
                {tab === 'script' && part && (
                  <>
                    <div className="e-row e-between">
                      <span className="e-muted">Только произносимый текст</span>
                      <button
                        className="e-button"
                        disabled={locked || srtDirty}
                        onClick={() => setImportOpen(true)}
                      >
                        Импорт сценария
                      </button>
                    </div>
                    <textarea
                      className="e-field story-script"
                      aria-label="Текст части"
                      value={part.text}
                      disabled={locked}
                      placeholder="Вставьте текст этой части…"
                      onChange={(e) => editPart({ text: e.target.value })}
                    />
                    <div className="e-muted">
                      {part.text.length} символов · ориентир 60–90 с; точная длина появится после
                      озвучки.
                    </div>
                  </>
                )}
                {tab === 'voice' && part && (
                  <>
                    <div className="st-tabs small" aria-label="Источник озвучки">
                      <button
                        className={voiceMode === 'generate' ? 'on' : ''}
                        onClick={() => setVoiceMode('generate')}
                      >
                        Из сценария
                      </button>
                      <button
                        className={voiceMode === 'import' ? 'on' : ''}
                        onClick={() => setVoiceMode('import')}
                      >
                        Готовая дорожка
                      </button>
                    </div>
                    {voiceMode === 'generate' ? (
                      <>
                        <div className="story-form-grid">
                          <label>
                            Модель
                            <select
                              value={story.profile.model}
                              disabled={locked}
                              onChange={(e) =>
                                edit({ profile: { ...story.profile, model: e.target.value } })
                              }
                            >
                              {catalog?.models.map((m) => (
                                <option key={m}>{m}</option>
                              ))}
                            </select>
                          </label>
                          <label>
                            Голос
                            <select
                              value={story.profile.voice}
                              disabled={locked}
                              onChange={(e) =>
                                edit({ profile: { ...story.profile, voice: e.target.value } })
                              }
                            >
                              {catalog?.voices.map((v) => (
                                <option key={v}>{v}</option>
                              ))}
                            </select>
                          </label>
                        </div>
                        <label>
                          Подача
                          <textarea
                            className="e-field"
                            rows={3}
                            value={story.profile.style}
                            disabled={locked}
                            onChange={(e) =>
                              edit({ profile: { ...story.profile, style: e.target.value } })
                            }
                          />
                        </label>
                        <div className="e-row">
                          <button
                            className="e-button"
                            disabled={locked}
                            onClick={() =>
                              void run(async () => {
                                const r = await api<{ available: string[] }>('/stories/models');
                                setCheckedModels(r.available);
                              })
                            }
                          >
                            Проверить доступ
                          </button>
                          <span className="e-muted">
                            {checkedModels
                              ? checkedModels.length
                                ? 'Доступны: ' + checkedModels.join(', ')
                                : 'Модели TTS недоступны'
                              : 'Используется защищённый ключ Gemini'}
                          </span>
                        </div>
                        <p className="e-muted">
                          Настройки сохраняются для всех частей. Каждый запуск делает до одного
                          нового запроса на часть; готовый кэш используется повторно. После
                          тайм-аута автоматического повтора нет.
                        </p>
                      </>
                    ) : (
                      <>
                        <label>
                          Готовая дорожка
                          <select
                            value={part.audio_id ?? ''}
                            disabled={locked}
                            onChange={(e) => {
                              const f = catalog?.files.find((f) => f.id === e.target.value);
                              editPart({
                                audio_id: f?.id ?? null,
                                start: 0,
                                end: f?.media.duration ?? null,
                              });
                            }}
                          >
                            <option value="">Озвучка из сценария</option>
                            {catalog?.files
                              .filter((f) => f.kind === 'audio')
                              .map((f) => (
                                <option key={f.id} value={f.id}>
                                  {f.name}
                                </option>
                              ))}
                          </select>
                        </label>
                        <div className="e-row">
                          <label className="e-button">
                            <Upload /> Загрузить аудио
                            <input
                              type="file"
                              accept="audio/*,.mp4"
                              disabled={locked}
                              onChange={(e) => {
                                uploadFiles(e.target.files, 'audio');
                                e.target.value = '';
                              }}
                              hidden
                            />
                          </label>
                        </div>
                        {part.audio_id && (
                          <div className="story-form-grid">
                            <label>
                              Начало, с
                              <input
                                aria-label="Начало аудио"
                                type="number"
                                min={0}
                                step="0.01"
                                value={part.start || 0}
                                disabled={locked}
                                onChange={(e) => editPart({ start: Number(e.target.value) })}
                              />
                            </label>
                            <label>
                              Конец, с
                              <input
                                aria-label="Конец аудио"
                                type="number"
                                min={0}
                                step="0.01"
                                value={part.end ?? ''}
                                disabled={locked}
                                onChange={(e) => editPart({ end: Number(e.target.value) })}
                              />
                            </label>
                          </div>
                        )}
                        <p className="e-muted">
                          Для одной длинной дорожки выберите этот файл в каждой части и задайте
                          границы. Сохраните и прослушайте стыки перед сборкой.
                        </p>
                      </>
                    )}
                  </>
                )}
                {tab === 'background' && (
                  <>
                    <div className="e-row">
                      {categories.map(([v, label]) => (
                        <label className="story-check" key={v}>
                          <input
                            type="checkbox"
                            checked={story.categories.includes(v)}
                            disabled={locked}
                            onChange={(e) =>
                              edit({
                                categories: e.target.checked
                                  ? [...story.categories, v]
                                  : story.categories.filter((c) => c !== v),
                              })
                            }
                          />
                          {label}
                        </label>
                      ))}
                    </div>
                    <label className="story-check">
                      <input
                        type="checkbox"
                        checked={story.loop}
                        disabled={locked}
                        onChange={(e) => edit({ loop: e.target.checked })}
                      />
                      Разрешить цикл короткого фона
                    </label>
                    <div className="e-row">
                      <select
                        aria-label="Категория загружаемых фонов"
                        value={category}
                        onChange={(e) => {
                          setCategory(e.target.value);
                          setFilePage(0);
                        }}
                      >
                        {categories.map(([v, label]) => (
                          <option key={v} value={v}>
                            {label}
                          </option>
                        ))}
                      </select>
                      <label className="e-button">
                        <Upload /> Добавить фоны
                        <input
                          type="file"
                          multiple
                          accept="video/*"
                          disabled={locked}
                          hidden
                          onChange={(e) => {
                            uploadFiles(e.target.files, 'background');
                            e.target.value = '';
                          }}
                        />
                      </label>
                    </div>
                    <div className="e-row story-link-row">
                      <input
                        className="e-field"
                        aria-label="Ссылка на видео для фона"
                        placeholder="Ссылка YouTube на фон (паркур, мыло…)"
                        value={link}
                        onChange={(e) => setLink(e.target.value)}
                      />
                      <button
                        className="e-button"
                        disabled={busy || !/^https?:\/\//.test(link.trim())}
                        onClick={() =>
                          void run(async () => {
                            await api('/stories/files/link', 'POST', {
                              url: link.trim(),
                              category,
                            });
                            setLink('');
                            await load();
                            setNotice('Скачиваю фон — он появится в списке этой категории.');
                          })
                        }
                      >
                        Скачать
                      </button>
                    </div>
                    {catalog?.downloads?.[0] && (
                      <small
                        className="e-muted story-download"
                        title={catalog.downloads[0].error || catalog.downloads[0].url}
                      >
                        {catalog.downloads[0].status === 'running'
                          ? `Скачивание: ${catalog.downloads[0].progress}%`
                          : catalog.downloads[0].status === 'done'
                            ? `Скачано: ${catalog.downloads[0].name ?? ''}`
                            : `Не удалось скачать — проверьте ссылку (подробности при наведении)`}
                      </small>
                    )}
                    <div className="story-backgrounds">
                      {(
                        catalog?.files.filter(
                          (f) => f.kind === 'background' && f.category === category,
                        ) ?? []
                      )
                        .slice(filePage * 3, filePage * 3 + 3)
                        .map((f) => (
                          <div key={f.id}>
                            <span>{f.name}</span>
                            <small>{f.media.duration.toFixed(1)} с</small>
                          </div>
                        ))}
                    </div>
                    <div className="e-row">
                      <button
                        className="e-button"
                        disabled={!filePage}
                        onClick={() => setFilePage((p) => p - 1)}
                      >
                        Назад
                      </button>
                      <button
                        className="e-button"
                        disabled={
                          (filePage + 1) * 3 >=
                          (catalog?.files.filter(
                            (f) => f.kind === 'background' && f.category === category,
                          ).length ?? 0)
                        }
                        onClick={() => setFilePage((p) => p + 1)}
                      >
                        Далее
                      </button>
                      <button
                        className="e-button"
                        disabled={locked || dirty || !part?.background}
                        onClick={() =>
                          void run(async () => {
                            accept(
                              await api<Story>(
                                `/stories/${story.id}/parts/${part!.id}/background`,
                                'POST',
                              ),
                            );
                          })
                        }
                      >
                        <RefreshCw /> Другой фрагмент
                      </button>
                    </div>
                    <p className="e-muted">
                      Фрагмент фона случайный и сохраняется, звук фона выключен.
                    </p>
                  </>
                )}
                {tab === 'captions' && part && (
                  <>
                    <div className="e-row">
                      <button
                        className="e-button"
                        disabled={locked || dirty || srtDirty || !part.has_audio}
                        onClick={() => start('captions')}
                      >
                        Распознать локально
                      </button>
                      <a
                        className="e-button"
                        href={`${prefix}/srt`}
                        aria-disabled={!part.cues?.length}
                        onClick={(e) => {
                          if (!part.cues?.length) e.preventDefault();
                        }}
                      >
                        SRT
                      </a>
                      <label className="e-button">
                        Импорт SRT
                        <input
                          type="file"
                          accept=".srt"
                          hidden
                          disabled={locked || dirty}
                          onChange={(e) => {
                            const f = e.target.files?.[0];
                            if (f)
                              void f.text().then((t) => {
                                setSrtText(t);
                                setSrtDirty(true);
                              });
                            e.target.value = '';
                          }}
                        />
                      </label>
                    </div>
                    <textarea
                      className="e-field story-script"
                      aria-label="Редактор субтитров SRT"
                      value={srtText}
                      disabled={locked || dirty}
                      onChange={(e) => {
                        setSrtText(e.target.value);
                        setSrtDirty(true);
                        setReviewed(false);
                      }}
                      placeholder="Создайте субтитры по готовому аудио или загрузите SRT…"
                    />
                    <div className="e-row">
                      <label className="story-check">
                        <input
                          type="checkbox"
                          checked={reviewed}
                          disabled={locked || dirty || !srtText}
                          onChange={(e) => {
                            setReviewed(e.target.checked);
                            setSrtDirty(true);
                          }}
                        />
                        Текст и тайминги проверены
                      </label>
                      <button
                        className="e-button"
                        disabled={locked || dirty || !srtDirty}
                        onClick={() =>
                          void run(async () => {
                            accept(
                              await api<Story>(
                                `/stories/${story.id}/parts/${part.id}/captions`,
                                'PUT',
                                { revision: story.revision, srt: srtText, reviewed },
                              ),
                            );
                            setSrtDirty(false);
                          })
                        }
                      >
                        Сохранить SRT
                      </button>
                      <button
                        className="e-button"
                        disabled={!srtDirty || locked}
                        onClick={() => {
                          setSrtDirty(false);
                          setSrtText(toSrt(part.cues ?? []));
                          setReviewed(part.captions_reviewed ?? false);
                        }}
                      >
                        Отменить правки
                      </button>
                    </div>
                    <p className="e-muted">
                      Распознавание — черновик. Проверьте произношение и границы фраз. Первый запуск
                      загрузит локальную модель.
                    </p>
                  </>
                )}
                {tab === 'release' && part && (
                  <>
                    <div className="story-form-grid">
                      <label>
                        Размер
                        <select
                          value={story.width}
                          disabled={locked}
                          onChange={(e) => edit({ width: Number(e.target.value) })}
                        >
                          <option value={720}>720 × 1280</option>
                          <option value={1080}>1080 × 1920</option>
                        </select>
                      </label>
                      <label>
                        Стиль субтитров
                        <select
                          value={story.caption_style ?? 'classic'}
                          disabled={locked || !story.subtitles}
                          onChange={(e) =>
                            edit({ caption_style: e.target.value as Story['caption_style'] })
                          }
                        >
                          <option value="words">Крупно, по словам с подсветкой</option>
                          <option value="classic">Фразы внизу</option>
                        </select>
                      </label>
                      <label className="story-wide">
                        Заголовок первых секунд
                        <input
                          value={story.hook ?? ''}
                          maxLength={80}
                          disabled={locked}
                          placeholder="Например: Записка под дверью"
                          onChange={(e) => edit({ hook: e.target.value })}
                        />
                      </label>
                      <div className="story-wide story-checks">
                        <label className="story-check">
                          <input
                            type="checkbox"
                            checked={story.subtitles}
                            disabled={locked}
                            onChange={(e) => edit({ subtitles: e.target.checked })}
                          />
                          Субтитры в видео
                        </label>
                        <label className="story-check">
                          <input
                            type="checkbox"
                            checked={story.next_hint ?? false}
                            disabled={locked || story.parts.length < 2}
                            onChange={(e) => edit({ next_hint: e.target.checked })}
                          />
                          В конце: «Часть N — уже в профиле»
                        </label>
                      </div>
                    </div>
                    <p className="e-muted">
                      Часть {partIndex + 1} из {story.parts.length}:{' '}
                      {part.duration ? `${part.duration.toFixed(1)} с` : 'сначала добавьте озвучку'}
                      . Соберите и просмотрите MP4; баннер, каналы и время — в монтажной.
                    </p>
                    <div className="e-row">
                      <a
                        className="e-button"
                        href={`${prefix}/video`}
                        download={`part-${partIndex + 1}.mp4`}
                        onClick={(e) => {
                          if (!part.has_output) e.preventDefault();
                        }}
                      >
                        <Download /> MP4
                      </a>
                      <button
                        className="e-button primary"
                        disabled={locked || dirty || !part.has_output}
                        onClick={() =>
                          void run(async () => {
                            onReady(
                              await api<Asset>(
                                `/stories/${story.id}/parts/${part.id}/editor`,
                                'POST',
                              ),
                            );
                          })
                        }
                      >
                        Передать в монтажную
                      </button>
                    </div>
                  </>
                )}
              </div>
              <div className="story-actions">
                <select
                  aria-label="Область обработки"
                  value={scope}
                  disabled={locked}
                  onChange={(e) => setScope(e.target.value)}
                >
                  <option value="part">Эта часть</option>
                  <option value="all">Все части</option>
                </select>
                <button
                  className="e-button"
                  disabled={locked || srtDirty || !part?.text.trim()}
                  onClick={() => start('voice')}
                >
                  Озвучить · до {scope === 'all' ? story.parts.length : 1} запр.
                </button>
                <button
                  className="e-button primary"
                  disabled={locked || srtDirty || !part?.has_audio}
                  onClick={() => start('render')}
                >
                  <Play /> Собрать MP4
                </button>
              </div>
              <div className="story-status" role="status">
                {paused ? 'Worker на паузе — задания начнутся после «Запустить». ' : ''}
                {task
                  ? `${task.detail} · ${Math.round(task.progress)}%`
                  : lastTask?.error ||
                    (lastTask?.status === 'succeeded'
                      ? 'Готово. Проверьте результат.'
                      : dirty
                        ? 'Есть несохранённые изменения'
                        : 'Черновик сохранён')}
                {task && (
                  <button
                    className="text-link"
                    disabled={busy || task.status === 'cancelling'}
                    onClick={() =>
                      void run(async () => {
                        await api(`/stories/tasks/${task.id}/cancel`, 'POST');
                        await load();
                      })
                    }
                  >
                    Отменить
                  </button>
                )}
              </div>
            </div>
            <aside className="story-preview">
              <h3>Предпросмотр · часть {partIndex + 1}</h3>
              <div className="story-screen">
                {part?.has_output ? (
                  <video
                    key={`${part.id}-${story.updated}`}
                    src={`${prefix}/video?v=${encodeURIComponent(story.updated)}`}
                    controls
                    playsInline
                  />
                ) : part?.background ? (
                  <p className="e-muted">
                    {part.background.name}
                    <br />
                    {part.background.start.toFixed(1)}–{part.background.end.toFixed(1)} с<br />
                    Соберите MP4 для просмотра
                  </p>
                ) : (
                  <p className="e-muted">Здесь появится собранный ролик</p>
                )}
              </div>
              {part?.has_audio && (
                <audio
                  key={`${part.id}-${story.updated}`}
                  src={`${prefix}/audio#t=${part.start || 0},${part.end || part.duration || ''}`}
                  controls
                />
              )}
              <p className="e-muted">
                {part?.has_audio
                  ? `Аудио: ${part.duration?.toFixed(1)} с`
                  : 'Озвучка ещё не добавлена'}
                {part?.cues?.length
                  ? ` · фраз: ${part.cues.length}${part.captions_reviewed ? ' · проверены' : ' · нужен просмотр'}`
                  : ''}
              </p>
            </aside>
          </>
        )}
      </div>
      <dialog
        ref={importDialog}
        className="story-import-dialog"
        onCancel={() => setImportOpen(false)}
      >
        <form onSubmit={(e) => e.preventDefault()}>
          <div className="e-row e-between">
            <h2>Импорт сценария</h2>
            <button className="e-button" onClick={() => setImportOpen(false)}>
              Закрыть
            </button>
          </div>
          <div className="e-row story-theme-row">
            <input
              className="e-field"
              aria-label="Тема для Gemini"
              placeholder="Тема: например, «нашёл чужой телефон в такси»"
              maxLength={300}
              value={theme}
              onChange={(e) => setTheme(e.target.value)}
            />
            <select
              aria-label="Частей"
              value={themeParts}
              onChange={(e) => setThemeParts(Number(e.target.value))}
            >
              <option value={2}>2 части</option>
              <option value={3}>3 части</option>
            </select>
            <button
              className="e-button"
              disabled={busy || theme.trim().length < 3}
              onClick={() =>
                void run(async () => {
                  setNotice('Gemini пишет сценарий… обычно 10–40 секунд');
                  const r = await api<{
                    script: string;
                    hook: string;
                    model: string;
                    words: number[];
                  }>('/stories/script/draft', 'POST', {
                    theme,
                    parts: themeParts,
                    category: story?.categories[0] ?? '',
                  });
                  setScript(r.script);
                  setDraftHook(r.hook);
                  setNotice(
                    `Черновик от ${r.model.replace('gemini-', '')}: ${r.words.join(' / ')} слов. Прочитайте и поправьте, затем «Разобрать».`,
                  );
                })
              }
            >
              Написать через Gemini
            </button>
          </div>
          <div className="e-row story-theme-row">
            <button
              className="e-button"
              disabled={busy}
              onClick={() =>
                void run(async () => {
                  const r = await api<{ ideas: typeof ideas }>('/stories/ideas');
                  setIdeas(r.ideas);
                  setNotice(r.ideas.length ? '' : 'Свежих трендов пока нет');
                })
              }
            >
              Идеи из трендов
            </button>
            {ideas.length > 0 && (
              <select
                className="story-ideas"
                aria-label="Идея из трендов"
                value=""
                onChange={(e) => {
                  const idea = ideas[Number(e.target.value)];
                  if (idea) setTheme(`Своя история по мотивам темы: «${idea.title}»`);
                }}
              >
                <option value="">Выберите тему ({ideas.length})…</option>
                {ideas.map((idea, i) => (
                  <option key={i} value={i}>
                    {idea.ratio.toFixed(1)}× · {idea.channel}: {idea.title}
                  </option>
                ))}
              </select>
            )}
          </div>
          <p>
            Название: … · Частей: 2 или 3 · Фон: Мойка машин
            <br />
            Далее [Часть 1], текст, [Часть 2], текст.
          </p>
          <textarea
            className="e-field"
            rows={12}
            aria-label="Полный сценарий"
            value={script}
            onChange={(e) => setScript(e.target.value)}
          />
          <div className="e-row">
            <label className="e-button">
              Открыть TXT
              <input
                type="file"
                accept=".txt,.md"
                hidden
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) void f.text().then(setScript);
                  e.target.value = '';
                }}
              />
            </label>
            <button
              className="e-button primary"
              disabled={busy || !script.trim()}
              onClick={() =>
                void run(async () => {
                  const r = await api<{
                    title: string;
                    parts: { text: string }[];
                    categories: string[];
                  }>('/stories/parse', 'POST', { text: script });
                  edit({
                    title: r.title,
                    parts: r.parts.map((p, i) => ({ id: story?.parts[i]?.id ?? '', text: p.text })),
                    categories: r.categories.length ? r.categories : story?.categories,
                    source_script: script,
                    ...(draftHook ? { hook: draftHook } : {}),
                  });
                  setDraftHook('');
                  setPartIndex(0);
                  setImportOpen(false);
                  setNotice('Сценарий разобран. Проверьте части и сохраните.');
                })
              }
            >
              Разобрать по частям
            </button>
          </div>
          {(error || notice) && <p role={error ? 'alert' : 'status'}>{error || notice}</p>}
        </form>
      </dialog>
    </section>
  );
}
