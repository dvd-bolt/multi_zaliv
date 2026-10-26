import { useEffect, useState } from 'react';
import { api, type Asset, type Edit, type Job } from './api';
type Check = {
  enabled: boolean;
  ready: boolean;
  area_percent: number;
  duration: number;
  issues: { code: string; message: string; level: string }[];
};
type Payout = {
  views: number | null;
  published_at: string | null;
  state: string;
  deadline: string | null;
  eligible_by_local_data: boolean;
};
type Checks = { checks: Check; payout: Payout };
type Comments = {
  status: string;
  checked: string | null;
  detail: string;
  flagged: number;
  comments: { id: string; text: string; reason: string; reviewed: boolean }[];
};
const labels: Record<string, string> = {
  profile_link: 'В шапке t.me/bubabot; нет чужих проектов и реферальных ссылок',
  voice: 'Озвучка разборчива, звук рекламы слышен',
  rights: 'Есть права на материал; чужой контент существенно переработан',
  audience: 'Ролик для русскоговорящей аудитории',
  clean: 'Нет хромакея, чужих знаков, политики, 18+ и шок-контента',
  no_promotion: 'Нет накрутки, платного продвижения и приманок на вовлечённость',
};
const tag = (s: string) =>
  /(?<![\p{L}\p{N}_])#бубавпн(?![\p{L}\p{N}_])/iu.test(s)
    ? s
    : s.trimEnd() + (s.trim() ? '\n' : '') + '#бубавпн';
