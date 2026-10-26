import {
  Children,
  cloneElement,
  isValidElement,
  useEffect,
  useId,
  useRef,
  useState,
  type ReactElement,
  type ReactNode,
} from 'react';
import {
  Clapperboard,
  ListVideo,
  Users,
  ChartNoAxesCombined,
  Settings2,
  Plus,
  Play,
  Pause,
  Upload,
  ChevronLeft,
  ChevronRight,
  X,
  Check,
  Save,
  Download,
  Trash2,
} from 'lucide-react';
import { api, type Account, type Asset, type Edit, type Job, type Metric, type State } from './api';

import { latestMetrics, channelSummaries, channelsReport } from './statistics';
import { CampaignPanel, JobCampaignPanel } from './Campaign';

const clock = (n: number) =>
  `${Math.floor(n / 60)
    .toString()
    .padStart(2, '0')}:${Math.floor(n % 60)
    .toString()
    .padStart(2, '0')}`;
const authPending = (status?: string) =>
  status === 'waiting' ||
  status === 'browser_waiting' ||
  status === 'verifying' ||
  status === 'action:google' ||
  !!status?.startsWith('confirm:');
const label: Record<string, string> = {
  queued: 'В очереди',
  rendering: 'Рендер',
  ready: 'Готов к проверке',
  waiting_auth: 'Нужен вход',
  scheduled: 'Запланирован',
  uploading: 'Отправляется',
  platform_processing: 'Обработка платформой',
  published: 'Обработан платформой',
  failed: 'Ошибка рендера',
  cancelled: 'Отменён',
  unknown: 'Результат неизвестен',
  missed: 'Время пропущено',
  rejected: 'Отклонён платформой',
};
const visibility: Record<string, string> = {
  private: 'Только я',
  unlisted: 'По ссылке',
  public: 'Открытый доступ',
};
type Run = (task: () => Promise<unknown>, success?: string) => Promise<void>;
const canAutoPublish = (account: Account) =>
  account.platform !== 'tiktok' || account.method === 'browser_verified';
function Button({
  children,
  primary = false,
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { primary?: boolean }) {
  return (
    <button className={'e-button' + (primary ? ' primary' : '')} {...props}>
      {children}
    </button>
  );
}
function Field({ label, children }: { label: string; children: ReactNode }) {
  const id = useId();
  const connect = (nodes: ReactNode): ReactNode =>
    Children.map(nodes, (node) => {
      if (!isValidElement(node)) return node;
      const element = node as ReactElement<{
        id?: string;
        'aria-labelledby'?: string;
        children?: ReactNode;
      }>;
      if (['input', 'select', 'textarea'].includes(String(node.type)))
        return cloneElement(element, { id, 'aria-labelledby': id + '-label' });
      if (element.props.children) return cloneElement(element, {}, connect(element.props.children));
      return node;
    });
  return (
    <div className="e-label">
      <label id={id + '-label'} htmlFor={id}>
        {label}
      </label>
      {connect(children)}
    </div>
  );
}
function Pager({
  page,
  set,
  total,
  size,
}: {
  page: number;
  set: (n: number) => void;
  total: number;
  size: number;
}) {
  const max = Math.max(1, Math.ceil(total / size));
  return (
    <div className="e-pager">
      <Button aria-label="Предыдущая страница" disabled={page <= 0} onClick={() => set(page - 1)}>
        <ChevronLeft size={13} />
      </Button>
      <span className="e-mono">
        {Math.min(page + 1, max)} / {max}
      </span>
      <Button
        aria-label="Следующая страница"
        disabled={page >= max - 1}
        onClick={() => set(page + 1)}
      >
        <ChevronRight size={13} />
      </Button>
    </div>
  );
}
function Modal({
  title,
  onClose,
  children,
  error,
  busy = false,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  error?: string;
  busy?: boolean;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const element = ref.current!;
    element.showModal();
    return () => element.close();
  }, []);
  return (
    <dialog
      ref={ref}
      aria-label={title}
      className="e-native-dialog"
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="e-dialog">
        <div className="e-row e-between">
          <h2>{title}</h2>
          <Button aria-label="Закрыть диалог" disabled={busy} onClick={onClose}>
            <X size={14} />
          </Button>
        </div>
        {children}
        {error && (
          <p className="e-modal-error" role="alert">
            {error}
          </p>
        )}
      </div>
    </dialog>
  );
}

function History({ rows }: { rows: Metric[] }) {
  const [metric, setMetric] = useState('views');
  const points = rows
    .filter((row) => row.values[metric] != null)
    .map((row) => ({ x: Date.parse(row.captured), y: row.values[metric]! }));
  const minX = points[0]?.x ?? 0,
    maxX = points.at(-1)?.x ?? 1,
    maxY = Math.max(1, ...points.map((p) => p.y));
  const coords = points.map((p) => ({
    x: 32 + ((p.x - minX) / Math.max(1, maxX - minX)) * 340,
    y: 130 - (p.y / maxY) * 100,
  }));
  return (
    <>
      <Field label="Показатель">
        <select className="e-field" value={metric} onChange={(e) => setMetric(e.target.value)}>
          <option value="views">Просмотры</option>
          <option value="likes">Лайки</option>
          <option value="comments">Комментарии</option>
          <option value="shares">Репосты</option>
        </select>
      </Field>
      {points.length ? (
        <>
          <svg
            viewBox="0 0 400 155"
            role="img"
            aria-label={`Динамика: ${points.length} снимков`}
            className="history-chart"
          >
            <path d="M32 25 V130 H380" fill="none" stroke="#596570" />
            <text x="2" y="24" fill="#a4aebb" fontSize="10">
              {maxY.toLocaleString('ru-RU')}
            </text>
            <text x="12" y="133" fill="#a4aebb" fontSize="10">
              0
            </text>
            <polyline
              points={coords.map((p) => `${p.x},${p.y}`).join(' ')}
              fill="none"
              stroke="#f5a064"
              strokeWidth="2"
            />
            {coords.map((p, i) => (
              <circle key={i} cx={p.x} cy={p.y} r="3" fill="#f5a064">
                <title>
                  {new Date(points[i].x).toLocaleString('ru-RU')} · {points[i].y}
                </title>
              </circle>
            ))}
          </svg>
          <p>
            {new Date(minX).toLocaleString('ru-RU')} → {new Date(maxX).toLocaleString('ru-RU')}
          </p>
        </>
      ) : (
        <p>Для этого показателя данных пока нет.</p>
      )}
      <p>
        Каждая точка — сохранённый накопительный счётчик одного ролика. Источники:{' '}
        {[...new Set(rows.map((r) => r.source))].join(', ') || '—'}. Импортированные данные не
        подтверждают публикацию.
      </p>
    </>
  );
}

