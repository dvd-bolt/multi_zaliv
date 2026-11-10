import { useEffect, useState, type ReactNode } from 'react';
import { Check, ExternalLink, Play, Trash2, X } from 'lucide-react';
import {
  api,
  type Account,
  type Asset,
  type BestTime,
  type Edit,
  type Job,
  type State,
} from './api';

export type Plan = {
  step: number;
  publish: boolean;
  scheduled: string | null;
  schedules: Record<string, string>;
  comment: string | null;
  comment_pin: boolean;
  tiktok_sound: string | null;
  continue_after_first: boolean;
  buba_confirmed: boolean;
};
type Deferred = { asset_ids: string[]; state: string; detail?: string };
export type ReleaseData = {
  id: string;
  status: 'draft' | 'started';
  asset_ids: string[];
  account_ids: string[];
  plan: Plan;
  jobs: { job_ids?: string[]; first?: Record<string, string>; deferred?: Record<string, Deferred> };
  updated: string;
};
type Run = (task: () => Promise<unknown>, success?: string) => Promise<void>;

const VISIBILITY: Record<string, string> = {
  public: 'Открытый доступ',
  private: 'Только я',
  unlisted: 'По ссылке',
};
const STEPS = ['Ролики', 'Каналы и время', 'Перед отправкой'];
const ROWS = 4;
const TABLE = 6;

export const firstNeeded = (a: Account) =>
  a.platform === 'tiktok' && a.method !== 'browser_verified';
export const readiness = (a: Account) =>
  !a.connected
    ? 'Нужен вход'
    : firstNeeded(a)
      ? 'Вход выполнен · готов к первой отправке'
      : a.platform === 'tiktok'
        ? 'Готов к автозаливу'
        : 'Подключён · готов к публикации';

/** One plain sentence per job state; technical details stay in «Подробнее». */
export function stateText(j: Job) {
  const name = j.account.name;
  switch (j.status) {
    case 'queued':
      return 'Ждёт подготовки видео';
    case 'rendering':
      return `Подготовка видео · ${Math.round(j.progress)}%`;
    case 'ready':
      return j.approved
        ? 'Ждёт отправки'
        : firstNeeded(j.account)
          ? 'Готов · ждёт первой публикации'
          : 'MP4 готов · ждёт вашего подтверждения';
    case 'waiting_auth':
      return `Войдите в ${name}, чтобы продолжить`;
    case 'scheduled':
      return j.scheduled ? 'Запланировано' : 'Ждёт очереди на отправку';
    case 'uploading':
      return 'Отправляется на платформу';
    case 'platform_processing':
      return 'Принято платформой · проверяем обработку';
    case 'published':
      return 'Опубликовано';
    case 'unknown':
      return 'Ответ потерян · повтор остановлен';
    case 'missed':
      return 'Время пропущено · выберите новое';
    case 'failed':
      return 'Не удалось подготовить видео';
    case 'cancelled':
      return 'Отменено';
    case 'rejected':
      return 'Отклонено платформой';
    case 'deleted':
      return 'Удалено с YouTube';
    default:
      return j.status;
  }
}
export const jobGroup = (j: Job) =>
  ['published', 'cancelled', 'deleted'].includes(j.status)
    ? 'done'
    : ['waiting_auth', 'unknown', 'missed', 'failed', 'rejected'].includes(j.status) ||
        (j.status === 'ready' && !j.approved)
      ? 'action'
      : 'work';

/** The single action that moves a stopped job forward. */
export function NextAction({
  job,
  busy,
  run,
  openJob,
  openChannels,
}: {
  job: Job;
  busy: boolean;
  run: Run;
  openJob: (job: Job) => void;
  openChannels: () => void;
}) {
  const tiktok = job.account.platform === 'tiktok';
  if (job.status === 'waiting_auth')
    return (
      <button className="e-button primary" onClick={openChannels}>
        Войти
      </button>
    );
  if (tiktok && ['unknown', 'platform_processing'].includes(job.status) && job.upload_started_at)
    return (
      <button
        className="e-button primary"
        disabled={busy}
        onClick={() =>
          void run(
            () => api(`/jobs/${job.id}/tiktok-result`, 'POST'),
            'Результат проверен в TikTok Studio. Повторной отправки не было',
          )
        }
      >
        Проверить результат
      </button>
    );
  if (job.url && ['published', 'platform_processing'].includes(job.status))
    return (
      <a className="e-button" href={job.url} target="_blank" rel="noreferrer">
        <ExternalLink size={13} /> Открыть ролик
      </a>
    );
  const label =
    job.status === 'unknown'
      ? 'Сверить ролик'
      : job.status === 'ready' && !job.approved
        ? firstNeeded(job.account)
          ? 'Опубликовать первый'
          : 'Подтвердить'
        : job.status === 'missed'
          ? 'Новое время'
          : job.status === 'failed'
            ? 'Повторить'
            : '';
  return label ? (
    <button className="e-button primary" onClick={() => openJob(job)}>
      {label}
    </button>
  ) : null;
}