function Pages({
  page,
  set,
  total,
  size = 3,
}: {
  page: number;
  set: (n: number) => void;
  total: number;
  size?: number;
}) {
  const max = Math.max(1, Math.ceil(total / size));
  return (
    <div className="e-row">
      <button className="e-button" disabled={page === 0} onClick={() => set(page - 1)}>
        ←
      </button>
      <span>
        {page + 1} / {max}
      </span>
      <button className="e-button" disabled={page + 1 >= max} onClick={() => set(page + 1)}>
        →
      </button>
    </div>
  );
}
function Tabs({ value, set, items }: { value: string; set: (s: string) => void; items: string[] }) {
  return (
    <div className="e-tabs">
      {items.map((t) => (
        <button key={t} aria-pressed={t === value} onClick={() => set(t)}>
          {t}
        </button>
      ))}
    </div>
  );
}
export function CampaignPanel({
  asset,
  draft,
  patch,
  banners,
  advanced,
}: {
  asset: Asset;
  draft: Edit;
  patch: (p: Partial<Edit>) => void;
  banners: Asset[];
  advanced: () => void;
}) {
  const [tab, setTab] = useState('Проверки'),
    [page, setPage] = useState(0),
    [checked, setChecked] = useState<Check | null>(null),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false);
  useEffect(() => {
    setChecked(null);
  }, [draft]);
  const banner = banners.find((b) => b.id === draft.banner_id);
  const entries = Object.entries(labels);
  const inspect = async () => {
    setBusy(true);
    setError('');
    try {
      setChecked(await api<Check>('/buba/check', 'POST', { asset_id: asset.id, edit: draft }));
      setPage(0);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Проверка не завершилась');
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="campaign-panel">
      <label className="e-check">
        <input
          type="checkbox"
          checked={!!draft.buba_mode}
          onChange={(e) =>
            patch({
              buba_mode: e.target.checked,
              caption: e.target.checked ? tag(draft.caption) : draft.caption,
            })
          }
        />
        Режим БУБА · обязательный #бубавпн
      </label>
      <Tabs
        value={tab}
        set={(s) => {
          setTab(s);
          setPage(0);
        }}
        items={['Проверки', 'Подтверждения', 'Баннер']}
      />
      {tab === 'Проверки' && (
        <>
          <p>Проверяются длительность, анимация, середина, площадь от 25%, звук и публичность.</p>
          <button className="e-button primary" disabled={busy} onClick={() => void inspect()}>
            Проверить настройки
          </button>
          {checked && (
            <>
              <p>
                <b>
                  {checked.ready ? 'Геометрия и отмеченные условия проверены' : 'Нужны исправления'}
                </b>{' '}
                · {checked.duration} с · баннер {checked.area_percent}%
              </p>
              <div className="campaign-list">
                {checked.issues.slice(page * 3, page * 3 + 3).map((i) => (
                  <p className={i.level === 'error' ? 'job-error' : 'e-small'} key={i.code}>
                    {i.message}
                  </p>
                ))}
              </div>
              <Pages page={page} set={setPage} total={checked.issues.length} />
            </>
          )}
          <p className="e-small">
            Один исходник нельзя повторно подавать на выплату с другого канала. Фильтры не
            доказывают существенную переработку. Проверка не гарантирует оплату.
          </p>
        </>
      )}
      {tab === 'Подтверждения' && (
        <>
          <p className="e-small">
            Подтверждай после просмотра MP4. Эти свойства программа сама не распознаёт.
          </p>
          <div className="campaign-list">
            {entries.slice(page * 3, page * 3 + 3).map(([key, text]) => (
              <label className="e-check" key={key}>
                <input
                  type="checkbox"
                  checked={!!draft.buba_review?.[key]}
                  onChange={(e) =>
                    patch({ buba_review: { ...draft.buba_review, [key]: e.target.checked } })
                  }
                />
                {text}
              </label>
            ))}
          </div>
          <Pages page={page} set={setPage} total={entries.length} />
        </>
      )}
      {tab === 'Баннер' && (
        <>
          <p>
            Ширина 100%, высота 26%, центр, полная длина рекламы. Пропорции немного изменятся,
            содержимое останется целиком.
          </p>
          <button
            className="e-button primary"
            disabled={!banner}
            onClick={() =>
              patch({
                buba_mode: true,
                caption: tag(draft.caption),
                width: 100,
                banner_height: 26,
                x: 50,
                y: 50,
                timing: 'middle',
                opacity: 100,
                banner_audio: !!banner?.media.has_audio,
                duration: Math.min(120, Math.max(0.1, banner?.media.duration || draft.duration)),
              })
            }
          >
            Подогнать баннер до 26%
          </button>
          <label className="e-label">
            Высота, % (меняет пропорции)
            <input
              className="e-field"
              type="number"
              min="5"
              max="100"
              value={draft.banner_height ?? ''}
              onChange={(e) =>
                patch({ banner_height: e.target.value ? Number(e.target.value) : null })
              }
            />
          </label>
          <button className="e-button" onClick={() => patch({ banner_height: null })}>
            Исходные пропорции
          </button>{' '}
          <button className="e-button" onClick={advanced}>
            Другие параметры
          </button>
          <p className="e-small">
            Для 2+ минут нужны несколько баннеров; неоднозначные тайминги правил требуют уточнения.
            Их отправка пока останавливается.
          </p>
        </>
      )}
      {error && (
        <p className="job-error" role="alert">
          {error}
        </p>
      )}
      <a className="text-link" href="https://mediabuba.ru/pravila" target="_blank" rel="noreferrer">
        Открыть правила ↗
      </a>
    </div>
  );
}
export function JobCampaignPanel({ job }: { job: Job }) {
  const [tab, setTab] = useState('Правила'),
    [data, setData] = useState<Checks | null>(null),
    [comments, setComments] = useState<Comments | null>(null),
    [page, setPage] = useState(0),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false),
    [published, setPublished] = useState(''),
    [state, setState] = useState('tracking'),
    [confirmed, setConfirmed] = useState(false);
  useEffect(() => {
    let active = true;
    Promise.all([api<Checks>(`/jobs/${job.id}/buba`), api<Comments>(`/jobs/${job.id}/comments`)])
      .then(([a, b]) => {
        if (active) {
          setData(a);
          setComments(b);
          setState(a.payout.state === 'untracked' ? 'tracking' : a.payout.state);
        }
      })
      .catch((e) => {
        if (active) setError(e.message);
      });
    return () => {
      active = false;
    };
  }, [job.id]);
  const run = async (task: () => Promise<void>) => {
    setBusy(true);
    setError('');
    try {
      await task();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Действие не выполнено');
    } finally {
      setBusy(false);
    }
  };
  const flagged = comments?.comments.filter((c) => c.reason && !c.reviewed) ?? [];
  return (
    <div className="campaign-panel">
      <Tabs
        value={tab}
        set={(s) => {
          setTab(s);
          setPage(0);
        }}
        items={['Правила', 'Комментарии', 'Выплата']}
      />
      {tab === 'Правила' && data && (
        <>
          <p>
            {data.checks.enabled
              ? 'Режим БУБА включён'
              : 'Старый вариант без режима БУБА: исправь настройки в монтажной и создай новый MP4.'}
          </p>
          <p>
            Длительность {data.checks.duration} с · площадь баннера {data.checks.area_percent}%
          </p>
          <div className="campaign-list">
            {data.checks.issues.slice(page * 3, page * 3 + 3).map((i) => (
              <p className={i.level === 'error' ? 'job-error' : 'e-small'} key={i.code}>
                {i.message}
              </p>
            ))}
          </div>
          <Pages page={page} set={setPage} total={data.checks.issues.length} />
        </>
      )}
      {tab === 'Комментарии' && (
        <>
          <p>{comments?.detail ?? 'Загрузка…'}</p>
          <button
            className="e-button"
            disabled={busy || !job.external_id}
            onClick={() =>
              void run(async () => {
                setComments(await api<Comments>(`/jobs/${job.id}/comments/scan`, 'POST'));
                setPage(0);
              })
            }
          >
            {busy ? 'Проверка…' : 'Обновить комментарии'}
          </button>
          <p className="e-small">
            {comments?.status === 'ok'
              ? 'Непроверенных подозрений'
              : 'Подозрений в прочитанных данных'}
            : {comments?.status === 'never' ? 'ещё не проверялось' : (comments?.flagged ?? '—')}.
            Отметка ничего не удаляет на платформе.
          </p>
          <div className="campaign-list">
            {flagged.slice(page, page + 1).map((c) => (
              <div key={c.id}>
                <p title={c.text}>
                  {c.text.slice(0, 160)}
                  {c.text.length > 160 ? '…' : ''}
                </p>
                <small>{c.reason}</small>
                <button
                  className="text-link"
                  disabled={busy}
                  onClick={() =>
                    void run(async () => {
                      await api(`/comments/${c.id}`, 'PATCH', { reviewed: true });
                      setComments(await api<Comments>(`/jobs/${job.id}/comments`));
                      setPage(0);
                    })
                  }
                >
                  Проверено вручную
                </button>
              </div>
            ))}
          </div>
          <Pages page={page} set={setPage} total={flagged.length} size={1} />
          {job.url && (
            <a className="text-link" target="_blank" rel="noreferrer" href={job.url}>
              Открыть ролик для ручной проверки ↗
            </a>
          )}
        </>
      )}
      {tab === 'Выплата' && data && (
        <>
          <p className="e-small">
            Просмотры: {data.payout.views ?? 'нет данных'} · состояние: {data.payout.state}. Порог
            10 000, срок — 7 календарных дней по Москве.
          </p>
          {data.payout.deadline && (
            <p className="e-small">
              Граница срока: {new Date(data.payout.deadline).toLocaleString('ru-RU')}
            </p>
          )}
          <label className="e-label">
            Фактическая публикация (местное время)
            <input
              className="e-field"
              type="datetime-local"
              value={published}
              onChange={(e) => setPublished(e.target.value)}
            />
          </label>
          <label className="e-check">
            <input
              type="checkbox"
              checked={confirmed}
              onChange={(e) => setConfirmed(e.target.checked)}
            />
            Подтверждаю время и публичность ролика
          </label>
          <label className="e-label">
            Учёт заявки
            <select className="e-field" value={state} onChange={(e) => setState(e.target.value)}>
              <option value="tracking">Наблюдаю</option>
              <option value="submitted">Уже подал через бот</option>
              <option value="paid">Выплачено</option>
              <option value="rejected">Отказано</option>
            </select>
          </label>
          <button
            className="e-button primary"
            disabled={busy || !published || !confirmed || !job.external_id}
            onClick={() =>
              void run(async () => {
                const payout = await api<Payout>(`/jobs/${job.id}/buba`, 'POST', {
                  published_at: new Date(published).toISOString(),
                  public_confirmed: confirmed,
                  state,
                });
                setData({ ...data, payout });
              })
            }
          >
            Сохранить отметку
          </button>
          <p className="e-small">
            Это не отправка в бот. Нужна запись экрана с телефона. После подачи нельзя скрывать,
            удалять или редактировать ролик. Один исходник — одна заявка между всеми каналами.
          </p>
        </>
      )}
      {busy && <span role="status"> Подождите…</span>}
      {error && (
        <p className="job-error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
