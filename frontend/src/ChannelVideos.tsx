import { useEffect, useState } from 'react';
import { api, type Account } from './api';

type Video = {
  external_id: string;
  title: string;
  present: boolean;
  job_id: string | null;
  url: string;
  published_at: string;
  visibility: string;
  values: Record<string, number | null>;
};
type Library = { sync: { captured?: string; error?: string } | null; videos: Video[] };
const fmt = (v: number | null | undefined) => (v == null ? '—' : v.toLocaleString('ru-RU'));

export default function ChannelVideos({
  account,
  changed,
}: {
  account: Account;
  changed: () => Promise<void>;
}) {
  const [data, setData] = useState<Library | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [page, setPage] = useState(0);
  const [showMissing, setShowMissing] = useState(false);
  useEffect(() => {
    let active = true;
    api<Library>(`/accounts/${account.id}/videos`).then(
      (result) => {
        if (active) setData(result);
      },
      (e: Error) => {
        if (active) setError(e.message);
      },
    );
    return () => {
      active = false;
    };
  }, [account.id]);
  const rows = (data?.videos ?? [])
    .filter((v) => showMissing || v.present)
    .sort((a, b) => Date.parse(b.published_at) - Date.parse(a.published_at));
  const pages = Math.max(1, Math.ceil(rows.length / 3));
  const selectedPage = Math.min(page, pages - 1);
  async function refresh() {
    setBusy(true);
    setError('');
    try {
      await api(`/accounts/${account.id}/videos/refresh`, 'POST');
      setData(await api<Library>(`/accounts/${account.id}/videos`));
      await changed();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <p className="e-small">
        {account.name} · все доступные видео канала, включая загруженные вручную.
      </p>
      <div className="e-row e-between">
        <button
          className="e-button"
          disabled={busy || !account.connected}
          onClick={() => void refresh()}
        >
          {busy ? 'Обновляю все страницы…' : 'Обновить с YouTube'}
        </button>
        <label className="e-check">
          <input
            type="checkbox"
            checked={showMissing}
            onChange={(e) => {
              setShowMissing(e.target.checked);
              setPage(0);
            }}
          />
          Показать недоступные
        </label>
      </div>
      <p className="e-small e-muted" role="status">
        {data?.sync?.captured
          ? `Обновлено ${new Date(data.sync.captured).toLocaleString('ru-RU', { timeZone: 'Europe/Moscow' })} МСК`
          : 'Нажмите «Обновить с YouTube», чтобы получить список.'}
      </p>
      {(error || data?.sync?.error) && (
        <p className="e-modal-error" role="alert">
          {error || data?.sync?.error}
        </p>
      )}
      <div className="channel-videos">
        {rows.slice(selectedPage * 3, selectedPage * 3 + 3).map((v) => (
          <article key={v.external_id}>
            <div className="e-row e-between">
              <a
                className="truncate text-link"
                title={v.title}
                href={v.url}
                target="_blank"
                rel="noreferrer"
              >
                {v.title} ↗
              </a>
              <span className={'e-badge' + (v.present ? '' : ' bad')}>
                {v.present
                  ? ({ public: 'Открытый', private: 'Только я', unlisted: 'По ссылке' }[
                      v.visibility
                    ] ?? v.visibility)
                  : 'Недоступен'}
              </span>
            </div>
            <small className="e-muted">
              {v.job_id ? 'FrameFlow' : 'Загружено вручную'} ·{' '}
              {new Date(v.published_at).toLocaleDateString('ru-RU', { timeZone: 'Europe/Moscow' })}
            </small>
            <p className="e-small">
              Просмотры {fmt(v.values.views)} · Лайки {fmt(v.values.likes)} · Комментарии{' '}
              {fmt(v.values.comments)}
            </p>
          </article>
        ))}
        {!rows.length && (
          <p className="e-muted">
            {data ? 'Видео пока не найдены.' : 'Загружаю сохранённый список…'}
          </p>
        )}
      </div>
      <div className="e-row e-between">
        <button
          className="e-button"
          disabled={selectedPage === 0}
          onClick={() => setPage(selectedPage - 1)}
        >
          Назад
        </button>
        <span className="e-small">
          {selectedPage + 1} / {pages} · Видео: {rows.length}
        </span>
        <button
          className="e-button"
          disabled={selectedPage + 1 >= pages}
          onClick={() => setPage(selectedPage + 1)}
        >
          Далее
        </button>
      </div>
    </>
  );
}
