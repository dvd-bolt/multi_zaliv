import { useEffect, useRef, useState } from 'react';
import { Plus, Upload, Save, Play, RefreshCw, Trash2 } from 'lucide-react';
import { api, type Asset } from './api';

type Candidate = {
  id: string;
  start: number;
  end: number;
  title: string;
  reason?: string;
  score?: number;
  selected: boolean;
  status?: string;
  has_output?: boolean;
};
type Source = {
  id: string;
  name: string;
  url: string;
  status: string;
  detail: string;
  progress: number;
  error: string;
  duration: number;
  method: string;
  has_video: boolean;
  candidates: Candidate[];
  settings: { crop: 'blur' | 'center'; captions: boolean; style: 'words' | 'classic' };
  updated: string;
};
const PAGE = 4;
const busyState = (s?: Source) => !!s && /^(queued|running)/.test(s.status);
const clock = (t: number) => `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, '0')}`;
const seconds = (v: string) => {
  const m = v.trim().match(/^(\d+):(\d{1,2}(?:\.\d+)?)$/);
  return m ? Number(m[1]) * 60 + Number(m[2]) : Number(v.replace(',', '.'));
};

export default function Clips({
  paused,
  onReady,
}: {
  paused: boolean;
  onReady: (assets: Asset[]) => void;
}) {
  const [sources, setSources] = useState<Source[] | null>(null),
    [current, setCurrent] = useState<Source | null>(null),
    [dirty, setDirty] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(''),
    [notice, setNotice] = useState(''),
    [link, setLink] = useState(''),
    [page, setPage] = useState(0),
    [preview, setPreview] = useState<string>(''),
    [gemini, setGemini] = useState(false);
  const selectedRef = useRef(''),
    dirtyRef = useRef(false);
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
  const accept = (s: Source) => {
    selectedRef.current = s.id;
    dirtyRef.current = false;
    setDirty(false);
    setCurrent(s);
  };
  const load = async () => {
    const r = await api<{ sources: Source[]; gemini: boolean }>('/clips');
    setSources(r.sources);
    setGemini(r.gemini);
    if (!dirtyRef.current) {
      const s = r.sources.find((x) => x.id === selectedRef.current) ?? r.sources[0];
      if (s) accept(s);
      else setCurrent(null);
    }
  };
  useEffect(() => {
    void load().catch(() => setError('Не удалось загрузить нарезки'));
    const timer = setInterval(() => void load().catch(() => undefined), 2500);
    return () => clearInterval(timer);
  }, []);
  const edit = (patch: Partial<Source>) => {
    if (!current) return;
    dirtyRef.current = true;
    setDirty(true);
    setCurrent({ ...current, ...patch });
  };
  const editClip = (id: string, patch: Partial<Candidate>) =>
    current &&
    edit({ candidates: current.candidates.map((c) => (c.id === id ? { ...c, ...patch } : c)) });
  const save = async () => {
    if (!current) return null;
    const saved = await api<Source>(`/clips/${current.id}`, 'PUT', {
      candidates: current.candidates,
      ...current.settings,
    });
    accept(saved);
    return saved;
  };
  const locked = busyState(current ?? undefined) || busy;
  const chosen = current?.candidates.filter((c) => c.selected) ?? [];
  const pages = Math.max(1, Math.ceil((current?.candidates.length ?? 0) / PAGE));
  return (
    <section className="e-content stories-view clips-view">
      <div className="e-page-head">
        <div>
          <h1>Нарезки</h1>
          <p>Длинное видео → лучшие моменты → вертикальные клипы с субтитрами → монтажная</p>
        </div>
      </div>
      {(error || notice) && (
        <div className="story-message" role={error ? 'alert' : 'status'}>
          {error || notice}
        </div>
      )}
      <div className="stories-layout">
        <aside className="stories-list">
          <h3>Источники</h3>
          <input
            className="e-field"
            aria-label="Ссылка на видео"
            placeholder="Ссылка YouTube…"
            value={link}
            onChange={(e) => setLink(e.target.value)}
          />
          <div className="e-row">
            <button
              className="e-button"
              disabled={busy || dirty || !/^https?:\/\//.test(link.trim())}
              onClick={() =>
                void run(async () => {
                  const s = await api<Source>('/clips/link', 'POST', { url: link.trim() });
                  setLink('');
                  accept(s);
                  await load();
                })
              }
            >
              <Plus /> По ссылке
            </button>
            <label className="e-button" aria-disabled={busy || dirty}>
              <Upload /> Файл
              <input
                type="file"
                accept="video/*"
                hidden
                disabled={busy || dirty}
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  e.target.value = '';
                  if (!file) return;
                  void run(async () => {
                    const form = new FormData();
                    form.append('file', file);
                    setNotice('Загружаю файл…');
                    accept(await api<Source>('/clips/upload', 'POST', form));
                    await load();
                    setNotice('');
                  });
                }}
              />
            </label>
          </div>
          {!sources ? (
            <p className="e-muted">Загрузка…</p>
          ) : !sources.length ? (
            <p className="e-muted">Добавьте ссылку или файл: подкаст, стрим, интервью.</p>
          ) : (
            sources.slice(0, 6).map((s) => (
              <button
                key={s.id}
                className={'story-list-item' + (current?.id === s.id ? ' on' : '')}
                disabled={dirty || busy}
                onClick={() => {
                  accept(s);
                  setPage(0);
                  setPreview('');
                }}
              >
                <b>{s.name}</b>
                <small>
                  {busyState(s)
                    ? `${s.detail} ${s.progress ? s.progress + '%' : ''}`
                    : s.status === 'failed'
                      ? 'Ошибка'
                      : `моментов: ${s.candidates.length}`}
                </small>
              </button>
            ))
          )}
        </aside>
        {!current ? (
          <div className="story-workspace">
            <p className="e-muted">
              Программа скачает видео, расшифрует речь и предложит 3–10 моментов по 20–60 секунд.
              {gemini
                ? ' Моменты выбирает Gemini по тексту.'
                : ' Без ключа Gemini — по плотности речи и громкости.'}
            </p>
          </div>
        ) : (
          <>
            <div className="story-workspace">
              <div className="story-partbar">
                <b className="clip-name">{current.name}</b>
                <span className="e-muted">
                  {current.duration ? clock(current.duration) : ''}
                  {current.method ? ` · ${current.method}` : ''}
                </span>
              </div>
              <div className="story-panel">
                {current.status === 'failed' && !current.candidates.length ? (
                  <p role="alert">{current.error || 'Не удалось обработать источник'}</p>
                ) : !current.candidates.length ? (
                  <p className="e-muted">
                    {busyState(current)
                      ? `${current.detail}${current.progress ? ` · ${current.progress}%` : ''}. Первая расшифровка загружает модель речи.`
                      : 'Моментов нет. Запустите поиск ещё раз.'}
                  </p>
                ) : (
                  <div className="clip-list">
                    {current.candidates.slice(page * PAGE, page * PAGE + PAGE).map((c) => (
                      <div className={'clip-row' + (c.selected ? ' on' : '')} key={c.id}>
                        <input
                          type="checkbox"
                          aria-label="Взять момент"
                          checked={c.selected}
                          disabled={locked}
                          onChange={(e) => editClip(c.id, { selected: e.target.checked })}
                        />
                        <input
                          className="e-field clip-title"
                          aria-label="Заголовок клипа"
                          value={c.title}
                          maxLength={80}
                          disabled={locked}
                          onChange={(e) => editClip(c.id, { title: e.target.value })}
                        />
                        <input
                          className="e-field clip-time"
                          aria-label="Начало"
                          defaultValue={clock(c.start)}
                          key={`s-${c.id}-${c.start}`}
                          disabled={locked}
                          onBlur={(e) => {
                            const v = seconds(e.target.value);
                            if (Number.isFinite(v)) editClip(c.id, { start: v });
                          }}
                        />
                        <input
                          className="e-field clip-time"
                          aria-label="Конец"
                          defaultValue={clock(c.end)}
                          key={`e-${c.id}-${c.end}`}
                          disabled={locked}
                          onBlur={(e) => {
                            const v = seconds(e.target.value);
                            if (Number.isFinite(v)) editClip(c.id, { end: v });
                          }}
                        />
                        <button
                          className="e-button clip-play"
                          title={
                            c.has_output ? 'Смотреть собранный клип' : 'Смотреть момент в источнике'
                          }
                          onClick={() =>
                            setPreview(
                              c.has_output
                                ? `/api/clips/${current.id}/clips/${c.id}?v=${encodeURIComponent(current.updated)}`
                                : `/api/clips/${current.id}/video#t=${c.start},${c.end}`,
                            )
                          }
                        >
                          <Play />
                        </button>
                        <small className="e-muted clip-meta">
                          {Math.round(c.end - c.start)} с{c.score ? ` · ${c.score}/10` : ''}
                          {c.status === 'done'
                            ? ' · собран'
                            : c.status === 'queued'
                              ? ' · в очереди'
                              : ''}
                          {c.reason ? ` · ${c.reason}` : ''}
                        </small>
                      </div>
                    ))}
                  </div>
                )}
                {current.candidates.length > PAGE && (
                  <div className="e-row">
                    <button className="e-button" disabled={!page} onClick={() => setPage(page - 1)}>
                      Назад
                    </button>
                    <span className="e-muted">
                      {page + 1} / {pages}
                    </span>
                    <button
                      className="e-button"
                      disabled={page + 1 >= pages}
                      onClick={() => setPage(page + 1)}
                    >
                      Далее
                    </button>
                  </div>
                )}
                <div className="e-row clip-settings">
                  <select
                    aria-label="Кадрирование"
                    value={current.settings.crop}
                    disabled={locked}
                    onChange={(e) =>
                      edit({
                        settings: {
                          ...current.settings,
                          crop: e.target.value as 'blur' | 'center',
                        },
                      })
                    }
                  >
                    <option value="blur">Весь кадр + размытый фон</option>
                    <option value="center">Обрезать по центру</option>
                  </select>
                  <label className="story-check">
                    <input
                      type="checkbox"
                      checked={current.settings.captions}
                      disabled={locked}
                      onChange={(e) =>
                        edit({ settings: { ...current.settings, captions: e.target.checked } })
                      }
                    />
                    Субтитры по словам
                  </label>
                </div>
              </div>
              <div className="story-actions">
                <button
                  className="e-button"
                  disabled={locked || !dirty}
                  onClick={() => void run(async () => void (await save()))}
                >
                  <Save /> Сохранить
                </button>
                <button
                  className="e-button"
                  disabled={locked || dirty || (!current.has_video && !current.url)}
                  title={current.has_video
                    ? 'Расшифровать заново и найти моменты; ваши правки моментов заменятся'
                    : 'Продолжить загрузку по исходной ссылке; скачанные части сохранятся'}
                  onClick={() =>
                    void run(async () => {
                      accept(await api<Source>(`/clips/${current.id}/analyze`, 'POST'));
                    })
                  }
                >
                  <RefreshCw /> {current.has_video ? 'Найти заново' : 'Повторить загрузку'}
                </button>
                <button
                  className="e-button primary"
                  disabled={locked || !chosen.length}
                  onClick={() =>
                    void run(async () => {
                      const saved = dirty ? await save() : current;
                      if (!saved) return;
                      accept(await api<Source>(`/clips/${saved.id}/render`, 'POST'));
                    })
                  }
                >
                  <Play /> Собрать {chosen.length}
                </button>
                <button
                  className="e-button"
                  disabled={locked || dirty || !chosen.some((c) => c.status === 'done')}
                  onClick={() =>
                    void run(async () => {
                      const r = await api<{ assets: Asset[] }>(
                        `/clips/${current.id}/editor`,
                        'POST',
                      );
                      onReady(r.assets);
                    })
                  }
                >
                  В монтажную
                </button>
                <button
                  className="e-button"
                  aria-label="Удалить источник"
                  disabled={locked || dirty}
                  onClick={() =>
                    void run(async () => {
                      if (!window.confirm('Удалить источник и собранные клипы?')) return;
                      await api(`/clips/${current.id}`, 'DELETE');
                      selectedRef.current = '';
                      await load();
                    })
                  }
                >
                  <Trash2 />
                </button>
              </div>
              <div className="story-status" role="status">
                {paused ? 'Worker на паузе — обработка начнётся после «Запустить». ' : ''}
                {busyState(current)
                  ? `${current.detail}${current.progress ? ` · ${current.progress}%` : ''}`
                  : current.error || (dirty ? 'Есть несохранённые изменения' : current.detail)}
                {busyState(current) && (
                  <button
                    className="text-link"
                    onClick={() =>
                      void run(async () =>
                        accept(await api<Source>(`/clips/${current.id}/cancel`, 'POST')),
                      )
                    }
                  >
                    Отменить
                  </button>
                )}
              </div>
            </div>
            <aside className="story-preview">
              <h3>Предпросмотр</h3>
              <div className="story-screen">
                {preview ? (
                  <video key={preview} src={preview} controls autoPlay playsInline />
                ) : (
                  <p className="e-muted">Нажмите ▶ у момента</p>
                )}
              </div>
              <p className="e-muted">
                Отмечено: {chosen.length} · собрано:{' '}
                {chosen.filter((c) => c.status === 'done').length}. Баннер, каналы и время — в
                монтажной.
              </p>
            </aside>
          </>
        )}
      </div>
    </section>
  );
}
