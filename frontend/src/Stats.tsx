import { useMemo, useState } from 'react';
import type { Job, Metric, Payout, State } from './api';
import { channelSummaries, latestMetrics } from './statistics';

// Statistics dashboard. Counters are cumulative snapshots: growth is computed between snapshots,
// platforms are never summed into one number, missing data is shown as «—», not zero.

const DAY = 86_400_000;
const PLATFORM = { tiktok: 'TikTok', youtube: 'YouTube' } as const;
const PHASE: Record<Payout['phase'], [string, string]> = {
  ready: ['Можно подавать', 'good'],
  collecting: ['Набирает', ''],
  choose: ['Выберите площадку', ''],
  submitted: ['Подано', 'accent'],
  paid: ['Выплачено', 'good'],
  rejected: ['Отказ', 'bad'],
  expired: ['Срок прошёл', 'bad'],
};

type Tab = 'overview' | 'videos' | 'channels' | 'payouts';
type Props = {
  data: State;
  metrics: Metric[];
  payouts: Payout[];
  busy: boolean;
  timezone: string;
  refresh: () => void;
  importCsv: () => void;
  history: (jobId: string) => void;
  exportCsv: () => void;
  choose: (assetId: string, jobId: string) => void;
  mark: (jobId: string, state: 'submitted' | 'paid' | 'rejected') => void;
};

const fmt = (v: number | null | undefined) =>
  v === null || v === undefined ? '—' : v.toLocaleString('ru-RU');
const short = (v: number | null | undefined) => {
  if (v === null || v === undefined) return '—';
  if (v >= 1_000_000) return (v / 1_000_000).toFixed(1).replace('.0', '') + ' млн';
  if (v >= 10_000) return Math.round(v / 1000) + ' тыс';
  return v.toLocaleString('ru-RU');
};
const views = (m?: Metric) =>
  typeof m?.values.views === 'number' ? (m.values.views as number) : null;
const published = (j: Job) => Date.parse(j.upload_started_at || j.updated);
const age = (ms: number) => {
  const h = Math.max(0, Math.floor(ms / 3_600_000));
  return h < 48 ? `${h} ч` : `${Math.floor(h / 24)} д`;
};

function seriesFor(jobs: Job[], snapshots: Metric[], days: number) {
  const byJob = new Map<string, Metric[]>();
  for (const m of snapshots) {
    const list = byJob.get(m.job_id) ?? [];
    list.push(m);
    byJob.set(m.job_id, list);
  }
  for (const list of byJob.values())
    list.sort((a, b) => Date.parse(a.captured) - Date.parse(b.captured));
  const end = new Date();
  end.setHours(23, 59, 59, 999);
  const points: {
    day: number;
    tiktok: number | null;
    youtube: number | null;
  }[] = [];
  for (let i = days - 1; i >= 0; i--) {
    const at = end.getTime() - i * DAY;
    const total: Record<'tiktok' | 'youtube', number | null> = {
      tiktok: null,
      youtube: null,
    };
    for (const job of jobs) {
      const known = (byJob.get(job.id) ?? []).filter(
        (m) => Date.parse(m.captured) <= at && views(m) !== null,
      );
      if (!known.length) continue;
      const p = job.account.platform;
      total[p] = (total[p] ?? 0) + views(known[known.length - 1])!;
    }
    points.push({ day: at, ...total });
  }
  return points;
}