export default function App() {
  const [data, setData] = useState<State | null>(null),
    [view, setView] = useState('editor'),
    [message, setMessage] = useState(''),
    [error, setError] = useState(false),
    [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState(''),
    [draft, setDraft] = useState<Edit | null>(null),
    [dirty, setDirty] = useState(false),
    [tab, setTab] = useState('banner'),
    [sourcePage, setSourcePage] = useState(0);
  const [modal, setModal] = useState(''),
    [currentJob, setCurrentJob] = useState<Job | null>(null),
    [currentAccount, setCurrentAccount] = useState<Account | null>(null);
  const [accountName, setAccountName] = useState(''),
    [platform, setPlatform] = useState('youtube'),
    [schedule, setSchedule] = useState(''),
    [allSources, setAllSources] = useState(false);
  const [metrics, setMetrics] = useState<Metric[]>([]),
    [jobPage, setJobPage] = useState(0),
    [channelPage, setChannelPage] = useState(0),
    [metricPage, setMetricPage] = useState(0),
    [filter, setFilter] = useState('all');
  const [historyJob, setHistoryJob] = useState(''),
    [externalId, setExternalId] = useState('');
  const [batchIds, setBatchIds] = useState<string[]>([]),
    [batchPage, setBatchPage] = useState(0);
  const [testConsent, setTestConsent] = useState(false);
  const [statsAccount, setStatsAccount] = useState('all');
  const [statsMode, setStatsMode] = useState('videos');
  const [statsPlatform, setStatsPlatform] = useState('all');
  const [browserPairing, setBrowserPairing] = useState<{ code: string; expires_at: number } | null>(
    null,
  );
  const [archiveCandidate, setArchiveCandidate] = useState<Asset | null>(null);
  const [targetPage, setTargetPage] = useState(0);
  const [showOriginal, setShowOriginal] = useState(false),
    [showBanner, setShowBanner] = useState(true);
  const metricFile = useRef<HTMLInputElement>(null);
  const [connectionLost, setConnectionLost] = useState(false);
  const mutationPending = useRef(false);
  const mutationGeneration = useRef(0),
    draftRevision = useRef(0);
  const [variantTarget, setVariantTarget] = useState(''),
    [time, setTime] = useState(0),
    [playing, setPlaying] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null),
    bannerRef = useRef<HTMLInputElement>(null),
    clientRef = useRef<HTMLInputElement>(null),
    sessionRef = useRef<HTMLInputElement>(null),
    video = useRef<HTMLVideoElement>(null),
    bannerVideo = useRef<HTMLVideoElement>(null),
    preview = useRef<HTMLDivElement>(null);
  const refresh = async () => {
    const next = await api<State>('/state');
    setData(next);
    return next;
  };
  useEffect(() => {
    setMessage('');
    setError(false);
    setTestConsent(false);
  }, [modal]);
  useEffect(() => {
    setCurrentJob((current) =>
      current ? (data?.jobs.find((j) => j.id === current.id) ?? current) : current,
    );
  }, [data?.jobs]);
  useEffect(() => {
    setCurrentAccount((current) =>
      current ? (data?.accounts.find((a) => a.id === current.id) ?? current) : current,
    );
  }, [data?.accounts]);
  useEffect(() => {
    let active = true;
    setBrowserPairing(null);
    if (modal === 'connection' && currentAccount?.auth === 'browser_waiting')
      void api<{ code: string; expires_at: number }>(
        `/accounts/${currentAccount.id}/browser-pairing`,
      )
        .then((value) => {
          if (active) setBrowserPairing(value);
        })
        .catch((error) => {
          if (active) {
            setMessage(error.message);
            setError(true);
          }
        });
    return () => {
      active = false;
    };
  }, [modal, currentAccount?.id, currentAccount?.auth]);
  useEffect(() => {
    let alive = true;
    let running = false;
    const load = async () => {
      if (running || mutationPending.current) return;
      running = true;
      const generation = mutationGeneration.current;
      try {
        const next = await api<State>('/state');
        if (alive && generation === mutationGeneration.current && !mutationPending.current) {
          setData(next);
          setConnectionLost(false);
        }
      } catch {
        if (alive) {
          setConnectionLost(true);
        }
      } finally {
        running = false;
      }
    };
    void load();
    const timer = setInterval(load, 2500);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);
  useEffect(() => {
    if (view === 'stats')
      void api<Metric[]>('/metrics')
        .then(setMetrics)
        .catch(() => {
          setMessage('Не удалось загрузить статистику');
          setError(true);
        });
  }, [view]);
  useEffect(() => {
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => {
      e.preventDefault();
    };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [dirty]);
  useEffect(() => {
    const sync = () => {
      const host = window as Window & {
        pywebview?: { api?: { set_dirty?: (value: boolean) => Promise<unknown> } };
      };
      void host.pywebview?.api?.set_dirty?.(dirty)?.catch(() => {});
    };
    sync();
    window.addEventListener('pywebviewready', sync);
    return () => window.removeEventListener('pywebviewready', sync);
  }, [dirty]);
  const videos = data?.assets.filter((a) => a.kind === 'video') ?? [];
  const asset = videos.find((a) => a.id === selected);
  useEffect(() => {
    if (!videos.some((video) => video.id === selected) && videos.length) {
      const next = videos[Math.min(sourcePage * 3, videos.length - 1)];
      setSelected(next.id);
      setDraft(next.edit);
      setDirty(false);
      setTime(0);
    } else if (selected && !videos.length) {
      setSelected('');
      setDraft(null);
      setDirty(false);
      setTime(0);
    }
  }, [selected, videos]);
  useEffect(() => {
    setSourcePage((page) => Math.min(page, Math.max(0, Math.ceil(videos.length / 3) - 1)));
  }, [videos.length]);
  useEffect(() => {
    setShowOriginal(false);
    setShowBanner(true);
    setPlaying(false);
  }, [selected]);
  useEffect(() => {
    if (view !== 'editor') setPlaying(false);
  }, [view]);
  const run: Run = async (task, success) => {
    if (mutationPending.current) return;
    mutationPending.current = true;
    mutationGeneration.current++;
    setBusy(true);
    setMessage('');
    try {
      await task();
      await refresh();
      if (success) {
        setMessage(success);
        setError(false);
      }
    } catch (e) {
      setMessage(e instanceof Error ? e.message : 'Не удалось выполнить действие');
      setError(true);
    } finally {
      mutationPending.current = false;
      setBusy(false);
    }
  };
  const patch = (value: Partial<Edit>) => {
    draftRevision.current++;
    setDraft((d) => (d ? { ...d, ...value } : d));
    setDirty(true);
  };
  const save = async () => {
    if (!asset || !draft) return;
    const revision = draftRevision.current;
    await api(`/assets/${asset.id}/edit`, 'PUT', draft);
    if (revision === draftRevision.current) setDirty(false);
  };
  const selectAsset = (next: Asset) =>
    next.id === selected
      ? undefined
      : void run(async () => {
          if (dirty) await save();
          setSelected(next.id);
          setDraft(next.edit);
          setDirty(false);
          setTime(0);
          setPlaying(false);
          setVariantTarget('');
        });
  const importFile = (file: File | undefined, kind: string) => {
    if (!file) return;
    void run(async () => {
      if (dirty) await save();
      const form = new FormData();
      form.append('file', file);
      const next = await api<Asset>(`/assets?kind=${kind}`, 'POST', form);
      setData((current) =>
        current
          ? { ...current, assets: [...current.assets.filter((a) => a.id !== next.id), next] }
          : current,
      );
      if (kind === 'video') {
        setSelected(next.id);
        setDraft(next.edit);
        setDirty(false);
        setTime(0);
        setSourcePage(Math.floor(videos.length / 3));
      } else {
        patch({ banner_id: next.id, freeze_video: true, banner_audio: !!next.media.has_audio });
      }
    }, 'Файл импортирован');
  };
  const close = () => {
    if (!busy) setModal('');
  };
  const start = draft?.timing === 'middle' ? (asset?.media.duration ?? 0) / 2 : (draft?.start ?? 0);
  const freezePreview = !!draft?.freeze_video && !showOriginal;
  const bannerDuration = draft?.duration ?? 0;
  const previewLength = (asset?.media.duration ?? 0) + (freezePreview ? bannerDuration : 0);
  const previewPosition = useRef(0);
  previewPosition.current = time;
  const sourceTime = (position: number) =>
    freezePreview && position >= start
      ? position < start + bannerDuration
        ? start
        : position - bannerDuration
      : position;
  useEffect(() => {
    const element = bannerVideo.current;
    if (!element || !draft) return;
    const local = Math.max(0, time - start);
    const duration = element.duration;
    const loop = draft.banner_end === 'loop';
    const position =
      Number.isFinite(duration) && duration > 0
        ? loop
          ? local % duration
          : Math.min(local, duration)
        : local;
    if (Math.abs(element.currentTime - position) > 0.2) element.currentTime = position;
    if (playing && (loop || !Number.isFinite(duration) || local < duration))
      void element.play().catch((error: unknown) => {
        if (error instanceof DOMException && error.name === 'AbortError') return;
        setError(true);
        setMessage('Не удалось воспроизвести звук баннера в предпросмотре. Проверьте готовый MP4.');
      });
    else element.pause();
  }, [
    time,
    playing,
    start,
    draft?.banner_id,
    draft?.banner_end,
    draft?.banner_audio,
    showBanner,
    showOriginal,
  ]);
  useEffect(() => {
    if (!freezePreview || !playing) return;
    let frame = 0;
    let previous = performance.now();
    const tick = (now: number) => {
      const element = video.current;
      if (!element) return;
      const position = Math.min(previewLength, previewPosition.current + (now - previous) / 1000);
      previous = now;
      previewPosition.current = position;
      const held = position >= start && position < start + bannerDuration;
      const source = position < start ? position : held ? start : position - bannerDuration;
      if (Math.abs(element.currentTime - source) > 0.15) element.currentTime = source;
      if (held || position >= previewLength) element.pause();
      else if (element.paused) void element.play().catch(() => setPlaying(false));
      setTime(position);
      if (position >= previewLength) setPlaying(false);
      else frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [freezePreview, playing, start, bannerDuration, previewLength, asset?.id]);
  const activeFilters = draft
    ? variantTarget && draft.overrides[variantTarget]
      ? draft.overrides[variantTarget]
      : draft
    : null;
  const slider = (key: keyof Edit, text: string, min: number, max: number, step = 1) => {
    const value = Number(
      tab === 'filters' && activeFilters
        ? activeFilters[key as string as keyof typeof activeFilters]
        : (draft?.[key] ?? 0),
    );
    return (
      <label className="e-label" htmlFor={'range-' + key}>
        <span>
          {text}
          <output>{Number(value.toFixed(2))}</output>
        </span>
        <input
          id={'range-' + key}
          aria-label={text}
          className="e-field"
          type="range"
          min={min}
          max={max}
          step={step}
          value={value}
          onChange={(e) => {
            const n = Number(e.target.value);
            if (tab === 'filters' && variantTarget && draft) {
              const defaults = {
                brightness: draft.brightness,
                contrast: draft.contrast,
                saturation: draft.saturation,
                warmth: draft.warmth,
                zoom: draft.zoom,
                mirror: draft.mirror,
              };
              patch({
                overrides: {
                  ...draft.overrides,
                  [variantTarget]: { ...defaults, ...draft.overrides[variantTarget], [key]: n },
                },
              });
            } else patch({ [key]: n });
          }}
        />
      </label>
    );
  };
  if (!data)
    return (
      <div className="boot">
        <h1>SurvivalFlow</h1>
        <p>
          {connectionLost
            ? 'Нет связи с локальным сервером. Запустите SurvivalFlow.'
            : message || 'Подключение к локальному серверу…'}
        </p>
      </div>
    );
  const banners = data.assets.filter((a) => a.kind === 'banner');
  const overlayMedia = banners.find((b) => b.id === draft?.banner_id)?.media ?? {
    width: 960,
    height: 180,
    has_audio: false,
  };
  const overlayWidth = draft
    ? draft.banner_height
      ? draft.width
      : Math.min(draft.width, (100 * (overlayMedia.width / overlayMedia.height)) / (9 / 16))
    : 84;
  const overlayHeight =
    draft?.banner_height ?? (overlayWidth * (9 / 16)) / (overlayMedia.width / overlayMedia.height);
  const targets = draft?.targets ?? [];
  const filteredJobs = data.jobs.filter((j) => filter === 'all' || j.status === filter);
  const visibleJobs = filteredJobs.slice(jobPage * 4, jobPage * 4 + 4);
  const statsAccounts = data.accounts.filter(
    (a) =>
      (statsAccount === 'all' || a.id === statsAccount) &&
      (statsPlatform === 'all' || a.platform === statsPlatform),
  );
  const statsIds = new Set(statsAccounts.map((a) => a.id));
  const jobsById = new Map(data.jobs.map((j) => [j.id, j]));
  const metricRows = latestMetrics(metrics).filter((m) => {
    const job = jobsById.get(m.job_id);
    return job && statsIds.has(job.account.id);
  });
  const summaries = channelSummaries(statsAccounts, data.jobs, metricRows);
  const uploadJson = async (file: File | undefined, path: string, method = 'PUT') => {
    if (!file) return;
    await run(async () => {
      const payload = JSON.parse(await file.text());
      await api(path, method, payload);
    }, 'Данные сохранены в защищённом хранилище Windows');
  };
  return (
    <div id="sf-editors">
      <div className="ed classic" aria-busy={busy}>
        <input
          hidden
          ref={metricFile}
          type="file"
          accept=".csv"
          onChange={(e) => {
            const file = e.target.files?.[0];
            e.target.value = '';
            if (file)
              void run(async () => {
                const form = new FormData();
                form.append('file', file);
                await api('/metrics/import', 'POST', form);
                setMetrics(await api('/metrics'));
                setModal('');
              }, 'CSV импортирован; источник данных указан в таблице');
          }}
        />
        <input
          hidden
          ref={fileRef}
          type="file"
          accept="video/*,.mkv"
          onChange={(e) => {
            importFile(e.target.files?.[0], 'video');
            e.target.value = '';
          }}
        />
        <input
          hidden
          ref={bannerRef}
          type="file"
          accept="image/png,image/jpeg,image/webp,image/gif,video/mp4,video/webm,video/quicktime,.mov"
          onChange={(e) => {
            importFile(e.target.files?.[0], 'banner');
            e.target.value = '';
          }}
        />
        <input
          hidden
          ref={clientRef}
          type="file"
          accept=".json"
          onChange={(e) => {
            void uploadJson(e.target.files?.[0], '/google-client');
            e.target.value = '';
          }}
        />
        <input
          hidden
          ref={sessionRef}
          type="file"
          accept=".json"
          onChange={(e) => {
            if (currentAccount)
              void uploadJson(
                e.target.files?.[0],
                `/accounts/${currentAccount.id}/session`,
                'POST',
              );
            e.target.value = '';
          }}
        />
        <header className="e-head">
          <div className="e-row">
            <span className="e-logo">
              survival<b>flow</b>
            </span>
            <span className="e-badge">LOCAL · 0.1</span>
            <span className="e-project" title={data.settings.project_name}>
              {data.settings.project_name}
            </span>
          </div>
          <div className="e-row">
            <span className={'e-badge ' + (data.worker ? 'good' : 'bad')}>
              {data.worker ? 'Процесс работает' : 'Процесс не запущен'}
            </span>
            <Button
              disabled={busy}
              onClick={() =>
                void run(
                  () => api('/settings', 'PATCH', { paused: !data.settings.paused }),
                  data.settings.paused ? 'Очередь запущена' : 'Новые задания приостановлены',
                )
              }
            >
              {data.settings.paused ? <Play /> : <Pause />}
              {data.settings.paused ? 'Запустить' : 'Пауза'}
            </Button>
          </div>
        </header>
        <div className="e-layout">
          <nav className="e-nav" aria-label="Разделы">
            {[
              [Clapperboard, 'editor', 'Монтаж'],
              [ListVideo, 'queue', 'Очередь'],
              [Users, 'channels', 'Каналы'],
              [ChartNoAxesCombined, 'stats', 'Статистика'],
              [Settings2, 'settings', 'Настройки'],
            ].map(([Icon, key, name]) => {
              const Glyph = Icon as typeof Clapperboard;
              return (
                <button
                  key={String(key)}
                  aria-pressed={view === key}
                  onClick={() => setView(String(key))}
                >
                  <Glyph />
                  <span>{String(name)}</span>
                </button>
              );
            })}
          </nav>
          <main data-view-root>
            {view === 'editor' && (
              <div className="e-editor">
                <aside className="e-assets">
                  <div className="e-row e-between e-assets-head">
                    <b>Исходники</b>
                    <span className="e-mono">{videos.length}</span>
                  </div>
                  {videos.slice(sourcePage * 3, sourcePage * 3 + 3).map((a) => (
                    <div key={a.id} className={'e-source' + (a.id === selected ? ' active' : '')}>
                      <button
                        className="e-source-select"
                        disabled={busy}
                        onClick={() => selectAsset(a)}
                      >
                        <div
                          className="e-thumb"
                          style={{
                            backgroundImage: `url(/api/assets/${a.id}/thumbnail)`,
                            backgroundSize: 'cover',
                            backgroundPosition: 'center',
                          }}
                        >
                          <span>{clock(a.media.duration)}</span>
                        </div>
                        <span className="e-source-title" title={a.name}>
                          {a.name}
                        </span>
                      </button>
                      <div className="e-source-tools">
                        <span>
                          {a.media.width}×{a.media.height}
                        </span>
                        <button
                          aria-label={`Убрать ${a.name} из списка`}
                          title="Убрать из монтажной"
                          disabled={busy}
                          onClick={() => {
                            setArchiveCandidate(a);
                            setModal('archive');
                          }}
                        >
                          <Trash2 size={12} />
                        </button>
                      </div>
                    </div>
                  ))}
                  {videos.length === 0 && (
                    <p className="e-small e-muted">
                      Добавьте первый ролик. Исходный файл сохранится без изменений.
                    </p>
                  )}
                  <div className="asset-pager">
                    <Pager page={sourcePage} set={setSourcePage} total={videos.length} size={3} />
                  </div>
                  <Button
                    className="e-button e-add"
                    disabled={busy}
                    onClick={() => fileRef.current?.click()}
                  >
                    <Plus size={13} />
                    Исходник
                  </Button>
                </aside>
                <section className="e-viewer">
                  <div className="e-viewer-heading">
                    <span>ПРЕДПРОСМОТР · 9:16</span>
                    <span>{draft?.format ?? '1080'}p</span>
                  </div>
                  <div className="e-preview" ref={preview}>
                    {asset && draft ? (
                      <>
                        <video
                          key={asset.id}
                          ref={video}
                          src={`/api/assets/${asset.id}/file`}
                          className="e-video"
                          playsInline
                          muted={
                            !showOriginal &&
                            showBanner &&
                            !!draft.banner_audio &&
                            !!overlayMedia.has_audio &&
                            time >= start &&
                            time < start + draft.duration
                          }
                          style={{
                            objectFit: draft.fit === 'crop' ? 'cover' : 'contain',
                            filter: showOriginal
                              ? 'none'
                              : `brightness(${1 + Number(activeFilters?.brightness ?? 0) / 100}) contrast(${Number(activeFilters?.contrast ?? 100) / 100}) saturate(${Number(activeFilters?.saturation ?? 100) / 100})`,
                            transform: showOriginal
                              ? 'none'
                              : `scale(${Number(activeFilters?.zoom ?? 100) / 100}) scaleX(${activeFilters?.mirror ? -1 : 1})`,
                          }}
                          onTimeUpdate={(e) => {
                            if (!freezePreview) setTime(e.currentTarget.currentTime);
                          }}
                          onPlay={() => setPlaying(true)}
                          onPause={() => {
                            if (!freezePreview) setPlaying(false);
                          }}
                          onEnded={() => {
                            if (!freezePreview) setPlaying(false);
                          }}
                          onLoadedMetadata={(e) => {
                            e.currentTarget.currentTime = Math.min(
                              sourceTime(time),
                              asset.media.duration,
                            );
                          }}
                        />
                        {!showOriginal &&
                          showBanner &&
                          time >= start &&
                          time <= start + draft.duration && (
                            <div
                              className="e-overlay image"
                              style={{
                                left: `${Math.max(overlayWidth / 2, Math.min(100 - overlayWidth / 2, draft.x))}%`,
                                top: `${Math.max(overlayHeight / 2, Math.min(100 - overlayHeight / 2, draft.y))}%`,
                                width: `${overlayWidth}%`,
                                height: draft.banner_height ? `${draft.banner_height}%` : undefined,
                                opacity: draft.opacity / 100,
                              }}
                              onPointerDown={(e) => {
                                e.currentTarget.setPointerCapture(e.pointerId);
                              }}
                              onPointerMove={(e) => {
                                if (e.buttons !== 1 || !preview.current) return;
                                const box = preview.current.getBoundingClientRect();
                                patch({
                                  x: Math.max(
                                    0,
                                    Math.min(100, ((e.clientX - box.left) / box.width) * 100),
                                  ),
                                  y: Math.max(
                                    0,
                                    Math.min(100, ((e.clientY - box.top) / box.height) * 100),
                                  ),
                                });
                              }}
                            >
                              {draft.banner_id ? (
                                banners.find((b) => b.id === draft.banner_id)?.media.image ? (
                                  <img
                                    style={
                                      draft.banner_height
                                        ? { height: '100%', objectFit: 'fill' }
                                        : undefined
                                    }
                                    src={`/api/assets/${draft.banner_id}/file`}
                                    alt="Баннер"
                                    draggable={false}
                                  />
                                ) : (
                                  <video
                                    style={
                                      draft.banner_height
                                        ? { height: '100%', objectFit: 'fill' }
                                        : undefined
                                    }
                                    ref={bannerVideo}
                                    src={`/api/assets/${draft.banner_id}/file`}
                                    playsInline
                                    muted={!draft.banner_audio}
                                    onLoadedMetadata={(e) => {
                                      e.currentTarget.currentTime = Math.max(0, time - start);
                                      if (playing)
                                        void e.currentTarget.play().catch((error: unknown) => {
                                          if (
                                            error instanceof DOMException &&
                                            error.name === 'AbortError'
                                          )
                                            return;
                                          setError(true);
                                          setMessage(
                                            'Не удалось воспроизвести звук баннера в предпросмотре. Проверьте готовый MP4.',
                                          );
                                        });
                                    }}
                                    loop={draft.banner_end === 'loop'}
                                  />
                                )
                              ) : (
                                <img
                                  src={`/api/banner-preview?text=${encodeURIComponent(draft.banner_text)}`}
                                  alt="Текстовый баннер"
                                  draggable={false}
                                />
                              )}
                            </div>
                          )}
                      </>
                    ) : (
                      <div className="preview-empty">
                        <Clapperboard size={34} />
                        <span>Ваш ролик появится здесь</span>
                      </div>
                    )}
                  </div>
                  <div className="e-viewer-controls">
                    <Button
                      disabled={!asset}
                      aria-label={playing ? 'Остановить воспроизведение' : 'Воспроизвести'}
                      onClick={() => {
                        if (playing) {
                          video.current?.pause();
                          setPlaying(false);
                        } else if (freezePreview) {
                          if (time >= previewLength) {
                            previewPosition.current = 0;
                            setTime(0);
                          }
                          setPlaying(true);
                        } else
                          void video.current?.play().catch(() => {
                            setError(true);
                            setMessage(
                              'Браузер не воспроизводит этот кодек. Проверьте готовый MP4 после экспорта.',
                            );
                          });
                      }}
                    >
                      {playing ? <Pause /> : <Play />}
                    </Button>
                    <span className="e-mono">
                      {clock(time)} / {clock(previewLength)}
                    </span>
                    <Button
                      disabled={!asset}
                      onClick={() => {
                        if (video.current) {
                          video.current.currentTime = start;
                          setTime(start);
                        }
                      }}
                    >
                      К баннеру
                    </Button>
                  </div>
                  <div className="e-viewer-tools">
                    <Button
                      className="e-button small"
                      disabled={!asset}
                      aria-label="Правила БУБА"
                      onClick={() => setModal('buba')}
                    >
                      БУБА
                    </Button>
                    <Button
                      className="e-button small"
                      disabled={!asset}
                      aria-pressed={showOriginal}
                      title="Сравнить с исходником; настройки сохраняются"
                      onClick={() => setShowOriginal((value) => !value)}
                    >
                      {showOriginal ? 'Исходник' : 'До / после'}
                    </Button>
                    <Button
                      className="e-button small"
                      disabled={!asset || showOriginal}
                      aria-pressed={showBanner}
                      onClick={() => setShowBanner((value) => !value)}
                    >
                      {showBanner ? 'Скрыть баннер' : 'Показать баннер'}
                    </Button>
                    <select
                      className="e-field"
                      aria-label="Вариант предпросмотра"
                      title="Черновой просмотр. Точный результат — в готовом MP4."
                      value={variantTarget}
                      onChange={(e) => setVariantTarget(e.target.value)}
                    >
                      <option value="">Общие настройки</option>
                      {data.accounts.map((a) => (
                        <option key={a.id} value={a.id}>
                          {a.name}
                        </option>
                      ))}
                    </select>
                  </div>
                </section>
                <section className="e-timeline" aria-label="Таймлайн">
                  <div className="e-ruler">
                    <span>00:00</span>
                    <span>{clock(previewLength / 2)}</span>
                    <span>{clock(previewLength)}</span>
                  </div>
                  <div className="e-track">{asset?.name ?? 'Видеодорожка'}</div>
                  <div className="e-track banner">
                    {asset && draft && (
                      <span
                        className="e-banner-mark"
                        style={{
                          left: `${(start / previewLength) * 100}%`,
                          width: `${(Math.min(draft.duration, previewLength - start) / previewLength) * 100}%`,
                        }}
                      />
                    )}
                  </div>
                  <input
                    aria-label="Позиция воспроизведения"
                    className="scrubber"
                    type="range"
                    min="0"
                    max={previewLength || 1}
                    step="0.01"
                    value={time}
                    disabled={!asset}
                    onChange={(e) => {
                      if (video.current)
                        video.current.currentTime = sourceTime(Number(e.target.value));
                      setTime(Number(e.target.value));
                    }}
                  />
                </section>
                <aside className="e-inspector">
                  <div className="e-tabs" role="tablist" aria-label="Настройки ролика">
                    {[
                      ['banner', 'Баннер'],
                      ['filters', 'Варианты'],
                      ['publish', 'Публикация'],
                    ].map(([key, name]) => (
                      <button
                        role="tab"
                        aria-selected={tab === key}
                        key={key}
                        onClick={() => setTab(key)}
                      >
                        {name}
                      </button>
                    ))}
                  </div>
                  <div data-inspector role="tabpanel">
                    {!draft ? (
                      <p className="e-muted">Загрузите видео, чтобы открыть настройки.</p>
                    ) : tab === 'banner' ? (
                      <>
                        <Field label="Файл баннера">
                          <div className="e-file-picker">
                            <select
                              className="e-field"
                              value={draft.banner_id ?? ''}
                              onChange={(e) =>
                                patch({
                                  banner_id: e.target.value || null,
                                  freeze_video: true,
                                  banner_audio: !!banners.find((b) => b.id === e.target.value)
                                    ?.media.has_audio,
                                })
                              }
                            >
                              <option value="">Текстовый баннер</option>
                              {banners.map((b) => (
                                <option key={b.id} value={b.id}>
                                  {b.name}
                                </option>
                              ))}
                            </select>
                            <Button
                              aria-label="Импортировать баннер"
                              disabled={busy}
                              onClick={() => bannerRef.current?.click()}
                            >
                              <Upload size={13} />
                            </Button>
                          </div>
                        </Field>
                        <div className="e-segment">
                          {[
                            ['middle', 'В середине'],
                            ['manual', 'Таймкод'],
                          ].map(([key, name]) => (
                            <button
                              key={key}
                              aria-pressed={draft.timing === key}
                              onClick={() => patch({ timing: key as Edit['timing'] })}
                            >
                              {name}
                            </button>
                          ))}
                        </div>
                        <div className="e-grid2">
                          <Field label="Начало, с">
                            <input
                              className="e-field"
                              type="number"
                              step="0.1"
                              min="0"
                              max={asset?.media.duration}
                              value={
                                draft.timing === 'middle' ? Number(start.toFixed(2)) : draft.start
                              }
                              disabled={draft.timing === 'middle'}
                              onChange={(e) => patch({ start: Number(e.target.value) })}
                            />
                          </Field>
                          <Field label="Длительность, с">
                            <input
                              className="e-field"
                              type="number"
                              step="0.1"
                              min="0.1"
                              max="120"
                              value={draft.duration}
                              onChange={(e) => patch({ duration: Number(e.target.value) })}
                            />
                          </Field>
                        </div>
                        {slider('width', 'Размер, %', 10, 100)}
                        {slider('y', 'Позиция по вертикали, %', 0, 100)}
                        <div className="e-grid2">
                          <label className="e-check e-small">
                            <input
                              type="checkbox"
                              checked={!!draft.freeze_video}
                              onChange={(e) => {
                                video.current?.pause();
                                setPlaying(false);
                                setTime(0);
                                if (video.current) video.current.currentTime = 0;
                                patch({ freeze_video: e.target.checked });
                              }}
                            />
                            Стоп-кадр
                          </label>
                          <label className="e-check e-small">
                            <input
                              type="checkbox"
                              checked={!!draft.banner_audio}
                              disabled={!overlayMedia.has_audio}
                              onChange={(e) => patch({ banner_audio: e.target.checked })}
                            />
                            Звук баннера
                          </label>
                        </div>
                        <Button onClick={() => setModal('banner')}>
                          Текст и дополнительные параметры
                        </Button>
                      </>
                    ) : tab === 'filters' ? (
                      <>
                        <Field label="Настройки варианта">
                          <select
                            className="e-field"
                            value={variantTarget}
                            onChange={(e) => setVariantTarget(e.target.value)}
                          >
                            <option value="">Общие настройки</option>
                            {data.accounts.map((a) => (
                              <option key={a.id} value={a.id}>
                                {a.name}
                              </option>
                            ))}
                          </select>
                        </Field>
                        {slider('brightness', 'Яркость', -15, 15, 0.1)}
                        {slider('contrast', 'Контраст', 80, 120)}
                        {slider('saturation', 'Насыщенность', 70, 130)}
                        {slider('warmth', 'Теплота', -15, 15, 0.1)}
                        {slider('zoom', 'Масштаб, %', 100, 110, 0.1)}
                        <label className="e-check">
                          <input
                            type="checkbox"
                            checked={Boolean(activeFilters?.mirror)}
                            onChange={(e) => {
                              if (variantTarget) {
                                patch({
                                  overrides: {
                                    ...draft.overrides,
                                    [variantTarget]: {
                                      brightness: draft.brightness,
                                      contrast: draft.contrast,
                                      saturation: draft.saturation,
                                      warmth: draft.warmth,
                                      zoom: draft.zoom,
                                      ...draft.overrides[variantTarget],
                                      mirror: e.target.checked,
                                    },
                                  },
                                });
                              } else patch({ mirror: e.target.checked });
                            }}
                          />
                          Отразить по горизонтали
                        </label>
                        <p className="e-small e-muted">
                          Каждый аккаунт получит отдельный файл. Автоподбор слегка меняет яркость,
                          насыщенность и теплоту.
                        </p>
                      </>
                    ) : (
                      <>
                        <Field label="Название для YouTube">
                          <input
                            className="e-field"
                            value={draft.title}
                            maxLength={100}
                            onChange={(e) => patch({ title: e.target.value })}
                          />
                        </Field>
                        <Field label="Описание">
                          <textarea
                            className="e-field"
                            value={draft.caption}
                            maxLength={2200}
                            onChange={(e) => patch({ caption: e.target.value })}
                          />
                        </Field>
                        <Field label="Видимость">
                          <select
                            className="e-field"
                            value={draft.visibility}
                            onChange={(e) =>
                              patch({ visibility: e.target.value as Edit['visibility'] })
                            }
                          >
                            {Object.entries(visibility).map(([key, name]) => (
                              <option key={key} value={key}>
                                {name}
                              </option>
                            ))}
                          </select>
                        </Field>
                        <div className="e-grid2">
                          <Field label="Размер MP4">
                            <select
                              className="e-field"
                              value={draft.format}
                              onChange={(e) => patch({ format: e.target.value as Edit['format'] })}
                            >
                              <option value="1080">1080 × 1920</option>
                              <option value="720">720 × 1280</option>
                            </select>
                          </Field>
                          <Field label="Кадрирование">
                            <select
                              className="e-field"
                              value={draft.fit}
                              onChange={(e) => patch({ fit: e.target.value as Edit['fit'] })}
                            >
                              <option value="pad">Поля</option>
                              <option value="crop">Обрезать</option>
                            </select>
                          </Field>
                        </div>
                        <Button onClick={() => setModal('disclosures')}>
                          Сведения о содержимом
                        </Button>
                        <p className="e-small e-muted">
                          Постановка в очередь создаёт файлы. Отправка подтверждается после
                          просмотра.
                        </p>
                      </>
                    )}
                  </div>
                  <div className="e-savebar">
                    <div className="e-row e-between">
                      <b className="e-small">Получатели · {targets.length}</b>
                      {data.accounts.length > 6 && (
                        <button
                          className="text-link"
                          aria-label={`Все аккаунты (${data.accounts.length})`}
                          onClick={() => {
                            setTargetPage(0);
                            setModal('targets');
                          }}
                        >
                          Все ({data.accounts.length})
                        </button>
                      )}
                      <Button
                        disabled={!dirty || busy}
                        onClick={() => void run(save, 'Настройки сохранены')}
                      >
                        <Save size={12} />
                        {dirty ? 'Сохранить' : 'Сохранено'}
                      </Button>
                    </div>
                    <div className="e-targets">
                      {data.accounts.slice(0, 6).map((a, i) => (
                        <button
                          key={a.id}
                          className="e-target"
                          title={a.name}
                          disabled={!draft}
                          aria-pressed={targets.includes(a.id)}
                          onClick={() =>
                            patch({
                              targets: targets.includes(a.id)
                                ? targets.filter((id) => id !== a.id)
                                : [...targets, a.id],
                            })
                          }
                        >
                          {a.platform === 'youtube' ? 'YT' : 'TT'} {i + 1}
                        </button>
                      ))}
                    </div>
                    {data.accounts.length === 0 ? (
                      <button className="text-link" onClick={() => setView('channels')}>
                        Добавить аккаунты →
                      </button>
                    ) : null}
                    <button
                      className="e-button primary e-primary-action"
                      disabled={!asset || targets.length === 0 || busy}
                      onClick={() => {
                        setAllSources(false);
                        setModal('enqueue');
                      }}
                    >
                      В очередь · {targets.length} вариантов
                    </button>
                  </div>
                </aside>
              </div>
            )}
            {view === 'queue' && (
              <section className="e-content">
                <div className="e-page-head">
                  <div>
                    <h1>Очередь</h1>
                    <p>Готовый файл проверяется перед отправкой на платформу.</p>
                  </div>
                  <div className="e-row">
                    <Button
                      disabled={
                        busy ||
                        !data.jobs.some((j) => j.status === 'ready' && canAutoPublish(j.account))
                      }
                      onClick={() => {
                        setBatchIds(
                          data.jobs
                            .filter((j) => j.status === 'ready' && canAutoPublish(j.account))
                            .map((j) => j.id),
                        );
                        setBatchPage(0);
                        setModal('approve-batch');
                      }}
                    >
                      Разрешить отправку пакета
                    </Button>
                    <Button
                      disabled={!videos.length || busy}
                      onClick={() => {
                        setAllSources(true);
                        setModal('enqueue');
                      }}
                    >
                      <Plus size={13} />
                      Пакет из всех исходников
                    </Button>
                  </div>
                </div>
                <div className="e-summary">
                  {[
                    ['Задания', data.jobs.length],
                    ['Готовые файлы', data.jobs.filter((j) => j.has_output).length],
                    [
                      'Обработаны платформой',
                      data.jobs.filter((j) => j.status === 'published').length,
                    ],
                  ].map(([name, value]) => (
                    <div className="e-stat" key={name}>
                      <span>{name}</span>
                      <strong>{value}</strong>
                    </div>
                  ))}
                </div>
                <div className="e-toolbar">
                  <select
                    aria-label="Фильтр состояния"
                    className="e-field"
                    value={filter}
                    onChange={(e) => {
                      setFilter(e.target.value);
                      setJobPage(0);
                    }}
                  >
                    <option value="all">Все состояния</option>
                    {Object.entries(label).map(([key, name]) => (
                      <option key={key} value={key}>
                        {name}
                      </option>
                    ))}
                  </select>
                  <span className="e-small e-muted">
                    {data.settings.paused ? 'Очередь приостановлена' : 'Очередь работает'}
                  </span>
                </div>
                {!filteredJobs.length ? (
                  <div className="e-empty">
                    <h2>Здесь появятся ваши задания</h2>
                    <p>Добавьте видео и выберите получателей в монтажной.</p>
                    <Button onClick={() => setView('editor')}>Открыть монтажную</Button>
                  </div>
                ) : (
                  <>
                    <table className="e-table">
                      <thead>
                        <tr>
                          <th style={{ width: '29%' }}>Исходник / аккаунт</th>
                          <th style={{ width: '26%' }}>Состояние</th>
                          <th style={{ width: '22%' }}>Время · {data.settings.timezone}</th>
                          <th>Действия</th>
                        </tr>
                      </thead>
                      <tbody>
                        {visibleJobs.map((j) => (
                          <tr key={j.id}>
                            <td>
                              <b className="truncate">{j.asset_name}</b>
                              <small>
                                {j.account.platform} · {j.account.name}
                              </small>
                            </td>
                            <td>
                              <span
                                className={
                                  'e-badge' +
                                  (['failed', 'unknown', 'rejected'].includes(j.status)
                                    ? ' bad'
                                    : j.has_output
                                      ? ' good'
                                      : '')
                                }
                              >
                                {label[j.status] ?? j.status}
                              </span>
                              {j.status === 'rendering' && (
                                <div className="e-progress">
                                  <span style={{ width: `${j.progress}%` }} />
                                </div>
                              )}
                            </td>
                            <td className="e-mono">
                              {j.scheduled
                                ? new Date(j.scheduled).toLocaleString('ru-RU', {
                                    timeZone: data.settings.timezone,
                                    dateStyle: 'short',
                                    timeStyle: 'short',
                                  })
                                : 'После подтверждения'}
                            </td>
                            <td>
                              <Button
                                onClick={() => {
                                  setCurrentJob(j);
                                  setModal('job');
                                }}
                              >
                                Подробнее
                              </Button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    <Pager page={jobPage} set={setJobPage} total={filteredJobs.length} size={4} />
                  </>
                )}
              </section>
            )}
            {view === 'channels' && (
              <section className="e-content">
                <div className="e-page-head">
                  <div>
                    <h1>Каналы</h1>
                    <p>Сначала добавьте аккаунт, затем подключите его.</p>
                  </div>
                  <Button primary onClick={() => setModal('account')}>
                    <Plus size={13} />
                    Добавить аккаунт
                  </Button>
                </div>
                {data.accounts.length ? (
                  <>
                    <div className="e-channel-list">
                      {data.accounts.slice(channelPage * 6, channelPage * 6 + 6).map((a) => (
                        <article key={a.id} className="e-channel">
                          <div className="e-row e-between">
                            <div className="e-row">
                              <span className="e-avatar">
                                {a.platform === 'youtube' ? 'YT' : 'TT'}
                              </span>
                              <div>
                                <h3 title={a.name}>{a.name}</h3>
                                <span className="e-small e-muted">
                                  {a.external_id ?? 'ID ещё не подтверждён'}
                                </span>
                              </div>
                            </div>
                            <span className={'e-badge' + (a.connected ? ' good' : '')}>
                              {a.connected
                                ? a.method === 'browser_login'
                                  ? 'Вход выполнен'
                                  : 'Подключён'
                                : 'Нужен вход'}
                            </span>
                          </div>
                          <div className="e-row e-between">
                            <span className="e-small e-muted">
                              {a.auth === 'waiting'
                                ? 'Ожидаем вход в браузере'
                                : a.auth === 'browser_waiting'
                                  ? 'Ожидаем подключение из вашего Chrome'
                                  : a.auth === 'action:google'
                                    ? 'Google отклонил вход — выберите QR-код TikTok'
                                    : a.auth === 'verifying'
                                      ? 'Проверяем и сохраняем сессию'
                                      : a.auth?.startsWith('confirm:')
                                        ? 'Подтвердите ' + a.auth.slice(8)
                                        : a.auth?.startsWith('error')
                                          ? 'Вход не завершён'
                                          : a.method === 'browser_unverified'
                                            ? 'Сессия импортирована, не проверена'
                                            : a.method === 'browser_login'
                                              ? 'Автопубликация ещё не проверена'
                                              : a.platform}
                            </span>
                            <Button
                              onClick={() => {
                                setCurrentAccount(a);
                                setModal('connection');
                              }}
                            >
                              Подключение
                            </Button>
                          </div>
                        </article>
                      ))}
                    </div>
                    <Pager
                      page={channelPage}
                      set={setChannelPage}
                      total={data.accounts.length}
                      size={6}
                    />
                  </>
                ) : (
                  <div className="e-empty">
                    <Users size={30} />
                    <h2>Аккаунты пока не добавлены</h2>
                    <p>Имена каналов YouTube будут проверены после входа через Google.</p>
                  </div>
                )}
              </section>
            )}
            {view === 'stats' && (
              <section className="e-content stats-view">
                <div className="e-page-head">
                  <div>
                    <h1>Статистика</h1>
                    <p>Последние накопительные счётчики. Это не прирост за период.</p>
                  </div>
                  <div className="e-row">
                    <Button onClick={() => setModal('metric-import')}>Импорт CSV</Button>
                    <Button
                      disabled={!data.jobs.length}
                      onClick={() => {
                        setHistoryJob(data.jobs[0]?.id ?? '');
                        setModal('history');
                      }}
                    >
                      Динамика
                    </Button>
                    <Button
                      disabled={busy}
                      onClick={() =>
                        void run(async () => {
                          const result = await api<{ updated: number; unavailable: number }>(
                            '/metrics/refresh',
                            'POST',
                          );
                          setMetrics(await api('/metrics'));
                          if (result.unavailable)
                            throw new Error(
                              `Обновлено: ${result.updated}. Нет доступа к данным: ${result.unavailable}.`,
                            );
                        }, 'Метрики обновлены')
                      }
                    >
                      Обновить метрики
                    </Button>
                  </div>
                </div>
                <div className="e-toolbar">
                  <select
                    className="e-field"
                    aria-label="Вид статистики"
                    value={statsMode}
                    onChange={(e) => {
                      setStatsMode(e.target.value);
                      setMetricPage(0);
                    }}
                  >
                    <option value="videos">Ролики</option>
                    <option value="channels">Сводка каналов</option>
                  </select>
                  <select
                    className="e-field"
                    aria-label="Платформа статистики"
                    value={statsPlatform}
                    onChange={(e) => {
                      setStatsPlatform(e.target.value);
                      setStatsAccount('all');
                      setMetricPage(0);
                    }}
                  >
                    <option value="all">Все платформы</option>
                    <option value="youtube">YouTube</option>
                    <option value="tiktok">TikTok</option>
                  </select>
                  <select
                    className="e-field"
                    aria-label="Канал статистики"
                    value={statsAccount}
                    onChange={(e) => {
                      setStatsAccount(e.target.value);
                      setMetricPage(0);
                    }}
                  >
                    <option value="all">Все аккаунты</option>
                    {data.accounts
                      .filter((a) => statsPlatform === 'all' || a.platform === statsPlatform)
                      .map((a) => (
                        <option key={a.id} value={a.id}>
                          {a.platform} · {a.name}
                        </option>
                      ))}
                  </select>
                  {statsMode === 'channels' && (
                    <Button
                      disabled={!summaries.length}
                      onClick={() => {
                        const url = URL.createObjectURL(
                          new Blob([channelsReport(summaries)], { type: 'text/csv;charset=utf-8' }),
                        );
                        const link = document.createElement('a');
                        link.href = url;
                        link.download = 'survivalflow-channels.csv';
                        link.click();
                        setTimeout(() => URL.revokeObjectURL(url), 1000);
                      }}
                    >
                      <Download size={13} /> Экспорт CSV
                    </Button>
                  )}
                </div>
                {statsMode === 'channels' && summaries.length ? (
                  <>
                    <table className="e-table" aria-label="Сводка каналов">
                      <thead>
                        <tr>
                          <th style={{ width: '32%' }}>Канал / охват данных</th>
                          <th>Просмотры</th>
                          <th>Лайки</th>
                          <th>Комментарии</th>
                          <th style={{ width: '23%' }}>Свежий снимок</th>
                        </tr>
                      </thead>
                      <tbody>
                        {summaries.slice(metricPage * 4, metricPage * 4 + 4).map((row) => (
                          <tr key={row.account.id}>
                            <td>
                              <b className="truncate" title={row.account.name}>
                                {row.account.name}
                              </b>
                              <small>
                                {row.account.platform} · Снимки: {row.measured} из {row.jobs} задач
                              </small>
                              <small className="truncate" title={row.sources.join(', ')}>
                                {row.sources.join(', ') || 'Нет снимков'}
                              </small>
                            </td>
                            {(['views', 'likes', 'comments'] as const).map((key) => (
                              <td key={key}>
                                {row[key].value?.toLocaleString('ru-RU') ?? '—'}
                                <small>
                                  {row[key].covered} из {row.jobs} задач
                                </small>
                              </td>
                            ))}
                            <td>
                              {row.captured
                                ? new Date(row.captured).toLocaleString('ru-RU', {
                                    timeZone: data.settings.timezone,
                                  })
                                : '—'}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    <p className="stats-note">
                      Сумма известных значений; без данных — «—». Платформы не суммируются. Даты и
                      возраст роликов могут различаться.
                    </p>
                    <Pager
                      page={metricPage}
                      set={setMetricPage}
                      total={summaries.length}
                      size={4}
                    />
                  </>
                ) : !metricRows.length ? (
                  <div className="e-empty">
                    <ChartNoAxesCombined size={32} />
                    <h2>Данных ещё нет</h2>
                    <p>
                      Подключите канал и подтвердите публикацию. Затем здесь появятся реальные
                      метрики.
                    </p>
                  </div>
                ) : (
                  <>
                    <table className="e-table">
                      <thead>
                        <tr>
                          <th style={{ width: '35%' }}>Ролик / аккаунт</th>
                          <th>Просмотры</th>
                          <th>Лайки</th>
                          <th>Комментарии</th>
                          <th style={{ width: '25%' }}>Обновлено</th>
                        </tr>
                      </thead>
                      <tbody>
                        {metricRows.slice(metricPage * 6, metricPage * 6 + 6).map((m) => (
                          <tr key={m.id}>
                            <td>
                              <b
                                className="truncate"
                                title={data.jobs.find((j) => j.id === m.job_id)?.account.name}
                              >
                                {data.jobs.find((j) => j.id === m.job_id)?.asset_name ?? m.job_id} ·{' '}
                                {data.jobs.find((j) => j.id === m.job_id)?.account.name}
                              </b>
                              <small>
                                {data.jobs.find((j) => j.id === m.job_id)?.account.platform} ·{' '}
                                {m.source}
                                {jobsById.get(m.job_id)?.url && (
                                  <>
                                    {' '}
                                    ·{' '}
                                    <a
                                      href={jobsById.get(m.job_id)!.url!}
                                      target="_blank"
                                      rel="noreferrer"
                                    >
                                      Открыть ролик
                                    </a>
                                  </>
                                )}
                              </small>
                            </td>
                            <td>{m.values.views?.toLocaleString('ru-RU') ?? '—'}</td>
                            <td>{m.values.likes?.toLocaleString('ru-RU') ?? '—'}</td>
                            <td>{m.values.comments?.toLocaleString('ru-RU') ?? '—'}</td>
                            <td>
                              {new Date(m.captured).toLocaleString('ru-RU', {
                                timeZone: data.settings.timezone,
                              })}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    <Pager
                      page={metricPage}
                      set={setMetricPage}
                      total={metricRows.length}
                      size={6}
                    />
                  </>
                )}
              </section>
            )}
            {view === 'settings' && (
              <section className="e-content">
                <div className="e-page-head">
                  <div>
                    <h1>Настройки</h1>
                    <p>Данные хранятся на этом компьютере. Секреты защищены Windows DPAPI.</p>
                  </div>
                  <label className="e-check">
                    <input
                      type="checkbox"
                      checked={!!data.settings.buba_required}
                      onChange={(e) =>
                        void run(
                          () => api('/settings', 'PATCH', { buba_required: e.target.checked }),
                          'Правила отправки обновлены',
                        )
                      }
                    />
                    Блокировать нарушения БУБА
                  </label>
                </div>
                <div className="e-settings">
                  <Field label="Название пакета">
                    <input
                      className="e-field"
                      defaultValue={data.settings.project_name}
                      maxLength={80}
                      onBlur={(e) => {
                        if (e.target.value !== data.settings.project_name)
                          void run(() =>
                            api('/settings', 'PATCH', { project_name: e.target.value }),
                          );
                      }}
                    />
                  </Field>
                  <Field label="Часовой пояс">
                    <select
                      className="e-field"
                      value={data.settings.timezone}
                      onChange={(e) =>
                        void run(() => api('/settings', 'PATCH', { timezone: e.target.value }))
                      }
                    >
                      <option>Europe/Moscow</option>
                      <option>UTC</option>
                    </select>
                  </Field>
                  <Field label="Качество экспорта">
                    <select
                      className="e-field"
                      value={data.settings.quality}
                      onChange={(e) =>
                        void run(() => api('/settings', 'PATCH', { quality: e.target.value }))
                      }
                    >
                      <option value="high">Высокое · CRF 20</option>
                      <option value="balanced">Сбалансированное · CRF 23</option>
                    </select>
                  </Field>
                  <Field label="Интервал публикаций на аккаунт, мин">
                    <input
                      className="e-field"
                      type="number"
                      min="0"
                      max="180"
                      defaultValue={data.settings.interval}
                      onBlur={(e) =>
                        void run(() =>
                          api('/settings', 'PATCH', { interval: Number(e.target.value) }),
                        )
                      }
                    />
                  </Field>
                  <h3>Подключение Google</h3>
                  <p className="e-muted e-small">
                    OAuth Desktop app · YouTube Data API v3 · добавьте свой аккаунт в тестовые
                    пользователи Google Cloud.
                  </p>
                  <div className="e-row">
                    <Button disabled={busy} onClick={() => clientRef.current?.click()}>
                      <Upload size={13} />
                      Импорт OAuth JSON
                    </Button>
                    <span className={'e-badge' + (data.google_configured ? ' good' : '')}>
                      {data.google_configured ? 'Конфигурация сохранена' : 'Не настроено'}
                    </span>
                  </div>
                  <hr className="e-divider" />
                  <h3>Локальные данные</h3>
                  <p className="e-small e-muted">
                    Исходники, экспорты и база — в папке data рядом с программой. Резервная копия
                    содержит базу; видео и защищённые секреты копируются отдельно.
                  </p>
                  <div className="e-row">
                    <Button
                      disabled={busy}
                      onClick={() =>
                        void run(async () => {
                          const result = await api<{ path: string }>('/backup', 'POST');
                          setMessage('Резервная копия: ' + result.path);
                          setError(false);
                        })
                      }
                    >
                      <Save size={13} />
                      Копия базы
                    </Button>
                    <Button
                      disabled={busy}
                      onClick={() =>
                        void run(async () => {
                          const result = await api<{ ok: boolean; free_gb: number }>('/health');
                          if (!result.ok)
                            throw new Error('Не найдены FFmpeg или ffprobe. Выполните установку.');
                          setMessage(`FFmpeg / ffprobe доступны. Свободно ${result.free_gb} ГБ.`);
                          setError(false);
                        })
                      }
                    >
                      Проверить окружение
                    </Button>
                    <Button onClick={() => setModal('cleanup')}>Очистка экспортов</Button>
                  </div>
                </div>
              </section>
            )}
          </main>
        </div>
        <footer className="e-bottom">
          {busy
            ? 'Выполняется действие…'
            : `${!data.worker ? 'Рабочий процесс не запущен' : data.settings.paused ? 'Пауза' : 'Очередь запущена'} · ${videos.length} исходников · ${data.accounts.length} аккаунтов · ${data.jobs.length} заданий · ${dirty ? 'Есть несохранённые изменения' : 'Настройки сохранены'}`}
        </footer>
        {(message || connectionLost) && !modal && (
          <div
            className={'toast' + (error || connectionLost ? ' error' : '')}
            role={error || connectionLost ? 'alert' : 'status'}
          >
            <span>
              {connectionLost
                ? 'Нет связи с локальным сервером. Ждём восстановления соединения…'
                : message}
            </span>
            <button aria-label="Скрыть сообщение" onClick={() => setMessage('')}>
              <X size={15} />
            </button>
          </div>
        )}
        {modal === 'banner' && draft && (
          <Modal
            busy={busy}
            error={error ? message : undefined}
            title="Параметры баннера"
            onClose={close}
          >
            <Field label="Текст встроенного баннера">
              <textarea
                className="e-field"
                maxLength={120}
                rows={3}
                value={draft.banner_text}
                onChange={(e) => patch({ banner_text: e.target.value })}
              />
            </Field>
            {slider('opacity', 'Непрозрачность, %', 0, 100)}
            {slider('x', 'Позиция по горизонтали, %', 0, 100)}
            <label className="e-row">
              <input
                type="checkbox"
                checked={!!draft.freeze_video}
                onChange={(e) => {
                  video.current?.pause();
                  setPlaying(false);
                  setTime(0);
                  if (video.current) video.current.currentTime = 0;
                  patch({ freeze_video: e.target.checked });
                }}
              />
              Стоп-кадр на время баннера
            </label>
            <p>
              Видео и его звук приостанавливаются, затем продолжаются с того же места. Длительность
              ролика увеличивается на время баннера.
            </p>
            <Field label="После конца анимации">
              <select
                className="e-field"
                value={draft.banner_end}
                onChange={(e) => patch({ banner_end: e.target.value as Edit['banner_end'] })}
              >
                <option value="hold">Удерживать последний кадр</option>
                <option value="loop">Повторять анимацию</option>
              </select>
            </Field>
            <p>
              Анимация запускается с первого кадра в момент появления баннера. Точный результат
              проверьте в экспортированном MP4.
            </p>
            <div className="e-row">
              <Button primary onClick={close}>
                Готово
              </Button>
            </div>
          </Modal>
        )}
        {modal === 'metric-import' && (
          <Modal
            busy={busy}
            error={error ? message : undefined}
            title="Импорт статистики"
            onClose={close}
          >
            <p>
              Скачайте шаблон со своими заданиями. Заполните views, likes, comments, shares
              (ненужные поля оставьте пустыми). captured — время снимка с часовым поясом. Файл: CSV
              UTF-8 с запятыми.
            </p>
            <p>
              Источник будет указан как «Импорт CSV пользователя». Импорт не меняет статус
              публикации. Повтор строки с тем же заданием и временем обновляет её данные.
            </p>
            <div className="e-row">
              <a className="e-button" href="/api/metrics/template">
                Скачать шаблон
              </a>
              <Button primary disabled={busy} onClick={() => metricFile.current?.click()}>
                Выбрать CSV
              </Button>
            </div>
          </Modal>
        )}
        {modal === 'history' && (
          <Modal
            busy={busy}
            error={error ? message : undefined}
            title="Динамика ролика"
            onClose={close}
          >
            <Field label="Ролик / аккаунт">
              <select
                className="e-field"
                value={historyJob}
                onChange={(e) => setHistoryJob(e.target.value)}
              >
                {data.jobs.map((j) => (
                  <option key={j.id} value={j.id}>
                    {j.asset_name} · {j.account.name}
                  </option>
                ))}
              </select>
            </Field>
            <History rows={metrics.filter((m) => m.job_id === historyJob)} />
            <Button onClick={close}>Закрыть</Button>
          </Modal>
        )}
        {modal === 'reconcile' && currentJob && (
          <Modal
            busy={busy}
            error={error ? message : undefined}
            title="Проверить результат отправки"
            onClose={close}
          >
            <p>
              Найдите отправленный ролик в YouTube Studio, проверьте содержимое и скопируйте
              11-символьный ID. Программа сверит канал и название через API; повторной отправки не
              будет.
            </p>
            <Field label="ID YouTube-видео">
              <input
                className="e-field"
                value={externalId}
                maxLength={11}
                onChange={(e) => setExternalId(e.target.value)}
              />
            </Field>
            <Button
              primary
              disabled={busy || externalId.length !== 11}
              onClick={() =>
                void run(async () => {
                  await api(`/jobs/${currentJob.id}/action`, 'POST', {
                    action: 'reconcile',
                    external_id: externalId,
                  });
                  setModal('');
                  setExternalId('');
                }, 'Ролик найден в выбранном канале; проверяем обработку')
              }
            >
              Сверить через API
            </Button>
          </Modal>
        )}
        {modal === 'cleanup' && (
          <Modal
            busy={busy}
            error={error ? message : undefined}
            title="Очистка экспортов"
            onClose={close}
          >
            <p>
              Удаляются только MP4 завершённых или отменённых заданий старше{' '}
              {data.settings.retention_days} дней. Исходники и экспорты с неизвестным результатом
              отправки сохраняются.
            </p>
            <Field label="Хранить экспорты, дней">
              <input
                className="e-field"
                type="number"
                min="1"
                max="365"
                defaultValue={data.settings.retention_days}
                onBlur={(e) =>
                  void run(() =>
                    api('/settings', 'PATCH', { retention_days: Number(e.target.value) }),
                  )
                }
              />
            </Field>
            <Button
              disabled={busy}
              onClick={() =>
                void run(async () => {
                  const result = await api<{ deleted: number }>('/cleanup', 'POST');
                  setModal('');
                  setMessage(`Удалено экспортов: ${result.deleted}`);
                  setError(false);
                })
              }
            >
              Очистить старые экспорты
            </Button>
          </Modal>
        )}
        {modal === 'buba' && draft && asset && (
          <Modal title="Правила БУБА" onClose={close}>
            <CampaignPanel
              asset={asset}
              draft={draft}
              patch={patch}
              banners={banners}
              advanced={() => setModal('banner')}
            />
          </Modal>
        )}
        {modal === 'job-campaign' && currentJob && (
          <Modal title="БУБА · контроль ролика" onClose={close}>
            <JobCampaignPanel job={currentJob} />
          </Modal>
        )}
        {modal === 'disclosures' && draft && (
          <Modal
            busy={busy}
            error={error ? message : undefined}
            title="Сведения о содержимом"
            onClose={close}
          >
            <label className="e-check">
              <input
                type="checkbox"
                checked={draft.synthetic}
                onChange={(e) => patch({ synthetic: e.target.checked })}
              />
              Реалистичное изменённое или синтетическое содержимое
            </label>
            <label className="e-check">
              <input
                type="checkbox"
                checked={draft.made_for_kids}
                onChange={(e) => patch({ made_for_kids: e.target.checked })}
              />
              Видео предназначено для детей (YouTube)
            </label>
            <p>Указывайте свойства самого ролика. Эти настройки сохраняются вместе с вариантом.</p>
            <Button primary onClick={close}>
              Готово
            </Button>
          </Modal>
        )}
        {modal === 'targets' && draft && (
          <Modal busy={busy} error={error ? message : undefined} title="Получатели" onClose={close}>
            <div className="target-dialog">
              {data.accounts.slice(targetPage * 6, targetPage * 6 + 6).map((a) => (
                <label className="e-check" key={a.id}>
                  <input
                    type="checkbox"
                    checked={targets.includes(a.id)}
                    onChange={() =>
                      patch({
                        targets: targets.includes(a.id)
                          ? targets.filter((id) => id !== a.id)
                          : [...targets, a.id],
                      })
                    }
                  />
                  {a.platform} · {a.name}
                </label>
              ))}
            </div>
            <Pager page={targetPage} set={setTargetPage} total={data.accounts.length} size={6} />
            <Button primary onClick={close}>
              Готово
            </Button>
          </Modal>
        )}
        {modal === 'enqueue' && (
          <Modal
            busy={busy}
            error={error ? message : undefined}
            title="Создать варианты"
            onClose={close}
          >
            <p>
              {allSources ? videos.length : 1} исходник(а) × {targets.length} аккаунт(ов) ={' '}
              {(allSources ? videos.length : 1) * targets.length} вариантов. Применяются сохранённые
              настройки каждого исходника; получатели выбраны в монтажной.
            </p>
            <Field label={`Время отправки · ${data.settings.timezone} (необязательно)`}>
              <input
                className="e-field"
                type="datetime-local"
                value={schedule}
                onChange={(e) => setSchedule(e.target.value)}
              />
            </Field>
            <p>
              Сначала программа создаст MP4. После просмотра нужно отдельно подтвердить материал,
              аккаунт и видимость. Пока подтверждения нет, публикация не начнётся.
            </p>
            <div className="e-row">
              <Button disabled={busy} onClick={close}>
                Отмена
              </Button>
              <Button
                primary
                disabled={busy || !targets.length}
                onClick={() =>
                  void run(async () => {
                    await save();
                    const result = await api<{ created: string[]; existing: string[] }>(
                      '/queue',
                      'POST',
                      {
                        asset_ids: allSources ? videos.map((a) => a.id) : [selected],
                        account_ids: targets,
                        scheduled: schedule
                          ? new Date(
                              schedule + (data.settings.timezone === 'UTC' ? 'Z' : '+03:00'),
                            ).toISOString()
                          : null,
                      },
                    );
                    setModal('');
                    setView('queue');
                    if (!result.created.length)
                      throw new Error(
                        'Такие варианты уже есть в очереди. Измените настройки для нового варианта.',
                      );
                  }, 'Варианты добавлены в очередь')
                }
              >
                Создать варианты
              </Button>
            </div>
          </Modal>
        )}
        {modal === 'account' && (
          <Modal
            busy={busy}
            error={error ? message : undefined}
            title="Добавить аккаунт"
            onClose={close}
          >
            <Field label="Платформа">
              <select
                className="e-field"
                value={platform}
                onChange={(e) => setPlatform(e.target.value)}
              >
                <option value="youtube">YouTube</option>
                <option value="tiktok">TikTok</option>
              </select>
            </Field>
            <Field label="Название для вас">
              <input
                autoFocus
                className="e-field"
                value={accountName}
                maxLength={80}
                onChange={(e) => setAccountName(e.target.value)}
              />
            </Field>
            <p>
              Добавление создаёт запись. Подключение и проверка доступа выполняются следующим шагом.
            </p>
            <div className="e-row">
              <Button
                primary
                disabled={busy || !accountName.trim()}
                onClick={() =>
                  void run(async () => {
                    await api('/accounts', 'POST', { platform, name: accountName.trim() });
                    setAccountName('');
                    setModal('');
                  }, 'Аккаунт добавлен')
                }
              >
                Добавить
              </Button>
            </div>
          </Modal>
        )}
        {modal === 'connection' && currentAccount && (
          <Modal
            busy={busy}
            error={error ? message : undefined}
            title={currentAccount.name}
            onClose={close}
          >
            {currentAccount.connected && (
              <p>
                Подключён: {currentAccount.name} · {currentAccount.external_id}
              </p>
            )}
            {currentAccount.auth?.startsWith('error:') && (
              <p className="e-modal-error" role="alert">
                {currentAccount.auth.slice(6)}
              </p>
            )}
            {currentAccount.platform === 'youtube' ? (
              <>
                <p>
                  Вход откроется в браузере. Выберите нужный Google-аккаунт и YouTube-канал. Имя и
                  ID будут получены от Google.
                </p>
                {!data.google_configured && (
                  <p>Сначала импортируйте OAuth Desktop JSON в настройках.</p>
                )}
                <Button
                  primary
                  disabled={busy || !data.google_configured || authPending(currentAccount.auth)}
                  onClick={() =>
                    void run(
                      () => api(`/accounts/${currentAccount.id}/connect`, 'POST'),
                      'Завершите вход в открывшемся браузере',
                    )
                  }
                >
                  Войти через Google
                </Button>
              </>
            ) : (
              <>
                {currentAccount.auth === 'browser_waiting' ? (
                  <>
                    <p>
                      Открыт обычный Chrome. Войдите в нужный TikTok и откройте «Профиль». Нажмите
                      расширение SurvivalFlow в Chrome и вставьте этот код.
                    </p>
                    <Field label="Код подключения Chrome">
                      <input
                        className="e-field e-mono"
                        readOnly
                        value={browserPairing?.code ?? 'Загрузка…'}
                        onFocus={(e) => e.target.select()}
                      />
                    </Field>
                    <div className="e-row">
                      <Button
                        disabled={!browserPairing}
                        onClick={() =>
                          void run(async () => {
                            await navigator.clipboard.writeText(browserPairing!.code);
                          }, 'Код скопирован')
                        }
                      >
                        Скопировать код
                      </Button>
                      <Button
                        disabled={busy}
                        onClick={() => void run(() => api('/browser-extension/folder', 'POST'))}
                      >
                        Папка расширения
                      </Button>
                    </div>
                    <p className="e-muted e-small">
                      Расширение устанавливается один раз через chrome://extensions → «Режим
                      разработчика» → «Загрузить распакованное». Выберите открытую папку
                      browser-extension. Код действует 10 минут.
                    </p>
                  </>
                ) : currentAccount.auth === 'action:google' ? (
                  <>
                    <p role="alert">
                      Google не разрешил вход из управляемого браузера. Вернитесь на TikTok и
                      выберите QR-код либо телефон/почту, привязанные к этому аккаунту.
                    </p>
                    <p className="e-small">
                      Если входите только через Google, сначала войдите в официальное приложение
                      TikTok на телефоне, затем используйте QR-код здесь.
                    </p>
                    <Button
                      primary
                      disabled={busy}
                      onClick={() =>
                        void run(
                          () =>
                            api(`/accounts/${currentAccount.id}/connect/return-to-tiktok`, 'POST'),
                          'Возвращаем окно на страницу входа TikTok',
                        )
                      }
                    >
                      Вернуться к входу в TikTok
                    </Button>
                  </>
                ) : currentAccount.auth?.startsWith('confirm:@') ? (
                  <>
                    <p>
                      Найден аккаунт <strong>{currentAccount.auth.slice(8)}</strong>. Убедитесь, что
                      это нужный профиль, и сохраните подключение.
                    </p>
                    <Button
                      primary
                      disabled={busy}
                      onClick={() =>
                        void run(
                          () =>
                            api(`/accounts/${currentAccount.id}/connect/confirm`, 'POST', {
                              handle: currentAccount.auth!.slice(9),
                            }),
                          'Проверяем аккаунт и сохраняем сессию',
                        )
                      }
                    >
                      Сохранить {currentAccount.auth.slice(8)}
                    </Button>
                  </>
                ) : currentAccount.auth === 'verifying' ? (
                  <p role="status">Проверяем аккаунт и сохраняем сессию…</p>
                ) : (
                  <>
                    <p>
                      {currentAccount.auth === 'waiting'
                        ? 'Войдите в TikTok через QR-код либо привязанные телефон/почту. После входа откройте «Профиль», вернитесь сюда и подтвердите аккаунт. Окно браузера пока не закрывайте. Вход через Google в этом окне может быть заблокирован.'
                        : 'TikTok откроется вкладкой в вашем обычном Chrome с привычной авторизацией. Для передачи сессии в SurvivalFlow используется небольшое расширение — установка один раз.'}
                    </p>
                    <Button
                      primary
                      disabled={busy || authPending(currentAccount.auth)}
                      onClick={() =>
                        void run(
                          () => api(`/accounts/${currentAccount.id}/connect`, 'POST'),
                          'Завершите вход в окне TikTok и вернитесь в SurvivalFlow',
                        )
                      }
                    >
                      {currentAccount.auth === 'waiting'
                        ? 'Ожидаем вход…'
                        : currentAccount.connected
                          ? 'Войти заново в TikTok'
                          : 'Открыть TikTok в моём Chrome'}
                    </Button>
                  </>
                )}
                <p className="e-muted e-small">
                  Сессия хранится локально в зашифрованном виде. Подключение аккаунта не запускает
                  публикации. Автопубликация TikTok ещё требует отдельной проверки.
                </p>
                {!authPending(currentAccount.auth) && (
                  <details>
                    <summary>Другие способы входа</summary>
                    <Button
                      disabled={busy}
                      onClick={() =>
                        void run(
                          () => api(`/accounts/${currentAccount.id}/connect/isolated`, 'POST'),
                          'Завершите вход в отдельном окне',
                        )
                      }
                    >
                      Отдельное окно входа
                    </Button>
                    <p className="e-small">
                      Локальный JSON: handle — имя аккаунта, cookies — массив cookies TikTok. Импорт
                      не подтверждает аккаунт.
                    </p>
                    <Button disabled={busy} onClick={() => sessionRef.current?.click()}>
                      Импортировать сессию
                    </Button>
                  </details>
                )}
              </>
            )}
            <div className="e-row">
              {authPending(currentAccount.auth) && (
                <Button
                  disabled={busy}
                  onClick={() =>
                    void run(
                      () => api(`/accounts/${currentAccount.id}/connect/cancel`, 'POST'),
                      'Вход отменён',
                    )
                  }
                >
                  Отменить вход
                </Button>
              )}
              <Button
                disabled={busy}
                onClick={() =>
                  void run(
                    () => api(`/accounts/${currentAccount.id}/disconnect`, 'POST'),
                    'Локальный доступ отключён',
                  )
                }
              >
                Отключить
              </Button>
              <Button onClick={close}>Закрыть</Button>
            </div>
          </Modal>
        )}
        {modal === 'approve-batch' && (
          <Modal
            busy={busy}
            error={error ? message : undefined}
            title="Подтвердить отправку пакета"
            onClose={close}
          >
            <p>
              Подтверждение для {batchIds.length} готовых вариантов. Проверьте файлы и получателей;
              видимость указана у каждого.
            </p>
            <div className="batch-list">
              {batchIds.slice(batchPage * 4, batchPage * 4 + 4).map((id) => {
                const j = data.jobs.find((item) => item.id === id);
                return j ? (
                  <div className="batch-item" key={id}>
                    <b>{j.asset_name}</b>
                    <small>
                      {j.account.platform} · {j.account.name} · {visibility[j.config.visibility]}
                    </small>
                    <a
                      className="text-link"
                      href={`/api/jobs/${j.id}/file`}
                      target="_blank"
                      rel="noreferrer"
                    >
                      Скачать MP4
                    </a>
                  </div>
                ) : null;
              })}
            </div>
            <Pager page={batchPage} set={setBatchPage} total={batchIds.length} size={4} />
            <div className="e-row">
              <Button disabled={busy} onClick={close}>
                Отмена
              </Button>
              <Button
                primary
                disabled={busy || !batchIds.length}
                onClick={() =>
                  void run(async () => {
                    await api('/jobs/approve-batch', 'POST', { job_ids: batchIds });
                    setModal('');
                  }, 'Пакет подтверждён. Отправки начнутся при доступе к аккаунтам и запущенной очереди')
                }
              >
                Разрешить {batchIds.length} отправок
              </Button>
            </div>
          </Modal>
        )}
        {modal === 'archive' && archiveCandidate && (
          <Modal
            busy={busy}
            error={error ? message : undefined}
            title="Убрать исходник из монтажной"
            onClose={close}
          >
            <p className="file-name" title={archiveCandidate.name}>
              {archiveCandidate.name}
            </p>
            <p>
              Исходный файл и уже созданные задания сохранятся. Видео исчезнет из списка монтажной и
              новых пакетов.
            </p>
            <div className="e-row">
              <Button disabled={busy} onClick={close}>
                Отмена
              </Button>
              <Button
                primary
                disabled={busy}
                onClick={() =>
                  void run(async () => {
                    await api(`/assets/${archiveCandidate.id}`, 'DELETE');
                    setModal('');
                  }, 'Исходник убран из монтажной; файлы и задания сохранены')
                }
              >
                Убрать из списка
              </Button>
            </div>
          </Modal>
        )}
        {modal === 'export-preview' && currentJob && (
          <Modal
            busy={busy}
            error={error ? message : undefined}
            title="Готовый MP4"
            onClose={close}
          >
            <p className="file-name" title={currentJob.asset_name}>
              {currentJob.asset_name} · {currentJob.account.name}
            </p>
            <video
              className="export-player"
              src={`/api/jobs/${currentJob.id}/file?download=false`}
              controls
              playsInline
            />
            <p>Это экспортированный файл с точными фильтрами и таймингом баннера.</p>
            <div className="e-row">
              <Button onClick={() => setModal('job')}>К заданию</Button>
              <Button primary onClick={close}>
                Готово
              </Button>
            </div>
          </Modal>
        )}
        {modal === 'job' && currentJob && (
          <Modal busy={busy} error={error ? message : undefined} title="Задание" onClose={close}>
            <p>
              <b>{currentJob.asset_name}</b>
              <br />
              {currentJob.account.platform} · {currentJob.account.name}
              <br />
              Видимость: {visibility[currentJob.config.visibility]}
              <br />
              Состояние:{' '}
              {label[data.jobs.find((j) => j.id === currentJob.id)?.status ?? currentJob.status]}
            </p>
            {currentJob.error && <p className="job-error">{currentJob.error}</p>}
            {currentJob.has_output && (
              <p>
                <Button onClick={() => setModal('export-preview')}>Посмотреть MP4</Button>{' '}
                <a
                  className="e-button"
                  href={`/api/jobs/${currentJob.id}/file`}
                  target="_blank"
                  rel="noreferrer"
                >
                  <Download size={13} />
                  Скачать и проверить MP4
                </a>
              </p>
            )}
            {currentJob.url && (
              <p>
                <a className="text-link" href={currentJob.url} target="_blank" rel="noreferrer">
                  Открыть ролик на платформе ↗
                </a>
              </p>
            )}
            <div className="e-row">
              {currentJob.account.platform === 'tiktok' &&
                ['unknown', 'platform_processing'].includes(currentJob.status) && (
                  <Button
                    disabled={busy}
                    onClick={() =>
                      void run(async () => {
                        await api(`/jobs/${currentJob.id}/tiktok-result`, 'POST');
                      }, 'Результат проверен через TikTok Studio; повторной отправки не было')
                    }
                  >
                    Проверить результат TikTok
                  </Button>
                )}
              {currentJob.status === 'unknown' && currentJob.account.platform === 'youtube' && (
                <Button onClick={() => setModal('reconcile')}>Сверить ID ролика</Button>
              )}
              <Button onClick={() => setModal('job-campaign')}>БУБА и комментарии</Button>
              {['ready', 'waiting_auth', 'missed', 'scheduled'].includes(currentJob.status) && (
                <Button primary disabled={busy} onClick={() => setModal('approve')}>
                  {canAutoPublish(currentJob.account)
                    ? 'Подтвердить отправку'
                    : 'Проверить и отправить в TikTok'}
                </Button>
              )}
              {['failed', 'cancelled'].includes(currentJob.status) && (
                <Button
                  disabled={busy}
                  onClick={() =>
                    void run(async () => {
                      await api(`/jobs/${currentJob.id}/action`, 'POST', { action: 'retry' });
                      setModal('');
                    }, 'Обработка поставлена в очередь')
                  }
                >
                  Повторить обработку
                </Button>
              )}
              {[
                'queued',
                'rendering',
                'ready',
                'waiting_auth',
                'scheduled',
                'missed',
                'failed',
              ].includes(currentJob.status) && (
                <Button
                  disabled={busy}
                  onClick={() =>
                    void run(async () => {
                      await api(`/jobs/${currentJob.id}/action`, 'POST', { action: 'cancel' });
                      setModal('');
                    }, 'Задание отменено')
                  }
                >
                  Отменить задание
                </Button>
              )}
              <Button onClick={close}>Закрыть</Button>
            </div>
          </Modal>
        )}
        {modal === 'approve' && currentJob && (
          <Modal
            busy={busy}
            error={error ? message : undefined}
            title={
              canAutoPublish(currentJob.account)
                ? 'Подтвердить публикацию'
                : 'Первая автоматическая отправка TikTok'
            }
            onClose={close}
          >
            <p>
              Материал: <b>{currentJob.asset_name}</b>
              <br />
              Аккаунт: <b>{currentJob.account.name}</b> ({currentJob.account.platform})<br />
              Видимость: <b>{visibility[currentJob.config.visibility]}</b>
            </p>
            {!canAutoPublish(currentJob.account) ? (
              <>
                <p>
                  Откроется служебное окно Chrome с сохранённой сессией. Программа проверит аккаунт,
                  загрузит MP4, выставит видимость и нажмёт публикацию один раз.
                </p>
                <p>
                  Это настоящая отправка указанного материала в указанный аккаунт сейчас. При потере
                  ответа повтор заблокируется до проверки профиля.
                </p>
                <label className="e-check">
                  <input
                    type="checkbox"
                    checked={testConsent}
                    onChange={(e) => setTestConsent(e.target.checked)}
                  />
                  Разрешаю тестовую публикацию с указанной видимостью
                </label>
                <div className="e-row">
                  <Button
                    primary
                    disabled={
                      busy ||
                      !testConsent ||
                      !currentJob.account.connected ||
                      !currentJob.has_output ||
                      currentJob.config.visibility === 'unlisted'
                    }
                    onClick={() =>
                      void run(async () => {
                        await api(`/jobs/${currentJob.id}/action`, 'POST', {
                          action: 'test_upload',
                        });
                        setModal('');
                      }, 'Тестовая отправка подтверждена. Очередь должна быть запущена.')
                    }
                  >
                    Проверить и отправить сейчас
                  </Button>
                </div>
                {!currentJob.account.connected && (
                  <p>Сначала переподключите TikTok через раздел «Каналы».</p>
                )}
                {currentJob.has_output && (
                  <a
                    className="e-button primary"
                    href={`/api/jobs/${currentJob.id}/file`}
                    target="_blank"
                    rel="noreferrer"
                  >
                    <Download size={13} /> Скачать готовый MP4
                  </a>
                )}
                <div className="e-row">
                  <Button onClick={close}>Закрыть</Button>
                </div>
              </>
            ) : (
              <>
                <p>
                  {currentJob.scheduled
                    ? 'Указанное время: ' +
                      new Date(currentJob.scheduled).toLocaleString('ru-RU', {
                        timeZone: data.settings.timezone,
                      })
                    : 'Отправка начнётся после подтверждения и подключения аккаунта.'}
                </p>
                <p>
                  Подтверждайте после просмотра готового MP4. Очередь должна быть запущена,
                  компьютер — включён.
                </p>
                <div className="e-row">
                  <Button disabled={busy} onClick={close}>
                    Отмена
                  </Button>
                  <Button
                    primary
                    disabled={busy}
                    onClick={() =>
                      void run(async () => {
                        await api(`/jobs/${currentJob.id}/action`, 'POST', {
                          action: currentJob.status === 'missed' ? 'resume' : 'approve',
                        });
                        setModal('');
                      }, 'Отправка подтверждена; её состояние отображается в очереди')
                    }
                  >
                    <Check size={13} />
                    Разрешить отправку
                  </Button>
                </div>
              </>
            )}
          </Modal>
        )}
      </div>
    </div>
  );
}
