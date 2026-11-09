import { useEffect, useState } from 'react';
import type { Account } from './api';

type Sample = {
  captured: string;
  source: string;
  values: Record<string, number | null>;
  quality?: string;
  offset_seconds?: number;
};
type Row = {
  id: string;
  title: string;
  account_id: string;
  account_name: string;
  published_at: string | null;
  hour: Sample | null;
  day: Sample | null;
  current: Sample;
};
type Cell = { mean: number | null; median: number | null; n: number; approximate: number };
type Report = {
  rows: Row[];
  cells: Record<string, Record<string, Cell>>;
  count: number;
  unknown_publication_time: number;
};
const stages = ['hour', 'day', 'current'] as const;
const format = (v: number | null | undefined, percent = false) =>
  v == null
    ? '—'
    : v.toLocaleString('ru-RU', { maximumFractionDigits: percent ? 1 : 0 }) + (percent ? '%' : '');

export default function Averages({
  accounts,
  revision,
  timezone,
}: {
  accounts: Account[];
  revision: string;
  timezone: string;
}) {
  const [platform, setPlatform] = useState('youtube'),
    [account, setAccount] = useState(''),
    [days, setDays] = useState('30');
  const [report, setReport] = useState<Report | null>(null),
    [error, setError] = useState(''),
    [loading, setLoading] = useState(true);
  const [median, setMedian] = useState(true),
    [video, setVideo] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError('');
    setReport(null);
    const params = new URLSearchParams({ platform, account_id: account, days });
    void fetch('/api/analytics/averages?' + params, { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error('Не удалось рассчитать средние. Повторите обновление.');
        return response.json() as Promise<Report>;
      })
      .then((data) => {
        setReport(data);
        setLoading(false);
      })
      .catch((e) => {
        if (!controller.signal.aborted) {
          setError(e.message);
          setLoading(false);
        }
      });
    return () => controller.abort();
  }, [platform, account, days, revision]);
  const eligible = report?.rows.filter((r) => r.day?.values.views != null) ?? [];
  const selected = eligible.find((r) => r.id === video) ?? eligible[0];
  const peers = eligible.filter(
    (r) => r.id !== selected?.id && r.account_id === selected?.account_id,
  );
  const baseline = peers.length
    ? peers.reduce((sum, r) => sum + r.day!.values.views!, 0) / peers.length
    : null;
  const value = selected?.day?.values.views;
  const latest = report?.rows
    .map((r) => r.current?.captured)
    .filter(Boolean)
    .sort()
    .at(-1);
  return (
    <div className="st-averages">
      <div className="st-average-filters">
        <div className="st-tabs small" aria-label="Платформа средних">
          {['youtube', 'tiktok'].map((p) => (
            <button
              key={p}
              className={platform === p ? 'on' : ''}
              aria-pressed={platform === p}
              onClick={() => {
                setPlatform(p);
                setAccount('');
              }}
            >
              {p === 'youtube' ? 'YouTube' : 'TikTok'}
            </button>
          ))}
        </div>
        <label>
          Канал{' '}
          <select
            aria-label="Канал средних"
            value={account}
            onChange={(e) => setAccount(e.target.value)}
          >
            <option value="">Все каналы</option>
            {accounts
              .filter((a) => a.platform === platform)
              .map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name}
                </option>
              ))}
          </select>
        </label>
        <label>
          Опубликованы за{' '}
          <select
            aria-label="Период средних"
            value={days}
            onChange={(e) => setDays(e.target.value)}
          >
            {[
              ['7', '7 дней'],
              ['14', '14 дней'],
              ['30', '30 дней'],
              ['90', '90 дней'],
              ['0', 'Всё время'],
            ].map(([v, text]) => (
              <option key={v} value={v}>
                {text}
              </option>
            ))}
          </select>
        </label>
      </div>
      {error && <p role="alert">{error}</p>}
      {loading && (
        <p className="e-muted" role="status">
          Расчёт по сохранённым наблюдениям…
        </p>
      )}
      {report && (
        <>
          <div className="e-row e-between">
            <h2>В среднем на один ролик</h2>
            <span className="e-muted">Роликов: {report.count}</span>
          </div>
          <table className="st-average-table">
            <caption className="sr-only">Средние показатели по возрасту после публикации</caption>
            <thead>
              <tr>
                <th>Показатель</th>
                <th>Первый час</th>
                <th>Первые 24 ч</th>
                <th>Сейчас</th>
              </tr>
            </thead>
            <tbody>
              {(
                [
                  ['views', 'Просмотры'],
                  ['likes', 'Лайки'],
                  ['comments', 'Комментарии'],
                  ['er', 'ER'],
                ] as const
              ).map(([key, label]) => (
                <tr key={key}>
                  <th scope="row">{label}</th>
                  {stages.map((stage) => {
                    const c = report.cells[stage][key];
                    return (
                      <td key={stage}>
                        <strong>{format(c.mean, key === 'er')}</strong>
                        <small>
                          n = {c.n}
                          {c.approximate ? ` · ≈ ${c.approximate}` : ''}
                        </small>
                      </td>
                    );
                  })}
                </tr>
              ))}
              {median && (
                <tr className="e-muted">
                  <th scope="row">Медиана просмотров</th>
                  {stages.map((stage) => (
                    <td key={stage}>{format(report.cells[stage].views.median)}</td>
                  ))}
                </tr>
              )}
            </tbody>
          </table>
          <div className="st-average-note">
            <span>n — роликов с данными · ≈ — приблизительные замеры</span>
            <label>
              <input
                type="checkbox"
                checked={median}
                onChange={(e) => setMedian(e.target.checked)}
              />{' '}
              Медиана просмотров
            </label>
          </div>
          <details className="st-average-details">
            <summary>
              Полнота и точность данных
              {report.unknown_publication_time
                ? ` · без времени публикации: ${report.unknown_publication_time}`
                : ''}
            </summary>
            <p>
              Пропуски не считаются нулями. ER — среднее (лайки + комментарии) / просмотры по
              роликам с полными данными. Допуски наблюдения: ±10 мин у часа, ±1 ч у суток; это
              наблюдения счётчиков с возможной задержкой платформы. «Сейчас» включает разный возраст
              роликов.
            </p>
            <p>
              {days !== '0'
                ? 'Без подтверждённого времени публикации ролики исключены из периода. Выберите «Всё время» для их текущих значений. '
                : ''}
              {latest
                ? `Последний снимок: ${new Date(latest).toLocaleString('ru-RU', { timeZone: timezone })}.`
                : 'Снимков пока нет. Нажмите «Обновить».'}
            </p>
            <p>
              Источники:{' '}
              {[...new Set(report.rows.map((r) => r.current?.source).filter(Boolean))].join(', ') ||
                'нет данных'}
              .
            </p>
          </details>
          <div className="st-average-compare">
            <div className="e-row e-between">
              <h3>Ролик относительно своего канала</h3>
              <span className="e-muted">Первые 24 часа</span>
            </div>
            {eligible.length ? (
              <div className="st-average-comparison">
                <select
                  aria-label="Ролик для сравнения"
                  value={selected?.id ?? ''}
                  onChange={(e) => setVideo(e.target.value)}
                >
                  {eligible.map((r) => (
                    <option key={r.id} value={r.id}>
                      {r.title} · {r.account_name}
                    </option>
                  ))}
                </select>
                <div>
                  <strong>{format(value)}</strong>
                  <small>Этот ролик{selected?.day?.quality === 'approximate' ? ' · ≈' : ''}</small>
                </div>
                <div>
                  <strong>{format(baseline)}</strong>
                  <small>Остальные в канале · n={peers.length}</small>
                </div>
                <div>
                  <strong>
                    {baseline && value != null
                      ? `${value >= baseline ? '+' : ''}${format(100 * (value / baseline - 1), true)}`
                      : '—'}
                  </strong>
                  <small>К среднему</small>
                </div>
              </div>
            ) : (
              <p className="e-muted">
                Пока нет пригодных замеров за первые 24 часа. Они появятся по мере сбора.
              </p>
            )}
          </div>
        </>
      )}
    </div>
  );
}