function Chart({ points }: { points: ReturnType<typeof seriesFor> }) {
  const W = 640,
    H = 150,
    P = 26;
  const max = Math.max(1, ...points.flatMap((p) => [p.tiktok ?? 0, p.youtube ?? 0]));
  const x = (i: number) => P + (i * (W - P * 2)) / Math.max(1, points.length - 1);
  const y = (v: number) => H - 18 - (v / max) * (H - 34);
  const line = (key: 'tiktok' | 'youtube') =>
    points
      .map((p, i) => (p[key] === null ? null : `${x(i).toFixed(1)},${y(p[key]!).toFixed(1)}`))
      .filter(Boolean)
      .join(' ');
  const area = (key: 'tiktok' | 'youtube') => {
    const idx = points.map((p, i) => (p[key] === null ? -1 : i)).filter((i) => i >= 0);
    if (idx.length < 2) return '';
    return `${x(idx[0])},${H - 18} ${line(key)} ${x(idx[idx.length - 1])},${H - 18}`;
  };
  const labels =
    points.length > 10
      ? [0, Math.floor(points.length / 2), points.length - 1]
      : points.map((_, i) => i);
  return (
    <svg className="st-chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Просмотры по дням">
      <defs>
        <linearGradient id="st-fill" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0" stopColor="var(--e-accent)" stopOpacity="0.28" />
          <stop offset="1" stopColor="var(--e-accent)" stopOpacity="0" />
        </linearGradient>
      </defs>
      {[0, 0.5, 1].map((f) => (
        <g key={f}>
          <line x1={P} x2={W - P} y1={y(max * f)} y2={y(max * f)} className="st-grid" />
          <text x={P - 4} y={y(max * f) + 3} className="st-axis" textAnchor="end">
            {short(Math.round(max * f))}
          </text>
        </g>
      ))}
      {area('tiktok') && <polygon points={area('tiktok')} fill="url(#st-fill)" />}
      <polyline points={line('tiktok')} className="st-line tt" />
      <polyline points={line('youtube')} className="st-line yt" />
      {labels.map((i) => (
        <text key={i} x={x(i)} y={H - 4} className="st-axis" textAnchor="middle">
          {new Date(points[i].day).toLocaleDateString('ru-RU', {
            day: 'numeric',
            month: 'short',
          })}
        </text>
      ))}
    </svg>
  );
}

function Spark({ values }: { values: number[] }) {
  if (values.length < 2) return <span className="e-muted">—</span>;
  const max = Math.max(...values),
    min = Math.min(...values);
  const pts = values
    .map(
      (v, i) =>
        `${(i * 60) / (values.length - 1)},${18 - ((v - min) / Math.max(1, max - min)) * 16}`,
    )
    .join(' ');
  return (
    <svg className="st-spark" viewBox="0 0 60 20" aria-hidden="true">
      <polyline points={pts} />
    </svg>
  );
}