const zone = (tz: string) => (tz === 'UTC' ? 'UTC' : 'Europe/Moscow');
const when = (iso: string, tz: string) =>
  new Date(iso).toLocaleString('ru-RU', {
    timeZone: zone(tz),
    day: '2-digit',
    month: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
const toInput = (iso: string | null, tz: string) =>
  iso ? new Date(Date.parse(iso) + (tz === 'UTC' ? 0 : 3 * 3600e3)).toISOString().slice(0, 16) : '';
const fromInput = (value: string, tz: string) =>
  value ? new Date(value + (tz === 'UTC' ? 'Z' : '+03:00')).toISOString() : null;

function Pages({
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
  const pages = Math.max(1, Math.ceil(total / size));
  if (pages < 2) return null;
  return (
    <div className="e-row release-pager">
      <button className="e-button" disabled={!page} onClick={() => set(page - 1)}>
        Назад
      </button>
      <span className="e-muted">
        {Math.min(page, pages - 1) + 1} / {pages}
      </span>
      <button className="e-button" disabled={page + 1 >= pages} onClick={() => set(page + 1)}>
        Далее
      </button>
    </div>
  );
}

export default function Release({
  data,
  releaseId,
  setReleaseId,
  busy,
  run,
  openJob,
  openChannels,
  openEditor,
  openQueue,
}: {
  data: State;
  releaseId: string;
  setReleaseId: (id: string) => void;
  busy: boolean;
  run: Run;
  openJob: (job: Job) => void;
  openChannels: () => void;
  openEditor: (assetId: string) => void;
  openQueue: () => void;
}) {
  const [release, setRelease] = useState<ReleaseData | null>(null),
    [loaded, setLoaded] = useState(false),
    [problem, setProblem] = useState(''),
    [page, setPage] = useState(0),
    [preview, setPreview] = useState(''),
    [commonBanner, setCommonBanner] = useState('keep'),
    [best, setBest] = useState<Record<string, BestTime>>({}),
    [link, setLink] = useState(''),
    [firstPage, setFirstPage] = useState(0);
  const tz = data.settings.timezone;
  useEffect(() => {
    let alive = true;
    setPage(0);
    setPreview('');
    const load = async () => {
      try {
        if (releaseId) {
          const next = await api<ReleaseData>(`/releases/${releaseId}`);
          if (alive) setRelease(next);
        } else {
          const list = await api<ReleaseData[]>('/releases');
          if (!alive) return;
          setRelease(list[0] ?? null);
          if (list[0]) setReleaseId(list[0].id);
        }
      } catch (e) {
        if (alive) {
          setRelease(null);
          if (releaseId) setReleaseId('');
          setProblem(e instanceof Error ? e.message : 'Не удалось открыть выпуск');
        }
      } finally {
        if (alive) setLoaded(true);
      }
    };
    void load();
    return () => {
      alive = false;
    };
  }, [releaseId]);
  // A started release changes in the background (deferred batch continues after the first publication).
  useEffect(() => {
    if (release?.status !== 'started') return;
    const timer = setInterval(
      () =>
        void api<ReleaseData>(`/releases/${release.id}`)
          .then(setRelease)
          .catch(() => undefined),
      3000,
    );
    return () => clearInterval(timer);
  }, [release?.id, release?.status]);

  if (!loaded) return <section className="e-content release-view" />;
  if (!release)
    return (
      <section className="e-content release-view">
        <div className="e-page-head">
          <div>
            <h1>Выпуск</h1>
            <p>Ролики → каналы и время → проверка перед отправкой</p>
          </div>
        </div>
        <div className="e-empty">
          <h2>Выпусков пока нет</h2>
          <p>
            Нажмите «К публикации» в Нарезках, Историях или Монтажной. Выбранные ролики появятся
            здесь, выбор сохранится при переходах и перезапуске.
          </p>
          {problem && <p role="alert">{problem}</p>}
        </div>
      </section>
    );

  const plan = release.plan;
  const assets = release.asset_ids
    .map((id) => data.assets.find((a) => a.id === id))
    .filter((a): a is Asset => !!a);
  const accounts = release.account_ids
    .map((id) => data.accounts.find((a) => a.id === id))
    .filter((a): a is Account => !!a);
  const banners = data.assets.filter((a) => a.kind === 'banner');
  const others = data.assets.filter((a) => a.kind === 'video' && !release.asset_ids.includes(a.id));
  const fresh = plan.publish ? accounts.filter(firstNeeded) : [];
  const interval = data.settings.interval;
  const draft = release.status === 'draft';

  const save = async (
    next: Partial<Pick<ReleaseData, 'asset_ids' | 'account_ids'>> & { plan?: Plan },
  ) => {
    setProblem('');
    setRelease({ ...release, ...next });
    try {
      const saved = await api<ReleaseData>(`/releases/${release.id}`, 'PUT', next);
      if (next.asset_ids || next.account_ids) setRelease(saved);
    } catch (e) {
      setProblem(e instanceof Error ? e.message : 'Не удалось сохранить выпуск');
      setRelease(await api<ReleaseData>(`/releases/${release.id}`));
    }
  };
  const setPlan = (patch: Partial<Plan>) => void save({ plan: { ...plan, ...patch } });
  const editAssets = (list: Asset[], patch: (edit: Edit) => Partial<Edit>, success: string) =>
    run(async () => {
      for (const a of list)
        await api(`/assets/${a.id}/edit`, 'PUT', { ...a.edit, ...patch(a.edit) });
    }, success);
  const toggleAccount = (a: Account) => {
    const chosen = release.account_ids.includes(a.id);
    const next = chosen
      ? release.account_ids.filter((id) => id !== a.id)
      : [
          // One video → one TikTok + one YouTube: a second account of a platform replaces the first.
          ...release.account_ids.filter(
            (id) =>
              data.settings.one_per_platform === false ||
              data.accounts.find((x) => x.id === id)?.platform !== a.platform,
          ),
          a.id,
        ];
    void save({ account_ids: next });
  };

  const bubaPending =
    plan.publish &&
    assets.some((a) => a.edit.buba_mode && !Object.values(a.edit.buba_review ?? {}).every(Boolean));
  const issues: { text: string; action?: ReactNode }[] = [];
  if (!assets.length) issues.push({ text: 'Добавьте хотя бы один ролик.' });
  if (!accounts.length) issues.push({ text: 'Выберите каналы на шаге «Каналы и время».' });
  const signedOut = plan.publish ? accounts.filter((x) => !x.connected) : [];
  if (signedOut.length)
    issues.push({
      text: `Нужен вход: ${signedOut.map((a) => a.name).join(', ')}.`,
      action: (
        <button className="text-link" onClick={openChannels}>
          Войти →
        </button>
      ),
    });
  if (
    plan.publish &&
    accounts.some((a) => a.platform === 'tiktok') &&
    assets.some((a) => a.edit.visibility === 'unlisted')
  )
    issues.push({
      text: 'TikTok не поддерживает «По ссылке». Выберите другую видимость на шаге 2.',
    });
  const noBuba =
    plan.publish && data.settings.buba_required ? assets.filter((x) => !x.edit.buba_mode) : [];
  if (noBuba.length)
    issues.push({
      text: `Включите режим БУБА в монтажной: ${noBuba.map((a) => `«${a.name}»`).join(', ')}.`,
      action: (
        <button className="text-link" onClick={() => openEditor(noBuba[0].id)}>
          Открыть →
        </button>
      ),
    });
  if (bubaPending && !plan.buba_confirmed) issues.push({ text: 'Подтвердите условия БУБА ниже.' });
  if (plan.scheduled && Date.parse(plan.scheduled) <= Date.now())
    issues.push({ text: 'Указанное время уже прошло. Выберите новое на шаге 2.' });

  const timeFor = (account: Account, index: number) => {
    if (!plan.publish) return 'Только MP4';
    if (firstNeeded(account))
      return index === 0
        ? 'Первый: по вашей кнопке'
        : plan.continue_after_first
          ? 'После успеха первого'
          : 'Ждёт вашего решения';
    const base = plan.schedules[account.id] ?? plan.scheduled;
    if (base) return when(new Date(Date.parse(base) + index * interval * 60e3).toISOString(), tz);
    return index === 0 ? 'Сразу после подготовки' : `≈ +${index * interval} мин`;
  };
  const rows = assets.flatMap((asset, index) =>
    accounts.map((account) => ({ asset, account, time: timeFor(account, index) })),
  );

  const start = () =>
    run(
      async () => {
        if (plan.publish) {
          // Read-only sign-in check of every chosen channel before anything is queued.
          const checks = await api<Record<string, { status: string; detail: string }>>(
            '/accounts/preflight',
            'POST',
            { account_ids: release.account_ids },
          );
          const failed = Object.entries(checks).filter(([, c]) => c.status === 'auth');
          if (failed.length)
            throw new Error(
              'Выпуск не запущен: вход не подтверждён — ' +
                failed
                  .map(
                    ([key, c]) =>
                      `${data.accounts.find((a) => a.id === key)?.name ?? key}: ${c.detail}`,
                  )
                  .join('; '),
            );
        }
        setRelease(await api<ReleaseData>(`/releases/${release.id}/start`, 'POST'));
        setPage(0);
      },
      plan.publish ? 'Выпуск запущен' : 'Готовим MP4 для просмотра',
    );

  const stepper = (
    <div className="release-steps" role="tablist" aria-label="Шаги выпуска">
      {STEPS.map((name, i) => (
        <button
          key={name}
          role="tab"
          aria-selected={draft && plan.step === i + 1}
          disabled={!draft}
          className={!draft || plan.step > i + 1 ? 'done' : ''}
          onClick={() => {
            setPage(0);
            setPlan({ step: i + 1 });
          }}
        >
          <span>{!draft || plan.step > i + 1 ? <Check size={12} /> : i + 1}</span>
          {name}
        </button>
      ))}
    </div>
  );

  // ---------- started: progress of the release ----------
  if (!draft) {
    const jobs = (release.jobs.job_ids ?? [])
      .map((id) => data.jobs.find((j) => j.id === id))
      .filter((j): j is Job => !!j);
    const firsts = Object.entries(release.jobs.first ?? {});
    const current = firsts[Math.min(firstPage, Math.max(0, firsts.length - 1))];
    const firstJob = current && data.jobs.find((j) => j.id === current[1]);
    const deferred = current && release.jobs.deferred?.[current[0]];
    const account = current && data.accounts.find((a) => a.id === current[0]);
    const finished = jobs.filter((j) => jobGroup(j) === 'done').length;
    const shown = firstJob?.has_output ? `/api/jobs/${firstJob.id}/file?download=false` : preview;
    return (
      <section className="e-content release-view">
        <div className="e-page-head">
          <div>
            <h1>Выпуск</h1>
            <p>
              {assets.length} ролик(ов) · {accounts.length} канал(ов) · завершено {finished} из{' '}
              {jobs.length}
            </p>
          </div>
          {stepper}
        </div>
        {problem && (
          <div className="story-message" role="alert">
            {problem}
          </div>
        )}
        <div className="release-body">
          <div className="release-main">
            {firstJob && account && (
              <div className="release-first" aria-label="Первая публикация TikTok">
                <div className="e-row e-between">
                  <b>Первая публикация в {account.name}</b>
                  {firsts.length > 1 && (
                    <Pages page={firstPage} set={setFirstPage} total={firsts.length} size={1} />
                  )}
                </div>
                <FirstStep
                  job={firstJob}
                  account={account}
                  busy={busy}
                  run={run}
                  link={link}
                  setLink={setLink}
                  paused={data.settings.paused}
                  openChannels={openChannels}
                  openJob={openJob}
                />
                {deferred && (
                  <p className="e-small release-deferred">
                    Остальные {deferred.asset_ids.length}:{' '}
                    {deferred.state === 'queued'
                      ? 'поставлены в автозалив.'
                      : deferred.state === 'waiting'
                        ? deferred.detail === 'unknown'
                          ? 'удерживаются, пока не проверен результат первого.'
                          : deferred.detail
                            ? 'удерживаются: первый ролик не опубликован.'
                            : 'продолжатся автоматически после успеха первого.'
                        : deferred.state === 'changed'
                          ? 'ролики изменились после подтверждения — проверьте и разрешите снова.'
                          : deferred.state === 'blocked'
                            ? deferred.detail
                            : 'ждут вашего решения после первого ролика.'}{' '}
                    {['not_allowed', 'changed', 'blocked'].includes(deferred.state) &&
                      account.method === 'browser_verified' && (
                        <button
                          className="text-link"
                          disabled={busy}
                          onClick={() =>
                            void run(async () => {
                              setRelease(
                                await api<ReleaseData>(
                                  `/releases/${release.id}/continue/${account.id}`,
                                  'POST',
                                ),
                              );
                            }, `Остальные ролики поставлены в автозалив ${account.name}`)
                          }
                        >
                          Продолжить остальные {deferred.asset_ids.length} →
                        </button>
                      )}
                  </p>
                )}
              </div>
            )}
            <table className="e-table release-table">
              <thead>
                <tr>
                  <th style={{ width: '34%' }}>Ролик</th>
                  <th style={{ width: '18%' }}>Канал</th>
                  <th style={{ width: '28%' }}>Состояние</th>
                  <th>Действие</th>
                </tr>
              </thead>
              <tbody>
                {jobs
                  .slice(page * (firstJob ? 3 : TABLE), (page + 1) * (firstJob ? 3 : TABLE))
                  .map((j) => (
                    <tr key={j.id}>
                      <td>
                        <b className="truncate">{j.asset_name}</b>
                      </td>
                      <td>
                        <span className="truncate">
                          {j.account.platform === 'youtube' ? 'YT' : 'TT'} · {j.account.name}
                        </span>
                      </td>
                      <td>
                        <span
                          className={
                            'e-badge' +
                            (jobGroup(j) === 'action'
                              ? ' bad'
                              : j.status === 'published'
                                ? ' good'
                                : '')
                          }
                        >
                          {stateText(j)}
                        </span>
                        {j.status === 'rendering' && (
                          <div className="e-progress">
                            <span style={{ width: `${j.progress}%` }} />
                          </div>
                        )}
                      </td>
                      <td>
                        <div className="e-row release-actions">
                          <NextAction
                            job={j}
                            busy={busy}
                            run={run}
                            openJob={openJob}
                            openChannels={openChannels}
                          />
                          <button className="e-button" onClick={() => openJob(j)}>
                            Подробнее
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
            <Pages page={page} set={setPage} total={jobs.length} size={firstJob ? 3 : TABLE} />
          </div>
          <aside className="story-preview">
            <h3>{firstJob?.has_output ? 'Итоговый MP4' : 'Предпросмотр'}</h3>
            <div className="story-screen">
              {shown ? (
                <video key={shown} src={shown} controls playsInline />
              ) : (
                <p className="e-muted">Готовый ролик появится здесь после подготовки</p>
              )}
            </div>
          </aside>
        </div>
        <div className="story-actions">
          <button className="e-button" onClick={openQueue}>
            Открыть очередь
          </button>
          <button
            className="e-button"
            disabled={busy}
            title="План скроется; задания останутся в очереди"
            onClick={() =>
              void run(async () => {
                await api(`/releases/${release.id}`, 'DELETE');
                setRelease(null);
                setReleaseId('');
              }, 'Выпуск закрыт. Задания остались в очереди')
            }
          >
            Закрыть выпуск
          </button>
          <span className="e-small e-muted">
            {data.settings.paused
              ? 'Очередь на паузе — отправка начнётся после «Запустить».'
              : 'Компьютер должен оставаться включённым до конца отправки.'}
          </span>
        </div>
      </section>
    );
  }

  // ---------- draft: three steps ----------
  const visibleAssets = assets.slice(page * ROWS, page * ROWS + ROWS);
  const mixed = new Set(assets.map((a) => a.edit.visibility)).size > 1;
  const shown = preview || (assets[0] ? `/api/assets/${assets[0].id}/file` : '');
  return (
    <section className="e-content release-view">
      <div className="e-page-head">
        <div>
          <h1>Выпуск</h1>
          <p>
            {assets.length} ролик(ов) · {accounts.length} канал(ов) · выбор сохраняется
            автоматически
          </p>
        </div>
        {stepper}
      </div>
      {problem && (
        <div className="story-message" role="alert">
          {problem}
        </div>
      )}
      <div className="release-body">
        <div className="release-main">
          {plan.step === 1 && (
            <>
              <div className="e-row release-toolbar">
                <label>
                  Баннер для всех
                  <select value={commonBanner} onChange={(e) => setCommonBanner(e.target.value)}>
                    <option value="keep">— выбрать —</option>
                    <option value="">Текстовый баннер</option>
                    {banners.map((b) => (
                      <option key={b.id} value={b.id}>
                        {b.name}
                      </option>
                    ))}
                  </select>
                </label>
                <button
                  className="e-button"
                  disabled={busy || commonBanner === 'keep' || !assets.length}
                  onClick={() => {
                    const banner = banners.find((b) => b.id === commonBanner);
                    void editAssets(
                      assets,
                      () => ({
                        banner_id: commonBanner || null,
                        freeze_video: true,
                        banner_audio: !!banner?.media.has_audio,
                      }),
                      `Баннер применён к ${assets.length} ролик(ам)`,
                    );
                  }}
                >
                  Применить ко всем
                </button>
                {others.length > 0 && (
                  <select
                    aria-label="Добавить ролик"
                    value=""
                    onChange={(e) =>
                      e.target.value &&
                      void save({ asset_ids: [...release.asset_ids, e.target.value] })
                    }
                  >
                    <option value="">+ Добавить ролик…</option>
                    {others.map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.name}
                      </option>
                    ))}
                  </select>
                )}
              </div>
              <div className="release-rows">
                {visibleAssets.map((a) => (
                  <div className="release-row" key={a.id}>
                    <img src={`/api/assets/${a.id}/thumbnail`} alt="" loading="lazy" />
                    <div className="release-row-main">
                      <input
                        className="e-field"
                        aria-label="Название ролика"
                        maxLength={100}
                        defaultValue={a.edit.title ?? ''}
                        key={`${a.id}-${a.edit.title}`}
                        placeholder="Название"
                        disabled={busy}
                        onBlur={(e) =>
                          e.target.value !== (a.edit.title ?? '') &&
                          void editAssets(
                            [a],
                            () => ({ title: e.target.value }),
                            'Название сохранено',
                          )
                        }
                      />
                      <small className="e-muted truncate">
                        {Math.round(a.media.duration)} с ·{' '}
                        {VISIBILITY[a.edit.visibility] ?? a.edit.visibility}
                        {a.edit.buba_mode ? ' · БУБА' : ''}
                      </small>
                    </div>
                    <select
                      aria-label="Баннер ролика"
                      value={a.edit.banner_id ?? ''}
                      disabled={busy}
                      onChange={(e) => {
                        const banner = banners.find((b) => b.id === e.target.value);
                        void editAssets(
                          [a],
                          () => ({
                            banner_id: e.target.value || null,
                            freeze_video: true,
                            banner_audio: !!banner?.media.has_audio,
                          }),
                          'Баннер ролика изменён',
                        );
                      }}
                    >
                      <option value="">Текстовый баннер</option>
                      {banners.map((b) => (
                        <option key={b.id} value={b.id}>
                          {b.name}
                        </option>
                      ))}
                    </select>
                    <button
                      className="e-button"
                      title="Смотреть исходник"
                      aria-label="Смотреть"
                      onClick={() => setPreview(`/api/assets/${a.id}/file`)}
                    >
                      <Play size={13} />
                    </button>
                    <button className="e-button" onClick={() => openEditor(a.id)}>
                      Править
                    </button>
                    <button
                      className="e-button"
                      aria-label="Убрать из выпуска"
                      title="Убрать из выпуска"
                      disabled={assets.length < 2}
                      onClick={() =>
                        void save({ asset_ids: release.asset_ids.filter((id) => id !== a.id) })
                      }
                    >
                      <X size={13} />
                    </button>
                  </div>
                ))}
              </div>
              <Pages page={page} set={setPage} total={assets.length} size={ROWS} />
            </>
          )}
          {plan.step === 2 && (
            <div className="release-grid">
              <div className="release-accounts" aria-label="Каналы">
                <h3>Куда</h3>
                {!data.accounts.length && (
                  <button className="text-link" onClick={openChannels}>
                    Добавьте канал в разделе «Каналы» →
                  </button>
                )}
                {data.accounts.slice(page * 6, page * 6 + 6).map((a) => (
                  <label
                    key={a.id}
                    className={
                      'release-account' + (release.account_ids.includes(a.id) ? ' on' : '')
                    }
                  >
                    <input
                      type="checkbox"
                      checked={release.account_ids.includes(a.id)}
                      onChange={() => toggleAccount(a)}
                    />
                    <span className="e-avatar">{a.platform === 'youtube' ? 'YT' : 'TT'}</span>
                    <span className="release-account-text">
                      <b className="truncate">{a.name}</b>
                      <small className={a.connected ? 'e-muted' : 'release-warn'}>
                        {readiness(a)}
                      </small>
                    </span>
                  </label>
                ))}
                <Pages page={page} set={setPage} total={data.accounts.length} size={6} />
              </div>
              <div className="release-options">
                <h3>Как и когда</h3>
                <label>
                  Видимость всех роликов
                  <select
                    className="e-field"
                    value={mixed ? '' : (assets[0]?.edit.visibility ?? 'public')}
                    disabled={busy || !assets.length}
                    onChange={(e) =>
                      e.target.value &&
                      void editAssets(
                        assets,
                        () => ({ visibility: e.target.value as Edit['visibility'] }),
                        'Видимость сохранена',
                      )
                    }
                  >
                    {mixed && <option value="">Разная</option>}
                    <option value="public">Открытый доступ</option>
                    <option value="private">Только я</option>
                    <option
                      value="unlisted"
                      disabled={accounts.some((a) => a.platform === 'tiktok')}
                    >
                      По ссылке (только YouTube)
                    </option>
                  </select>
                </label>
                <div className="e-segment release-when" role="radiogroup" aria-label="Когда">
                  <button
                    role="radio"
                    aria-checked={!plan.scheduled}
                    onClick={() => setPlan({ scheduled: null })}
                  >
                    Сразу после подготовки
                  </button>
                  <button
                    role="radio"
                    aria-checked={!!plan.scheduled}
                    onClick={() =>
                      !plan.scheduled &&
                      setPlan({ scheduled: new Date(Date.now() + 3600e3).toISOString() })
                    }
                  >
                    По расписанию
                  </button>
                </div>
                {plan.scheduled && (
                  <label>
                    Первая отправка · {tz}
                    <input
                      className="e-field"
                      type="datetime-local"
                      value={toInput(plan.scheduled, tz)}
                      onChange={(e) => setPlan({ scheduled: fromInput(e.target.value, tz) })}
                    />
                  </label>
                )}
                <div className="e-row">
                  <button
                    className="e-button"
                    disabled={busy || !accounts.length}
                    title="Подобрать время для каждого канала по его статистике за первые сутки"
                    onClick={() =>
                      void run(async () => {
                        const advice = await api<Record<string, BestTime>>(
                          `/best-time?accounts=${release.account_ids.join(',')}`,
                        );
                        setBest(advice);
                        setPlan({
                          schedules: Object.fromEntries(
                            Object.entries(advice).map(([key, value]) => [key, value.time]),
                          ),
                        });
                      }, 'Время подобрано по статистике каналов')
                    }
                  >
                    Лучшее время
                  </button>
                  {Object.keys(plan.schedules).length > 0 && (
                    <button
                      className="text-link"
                      onClick={() => {
                        setBest({});
                        setPlan({ schedules: {} });
                      }}
                    >
                      Сбросить
                    </button>
                  )}
                </div>
                {Object.keys(plan.schedules).length > 0 && (
                  <ul className="release-times">
                    {accounts
                      .filter((a) => plan.schedules[a.id])
                      .map((a) => (
                        <li key={a.id} title={best[a.id]?.reason}>
                          <span className="truncate">{a.name}</span>
                          <b>{when(plan.schedules[a.id], tz)}</b>
                        </li>
                      ))}
                  </ul>
                )}
                <label>
                  Комментарий автора после публикации
                  <input
                    className="e-field"
                    maxLength={500}
                    defaultValue={plan.comment ?? ''}
                    placeholder="Например: А вы бы открыли {название}?"
                    onBlur={(e) => setPlan({ comment: e.target.value.trim() || null })}
                  />
                </label>
                <div className="e-row">
                  <label className="e-check">
                    <input
                      type="checkbox"
                      checked={plan.comment_pin}
                      onChange={(e) => setPlan({ comment_pin: e.target.checked })}
                    />
                    Закрепить
                  </label>
                  {accounts.some((a) => a.platform === 'tiktok') && (
                    <input
                      className="e-field release-sound"
                      maxLength={100}
                      aria-label="Звук TikTok из «Избранного»"
                      placeholder="Звук TikTok из «Избранного»"
                      defaultValue={plan.tiktok_sound ?? ''}
                      onBlur={(e) => setPlan({ tiktok_sound: e.target.value.trim() || null })}
                    />
                  )}
                </div>
              </div>
            </div>
          )}
          {plan.step === 3 && (
            <>
              <table className="e-table release-table">
                <thead>
                  <tr>
                    <th style={{ width: '36%' }}>Ролик</th>
                    <th style={{ width: '22%' }}>Канал</th>
                    <th style={{ width: '17%' }}>Видимость</th>
                    <th>Когда · {tz}</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.slice(page * TABLE, page * TABLE + TABLE).map((r) => (
                    <tr key={r.asset.id + r.account.id}>
                      <td>
                        <b className="truncate">{r.asset.name}</b>
                      </td>
                      <td>
                        <span className="truncate">
                          {r.account.platform === 'youtube' ? 'YT' : 'TT'} · {r.account.name}
                        </span>
                      </td>
                      <td>{VISIBILITY[r.asset.edit.visibility] ?? r.asset.edit.visibility}</td>
                      <td className="release-time">{r.time}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <Pages page={page} set={setPage} total={rows.length} size={TABLE} />
              <div className="release-confirm">
                <label className="e-check">
                  <input
                    type="checkbox"
                    checked={!plan.publish}
                    onChange={(e) => setPlan({ publish: !e.target.checked })}
                  />
                  Только подготовить MP4, ничего не публиковать
                </label>
                {fresh.length > 0 && assets.length > 1 && (
                  <label className="e-check">
                    <input
                      type="checkbox"
                      checked={plan.continue_after_first}
                      onChange={(e) => setPlan({ continue_after_first: e.target.checked })}
                    />
                    После успешного первого ролика продолжить остальные {assets.length - 1} в{' '}
                    {fresh.map((a) => a.name).join(', ')} по этому плану
                  </label>
                )}
                {bubaPending && (
                  <label className="e-check">
                    <input
                      type="checkbox"
                      checked={plan.buba_confirmed}
                      onChange={(e) => setPlan({ buba_confirmed: e.target.checked })}
                    />
                    Подтверждаю условия БУБА: ссылка бота в шапке, разборчивый звук рекламы, права и
                    переработка, без запрещённого контента и накрутки
                  </label>
                )}
                {fresh.length > 0 && (
                  <p className="e-small e-muted">
                    {fresh.map((a) => a.name).join(', ')}: первая отправка. Сначала подготовим
                    первый ролик, вы посмотрите итоговый MP4 и опубликуете его кнопкой. После
                    подтверждённого результата канал готов к автозаливу.
                  </p>
                )}
              </div>
            </>
          )}
        </div>
        <aside className="story-preview release-side">
          {plan.step === 1 ? (
            <>
              <h3>Предпросмотр исходника</h3>
              <div className="story-screen">
                {shown ? (
                  <video key={shown} src={shown} controls playsInline />
                ) : (
                  <p className="e-muted">Нет роликов</p>
                )}
              </div>
              <p>Итоговый MP4 с баннером появится после подготовки.</p>
            </>
          ) : (
            <>
              <h3>{plan.step === 3 ? 'Проверка' : 'Сводка'}</h3>
              <p>
                {assets.length} ролик(ов) → {accounts.length} канал(ов) = {rows.length} отправок.
                Интервал канала: {interval} мин.
              </p>
              {issues.length ? (
                <ul className="release-issues" role="alert">
                  {issues.slice(0, 3).map((issue) => (
                    <li key={issue.text}>
                      <span className="release-issue-text">{issue.text}</span> {issue.action}
                    </li>
                  ))}
                  {issues.length > 3 && <li>И ещё {issues.length - 3}.</li>}
                </ul>
              ) : (
                <p className="release-ok">
                  <Check size={12} /> Всё готово к запуску
                </p>
              )}
              {!data.worker && <p className="release-warn">Фоновая обработка не запущена.</p>}
            </>
          )}
        </aside>
      </div>
      <div className="story-actions">
        <button
          className="e-button"
          disabled={plan.step === 1}
          onClick={() => {
            setPage(0);
            setPlan({ step: plan.step - 1 });
          }}
        >
          Назад
        </button>
        {plan.step < 3 ? (
          <button
            className="e-button primary"
            disabled={!assets.length || (plan.step === 2 && !accounts.length)}
            onClick={() => {
              setPage(0);
              setPlan({ step: plan.step + 1 });
            }}
          >
            Далее: {STEPS[plan.step]}
          </button>
        ) : (
          <button
            className="e-button primary"
            disabled={busy || issues.length > 0}
            onClick={() => void start()}
          >
            {!plan.publish
              ? `Подготовить MP4 · ${rows.length}`
              : fresh.length
                ? 'Запустить выпуск'
                : `Запустить автозалив · ${rows.length}`}
          </button>
        )}
        <button
          className="e-button"
          aria-label="Удалить черновик выпуска"
          title="Удалить черновик выпуска (ролики останутся)"
          disabled={busy}
          onClick={() =>
            void run(async () => {
              await api(`/releases/${release.id}`, 'DELETE');
              setRelease(null);
              setReleaseId('');
            }, 'Черновик выпуска удалён')
          }
        >
          <Trash2 size={13} />
        </button>
        <span className="e-small e-muted">
          {plan.publish
            ? 'Ничего не отправляется до кнопки запуска на шаге 3.'
            : 'Будут созданы MP4 для просмотра.'}
        </span>
      </div>
    </section>
  );
}

function FirstStep({
  job,
  account,
  busy,
  run,
  link,
  setLink,
  paused,
  openChannels,
  openJob,
}: {
  job: Job;
  account: Account;
  busy: boolean;
  run: Run;
  link: string;
  setLink: (v: string) => void;
  paused: boolean;
  openChannels: () => void;
  openJob: (job: Job) => void;
}) {
  const visibility = VISIBILITY[job.config.visibility] ?? job.config.visibility;
  const profile = account.external_id
    ? `https://www.tiktok.com/@${account.external_id.replace(/^@/, '')}`
    : '';
  const check = (
    <button
      className="e-button primary"
      disabled={busy}
      onClick={() =>
        void run(
          () => api(`/jobs/${job.id}/tiktok-result`, 'POST'),
          'Результат проверен в TikTok Studio. Повторной отправки не было',
        )
      }
    >
      Проверить результат
    </button>
  );
  const stages = ['Подготовка', 'Публикация', 'Обработка TikTok', 'Результат'];
  const stage =
    job.status === 'published'
      ? 4
      : job.status === 'platform_processing' || job.status === 'unknown'
        ? 3
        : job.status === 'uploading' || job.approved
          ? 2
          : 1;
  return (
    <>
      <ol className="release-stages">
        {stages.map((name, i) => (
          <li key={name} className={i + 1 < stage ? 'done' : i + 1 === stage ? 'on' : ''}>
            {name}
          </li>
        ))}
      </ol>
      {['queued', 'rendering'].includes(job.status) ? (
        <p>
          Готовим «{job.asset_name}»
          {job.status === 'rendering' ? ` · ${Math.round(job.progress)}%` : ''}.
          {paused ? ' Очередь на паузе — нажмите «Запустить» вверху.' : ''}
        </p>
      ) : job.status === 'waiting_auth' || !account.connected ? (
        <div className="e-row">
          <span>Вход в {account.name} перестал действовать.</span>
          <button className="e-button primary" onClick={openChannels}>
            Войти
          </button>
        </div>
      ) : job.status === 'ready' && !job.approved ? (
        <>
          <p>
            Посмотрите итоговый MP4 справа. Это настоящая публикация «{job.asset_name}» в{' '}
            {account.name} с видимостью «{visibility}». После подтверждённого результата канал
            станет готов к автозаливу.
          </p>
          <div className="e-row">
            <button
              className="e-button primary"
              disabled={busy || job.config.visibility === 'unlisted'}
              onClick={() =>
                void run(async () => {
                  await api(`/jobs/${job.id}/action`, 'POST', { action: 'test_upload' });
                  if (paused) await api('/settings', 'PATCH', { paused: false });
                }, 'Первая публикация запущена')
              }
            >
              Опубликовать первый ролик
            </button>
            <a
              className="e-button"
              href={`/api/jobs/${job.id}/file`}
              target="_blank"
              rel="noreferrer"
            >
              Скачать MP4
            </a>
          </div>
        </>
      ) : job.status === 'uploading' ||
        (job.approved && ['ready', 'scheduled'].includes(job.status)) ? (
        <p>
          {job.status === 'uploading'
            ? 'Загружаем в TikTok. Не выключайте компьютер.'
            : paused
              ? 'Отправка разрешена. Очередь на паузе — нажмите «Запустить» вверху.'
              : 'Отправка разрешена, начнётся в ближайшие минуты.'}
        </p>
      ) : job.status === 'platform_processing' ? (
        <div className="e-row">
          <span>TikTok принял ролик. Проверяем, что он появился.</span>
          {job.upload_started_at && check}
          {job.url && (
            <a className="e-button" href={job.url} target="_blank" rel="noreferrer">
              <ExternalLink size={13} /> Открыть ролик
            </a>
          )}
        </div>
      ) : job.status === 'published' ? (
        <div className="e-row">
          <span className="release-ok">
            <Check size={12} /> Опубликовано. {account.name} готов к автозаливу.
          </span>
          {job.url && (
            <a className="e-button" href={job.url} target="_blank" rel="noreferrer">
              <ExternalLink size={13} /> Открыть ролик
            </a>
          )}
        </div>
      ) : job.status === 'unknown' ? (
        <>
          <p>
            Ответ TikTok потерян. Повторная отправка остановлена, чтобы не появился дубль. Проверьте
            результат; если программа не найдёт ролик, откройте профиль и ответьте сами.
          </p>
          <div className="e-row release-unknown">
            {job.upload_started_at && check}
            {profile && (
              <a className="e-button" href={profile} target="_blank" rel="noreferrer">
                <ExternalLink size={13} /> Профиль
              </a>
            )}
            <input
              className="e-field"
              aria-label="Ссылка на опубликованный ролик"
              placeholder="Ролик есть — вставьте ссылку"
              value={link}
              onChange={(e) => setLink(e.target.value)}
            />
            <button
              className="e-button"
              disabled={busy || !link.trim()}
              onClick={() =>
                void run(async () => {
                  await api(`/jobs/${job.id}/action`, 'POST', {
                    action: 'tiktok_link',
                    external_id: link.trim(),
                  });
                  setLink('');
                }, 'Ролик учтён по вашей ссылке')
              }
            >
              Сохранить
            </button>
            <button
              className="e-button"
              disabled={busy}
              onClick={() =>
                window.confirm(
                  `Вы проверили профиль ${account.name}, и ролика «${job.asset_name}» там нет? Повторная отправка станет доступна по кнопке.`,
                ) &&
                void run(
                  () => api(`/jobs/${job.id}/action`, 'POST', { action: 'tiktok_absent' }),
                  'Отмечено: ролика нет. Можно опубликовать снова',
                )
              }
            >
              Ролика нет
            </button>
          </div>
        </>
      ) : (
        <div className="e-row">
          <span className="release-warn">{job.error || 'Первая публикация остановлена.'}</span>
          <button className="e-button" onClick={() => openJob(job)}>
            Подробнее
          </button>
        </div>
      )}
    </>
  );
}
