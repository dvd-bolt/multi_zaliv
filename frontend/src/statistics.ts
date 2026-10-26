import type { Account, Job, Metric } from './api';

// Quote fields and neutralize spreadsheet formulas in user-controlled names.
export function csvReport(rows: (string | number | null)[][]): string {
  return (
    '\uFEFF' +
    rows
      .map((row) =>
        row
          .map((value) => {
            let cell = value === null ? '' : String(value);
            if (typeof value === 'string' && /^[\s\u0000-\u001f]*[=+@-]/.test(cell))
              cell = "'" + cell;
            return '"' + cell.replaceAll('"', '""') + '"';
          })
          .join(','),
      )
      .join('\r\n') +
    '\r\n'
  );
}

export function channelsReport(rows: ChannelSummary[]): string {
  return csvReport([
    [
      'platform',
      'account',
      'tasks',
      'videos_with_snapshots',
      'views',
      'views_coverage',
      'likes',
      'likes_coverage',
      'comments',
      'comments_coverage',
      'latest_snapshot',
      'sources',
    ],
    ...rows.map((r) => [
      r.account.platform,
      r.account.name,
      r.jobs,
      r.measured,
      r.views.value,
      r.views.covered,
      r.likes.value,
      r.likes.covered,
      r.comments.value,
      r.comments.covered,
      r.captured,
      r.sources.join('; '),
    ]),
  ]);
}

// Counters are cumulative. Select one snapshot per video before aggregating;
// the result describes current known totals, not growth during a period.
export function latestMetrics(metrics: Metric[]): Metric[] {
  const latest = new Map<string, Metric>();
  for (const metric of metrics) {
    const previous = latest.get(metric.job_id);
    if (!previous || Date.parse(metric.captured) >= Date.parse(previous.captured))
      latest.set(metric.job_id, metric);
  }
  return [...latest.values()].sort((a, b) => Date.parse(b.captured) - Date.parse(a.captured));
}

export type Counter = { value: number | null; covered: number };
export type ChannelSummary = {
  account: Account;
  jobs: number;
  measured: number;
  captured: string | null;
  sources: string[];
  views: Counter;
  likes: Counter;
  comments: Counter;
};

export function channelSummaries(
  accounts: Account[],
  jobs: Job[],
  snapshots: Metric[],
): ChannelSummary[] {
  const latest = new Map(latestMetrics(snapshots).map((m) => [m.job_id, m]));
  const grouped = new Map<string, Job[]>();
  for (const job of jobs) {
    const rows = grouped.get(job.account.id) ?? [];
    rows.push(job);
    grouped.set(job.account.id, rows);
  }
  return accounts.map((account) => {
    const rows = grouped.get(account.id) ?? [];
    const measured = rows.flatMap((job) => {
      const snapshot = latest.get(job.id);
      return snapshot ? [snapshot] : [];
    });
    const counter = (key: string): Counter => {
      const values = measured.flatMap((m) => {
        const value = m.values[key];
        return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? [value] : [];
      });
      return {
        value: values.length ? values.reduce((sum, v) => sum + v, 0) : null,
        covered: values.length,
      };
    };
    return {
      account,
      jobs: rows.length,
      measured: measured.length,
      captured: measured.reduce<string | null>(
        (date, m) => (!date || Date.parse(m.captured) > Date.parse(date) ? m.captured : date),
        null,
      ),
      sources: [...new Set(measured.map((m) => m.source))].sort(),
      views: counter('views'),
      likes: counter('likes'),
      comments: counter('comments'),
    };
  });
}