export default function Stats(p: Props) {
  const [tab, setTab] = useState<Tab>('overview');
  const [platform, setPlatform] = useState<'all' | 'tiktok' | 'youtube'>('all');
  const [days, setDays] = useState(14);
  const [page, setPage] = useState(0);
  const jobs = useMemo(
    () =>
      p.data.jobs.filter(
        (j) => j.external_id && (platform === 'all' || j.account.platform === platform),
      ),
    [p.data.jobs, platform],
  );
  const ids = new Set(jobs.map((j) => j.id));
  const snapshots = p.metrics.filter((m) => ids.has(m.job_id));
  const latest = new Map(latestMetrics(snapshots).map((m) => [m.job_id, m]));
  const history = (jobId: string) =>
    snapshots
      .filter((m) => m.job_id === jobId && views(m) !== null)
      .sort((a, b) => Date.parse(a.captured) - Date.parse(b.captured));
  const rows = jobs
    .map((job) => {
      const last = latest.get(job.id);
      const list = history(job.id);
      const lastAt = last ? Date.parse(last.captured) : 0;
      const before = [...list].reverse().find((m) => Date.parse(m.captured) <= lastAt - DAY);
      const at24 = list.filter((m) => Date.parse(m.captured) <= published(job) + DAY).pop();
      const v = views(last),
        likes = last?.values.likes ?? null,
        comments = last?.values.comments ?? null;
      return {
        job,
        views: v,
        likes: typeof likes === 'number' ? likes : null,
        comments: typeof comments === 'number' ? comments : null,
        growth: v !== null && before ? v - views(before)! : null,
        newComments:
          typeof comments === 'number' && typeof before?.values.comments === 'number'
            ? comments - before.values.comments
            : null,
        day1: Date.now() - published(job) >= DAY && at24 ? views(at24) : null,
        er:
          v && typeof likes === 'number'
            ? ((likes + (typeof comments === 'number' ? comments : 0)) / v) * 100
            : null,
        spark: list.map((m) => views(m)!),
        captured: last?.captured ?? null,
      };
    })
    .sort((a, b) => (b.views ?? -1) - (a.views ?? -1));
  const total = (
    pf: 'tiktok' | 'youtube' | 'all',
    key: 'views' | 'likes' | 'growth' | 'comments' | 'newComments',
  ) => {
    const values = rows
      .filter((r) => (pf === 'all' || r.job.account.platform === pf) && r[key] !== null)
      .map((r) => r[key]!);
    return values.length ? values.reduce((a, b) => a + b, 0) : null;
  };
  const engaged = rows.filter((r) => r.er !== null);
  const avgEr = engaged.length ? engaged.reduce((a, r) => a + r.er!, 0) / engaged.length : null;
  const best = rows.find((r) => r.views !== null);
  const updated = rows.reduce<string | null>(
    (d, r) => (r.captured && (!d || r.captured > d) ? r.captured : d),
    null,
  );
  const summaries = channelSummaries(
    p.data.accounts.filter((a) => platform === 'all' || a.platform === platform),
    jobs,
    snapshots,
  );
  const ready = p.payouts.filter((x) => x.phase === 'ready').length;
  const when = (iso: string | null) =>
    iso
      ? new Date(iso).toLocaleString('ru-RU', {
          timeZone: p.timezone,
          dateStyle: 'short',
          timeStyle: 'short',
        })
      : '—';

  return (
    <section className="e-content stats-view st">
      <div className="e-page-head">
        <div>
          <h1>Статистика</h1>
          <p>
            {updated ? `Данные на ${when(updated)} · ` : ''}счётчики платформ, TikTok и YouTube
            считаются отдельно
          </p>
        </div>
        <div className="e-row">
          <button className="e-button" onClick={p.importCsv}>
            Импорт CSV
          </button>
          <button className="e-button" onClick={p.exportCsv} disabled={!summaries.length}>
            Экспорт CSV
          </button>
          <button className="e-button primary" disabled={p.busy} onClick={p.refresh}>
            Обновить
          </button>
        </div>
      </div>
      <div className="st-bar">
        <div className="st-tabs" role="tablist">
          {(
            [
              ['overview', 'Обзор'],
              ['videos', 'Ролики'],
              ['channels', 'Каналы'],
              ['payouts', 'Выплаты БУБА' + (ready ? ` · ${ready}` : '')],
            ] as [Tab, string][]
          ).map(([key, label]) => (
            <button
              key={key}
              role="tab"
              aria-selected={tab === key}
              className={tab === key ? 'on' : ''}
              onClick={() => {
                setTab(key);
                setPage(0);
              }}
            >
              {label}
            </button>
          ))}
        </div>
        {tab !== 'payouts' && (
          <div className="st-tabs small" aria-label="Платформа">
            {(['all', 'tiktok', 'youtube'] as const).map((key) => (
              <button
                key={key}
                className={platform === key ? 'on' : ''}
                onClick={() => setPlatform(key)}
              >
                {key === 'all' ? 'Все' : PLATFORM[key]}
              </button>
            ))}
          </div>
        )}
      </div>

      {tab === 'overview' && (
        <>
          <div className="st-kpis">
            {(['tiktok', 'youtube'] as const)
              .filter((pf) => platform === 'all' || platform === pf)
              .map((pf) => (
                <div className="st-kpi" key={pf}>
                  <span>Просмотры · {PLATFORM[pf]}</span>
                  <strong>{short(total(pf, 'views'))}</strong>
                  <small className={total(pf, 'growth') ? 'up' : ''}>
                    {total(pf, 'growth') !== null
                      ? `+${fmt(total(pf, 'growth'))} за сутки`
                      : 'прирост — нужен снимок суточной давности'}
                  </small>
                </div>
              ))}
            <div className="st-kpi">
              <span>Комментарии</span>
              <strong>{short(total('all', 'comments'))}</strong>
              <small className={(total('all', 'newComments') ?? 0) > 0 ? 'up' : ''}>
                {total('all', 'newComments') !== null
                  ? `${total('all', 'newComments')! >= 0 ? '+' : ''}${fmt(total('all', 'newComments'))} за сутки`
                  : 'прирост — нужен снимок суточной давности'}
              </small>
            </div>
            <div className="st-kpi">
              <span>Вовлечённость</span>
              <strong>{avgEr === null ? '—' : avgEr.toFixed(1) + '%'}</strong>
              <small>(лайки + комм.) / просмотры</small>
            </div>
            <div className="st-kpi">
              <span>Роликов на площадках</span>
              <strong>{jobs.length}</strong>
              <small>
                {ready
                  ? `💰 к выплате: ${ready}`
                  : `со снимками: ${rows.filter((r) => r.views !== null).length}`}
              </small>
            </div>
          </div>
          <div className="st-card">
            <div className="st-card-head">
              <b>Просмотры по дням</b>
              <span className="st-legend">
                <i className="tt" /> TikTok <i className="yt" /> YouTube
              </span>
              <div className="st-tabs small">
                {[7, 14, 30].map((d) => (
                  <button key={d} className={days === d ? 'on' : ''} onClick={() => setDays(d)}>
                    {d} дн
                  </button>
                ))}
              </div>
            </div>
            {snapshots.length ? (
              <Chart points={seriesFor(jobs, snapshots, days)} />
            ) : (
              <p className="e-muted e-small st-empty">
                Снимков ещё нет — нажмите «Обновить» после первой публикации.
              </p>
            )}
          </div>
          {best && (
            <div className="st-best">
              <span className="st-chip">🏆 Лучший ролик</span>
              <b className="truncate">{best.job.config.title || best.job.asset_name}</b>
              <span className="e-muted">
                {PLATFORM[best.job.account.platform]} · {best.job.account.name}
              </span>
              <strong>{fmt(best.views)}</strong>
              {best.job.url && (
                <a href={best.job.url} target="_blank" rel="noreferrer">
                  Открыть
                </a>
              )}
            </div>
          )}
        </>
      )}

      {tab === 'videos' &&
        (rows.length ? (
          <>
            <table className="e-table st-table">
              <thead>
                <tr>
                  <th style={{ width: '30%' }}>Ролик</th>
                  <th>Просмотры</th>
                  <th>За сутки</th>
                  <th>Первые 24 ч</th>
                  <th>Лайки</th>
                  <th>Комм.</th>
                  <th>ER</th>
                  <th>Возраст</th>
                  <th style={{ width: 70 }}>Динамика</th>
                </tr>
              </thead>
              <tbody>
                {rows.slice(page * 6, page * 6 + 6).map((r) => (
                  <tr key={r.job.id}>
                    <td>
                      <b className="truncate" title={r.job.asset_name}>
                        {r.job.config.title || r.job.asset_name}
                      </b>
                      <small>
                        <span className={'st-pf ' + r.job.account.platform}>
                          {PLATFORM[r.job.account.platform]}
                        </span>{' '}
                        {r.job.account.name}
                        {r.job.url && (
                          <>
                            {' · '}
                            <a href={r.job.url} target="_blank" rel="noreferrer">
                              открыть
                            </a>
                          </>
                        )}
                      </small>
                    </td>
                    <td>
                      <b>{fmt(r.views)}</b>
                    </td>
                    <td className={r.growth ? 'st-up' : ''}>
                      {r.growth === null ? '—' : '+' + fmt(r.growth)}
                    </td>
                    <td>{fmt(r.day1)}</td>
                    <td>{fmt(r.likes)}</td>
                    <td>
                      {fmt(r.comments)}
                      {(r.newComments ?? 0) > 0 && <small className="st-up">+{fmt(r.newComments)}</small>}
                    </td>
                    <td>{r.er === null ? '—' : r.er.toFixed(1) + '%'}</td>
                    <td>{age(Date.now() - published(r.job))}</td>
                    <td>
                      <button
                        className="st-spark-btn"
                        title="Подробная динамика"
                        onClick={() => p.history(r.job.id)}
                      >
                        <Spark values={r.spark} />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <Pages page={page} set={setPage} total={rows.length} size={6} />
            <p className="stats-note">
              «Первые 24 ч» — просмотры на снимке не позже суток после публикации: так ролики
              сравниваются на одинаковом возрасте.
            </p>
          </>
        ) : (
          <Empty />
        ))}

      {tab === 'channels' &&
        (summaries.length ? (
          <div className="st-channels">
            {summaries.map((s) => (
              <div className="st-channel" key={s.account.id}>
                <div>
                  <span className={'st-pf ' + s.account.platform}>
                    {PLATFORM[s.account.platform]}
                  </span>
                  <b className="truncate" title={s.account.name}>
                    {s.account.name}
                  </b>
                  {!s.account.connected && <span className="e-badge bad">нужен вход</span>}
                </div>
                <strong>{short(s.views.value)}</strong>
                <small>
                  просмотров · роликов {s.jobs} · в среднем{' '}
                  {s.views.value !== null && s.views.covered
                    ? short(Math.round(s.views.value / s.views.covered))
                    : '—'}
                </small>
                <small>
                  лайки {fmt(s.likes.value)} · комментарии {fmt(s.comments.value)}
                </small>
              </div>
            ))}
          </div>
        ) : (
          <Empty />
        ))}

      {tab === 'payouts' &&
        (p.payouts.length ? (
          <div className="st-payouts">
            {p.payouts.slice(page * 4, page * 4 + 4).map((x) => {
              const chosen = x.publications.find((pub) => pub.job_id === x.chosen);
              const target = chosen ?? x.publications.find((pub) => pub.job_id === x.recommended);
              const left = x.deadline ? Date.parse(x.deadline) - Date.now() : null;
              const final = ['submitted', 'paid', 'rejected'].includes(x.phase);
              return (
                <div className={'st-payout ' + x.phase} key={x.asset_id}>
                  <div className="st-payout-head">
                    <b className="truncate">{x.title || x.name}</b>
                    <span className={'e-badge ' + PHASE[x.phase][1]}>{PHASE[x.phase][0]}</span>
                    <span className="e-muted e-small">
                      {left !== null && left > 0 ? `осталось ${age(left)}` : ''}
                    </span>
                  </div>
                  <div className="st-progress" title={`${fmt(target?.views)} из 10 000`}>
                    <i style={{ width: `${Math.round(x.progress * 100)}%` }} />
                  </div>
                  <small className="e-muted">
                    {fmt(target?.views)} / 10 000 просмотров
                    {chosen ? ` · заявка с ${PLATFORM[chosen.platform]}` : ' · площадка не выбрана'}
                  </small>
                  <div className="st-pubs">
                    {x.publications.map((pub) => (
                      <button
                        key={pub.job_id}
                        className={'st-pub' + (pub.job_id === x.chosen ? ' on' : '')}
                        disabled={p.busy || final}
                        onClick={() => p.choose(x.asset_id, pub.job_id)}
                        title="Выбрать эту публикацию для выплаты"
                      >
                        <span className={'st-pf ' + pub.platform}>{PLATFORM[pub.platform]}</span>
                        <b>{short(pub.views)}</b>
                        <small className="truncate">{pub.account}</small>
                        {pub.job_id === x.recommended && pub.job_id !== x.chosen && (
                          <em>больше просмотров</em>
                        )}
                      </button>
                    ))}
                  </div>
                  {chosen && x.phase === 'ready' && (
                    <button
                      className="e-button primary"
                      disabled={p.busy}
                      onClick={() => p.mark(chosen.job_id, 'submitted')}
                    >
                      Я подал заявку в боте БУБА
                    </button>
                  )}
                  {chosen && x.phase === 'submitted' && (
                    <div className="e-row">
                      <button
                        className="e-button"
                        disabled={p.busy}
                        onClick={() => p.mark(chosen.job_id, 'paid')}
                      >
                        Выплачено
                      </button>
                      <button
                        className="e-button"
                        disabled={p.busy}
                        onClick={() => p.mark(chosen.job_id, 'rejected')}
                      >
                        Отказ
                      </button>
                    </div>
                  )}
                </div>
              );
            })}
            <Pages page={page} set={setPage} total={p.payouts.length} size={4} />
            <p className="stats-note">
              Один ролик подаётся на выплату один раз — выберите площадку. Порог 10 000 просмотров,
              срок 7 дней; при готовности и в последний день придёт напоминание в Telegram.
            </p>
          </div>
        ) : (
          <div className="e-empty">
            <h2>Роликов БУБА пока нет</h2>
            <p>
              После публикации в режиме БУБА здесь появятся прогресс до 10 000 и выбор площадки для
              выплаты.
            </p>
          </div>
        ))}
    </section>
  );
}

function Empty() {
  return (
    <div className="e-empty">
      <h2>Данных ещё нет</h2>
      <p>Опубликуйте ролик и нажмите «Обновить» — появятся реальные счётчики площадок.</p>
    </div>
  );
}

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
  const pages = Math.ceil(total / size);
  if (pages < 2) return null;
  return (
    <div className="e-row st-pages">
      <button className="e-button" disabled={page === 0} onClick={() => set(page - 1)}>
        ‹
      </button>
      <span className="e-small e-muted">
        {page + 1} / {pages}
      </span>
      <button className="e-button" disabled={page >= pages - 1} onClick={() => set(page + 1)}>
        ›
      </button>
    </div>
  );
}
