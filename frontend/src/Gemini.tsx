import { useEffect, useRef, useState } from 'react';
import { api } from './api';

type Proposal = {
  title: string;
  description: string;
  hashtags: string[];
  uncertain: boolean;
  model: string;
};
type Task = { state: string; detail?: string; proposal?: Proposal };
type Config = { configured: boolean; auto: boolean; models: string[] };

export function useGemini(
  assetId: string,
  caption: string,
  title: string,
  buba: boolean,
  fixedTags: string[],
  apply: (value: { title: string; caption: string }) => void,
) {
  const [status, setStatus] = useState('');
  const [running, setRunning] = useState(false);
  const [failed, setFailed] = useState(false);
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const latest = useRef({ assetId, caption, title, buba, fixedTags, apply });
  latest.current = { assetId, caption, title, buba, fixedTags, apply };
  const sequence = useRef(0);

  function applyProposal(p: Proposal) {
    const tags = [...p.hashtags];
    for (const tag of latest.current.fixedTags)
      if (!tags.some((t) => t.toLowerCase() === tag.toLowerCase())) tags.push(tag);
    if (latest.current.buba && !tags.some((t) => t.toLowerCase() === '#бубавпн'))
      tags.push('#бубавпн');
    latest.current.apply({ title: p.title, caption: p.description + '\n\n' + tags.join(' ') });
    setProposal(null);
  }

  async function generate(force = false) {
    const initial = { ...latest.current };
    if (!initial.assetId) return;
    const generation = ++sequence.current;
    setRunning(true);
    setFailed(false);
    setStatus('Gemini анализирует исходник…');
    setProposal(null);
    try {
      let task = await api<Task>(`/assets/${initial.assetId}/ai-caption`, 'POST', { force });
      for (let i = 0; task.state === 'running' && i < 180; i++) {
        await new Promise((resolve) => setTimeout(resolve, 2000));
        if (generation !== sequence.current) return;
        task = await api<Task>(`/assets/${initial.assetId}/ai-caption`);
        if (task.state === 'running' && task.detail) setStatus(task.detail);
      }
      if (generation !== sequence.current || latest.current.assetId !== initial.assetId) return;
      if (task.state !== 'ready' || !task.proposal)
        throw new Error(task.detail || 'Анализ ещё выполняется. Нажмите позже для проверки');
      const p = task.proposal;
      if (latest.current.caption === initial.caption && latest.current.title === initial.title)
        applyProposal(p);
      else setProposal(p);
      setStatus(
        `${p.model.replace('gemini-', '')}${p.uncertain ? ' · проверь имена и сюжет' : ' · проверь текст'}`,
      );
    } catch (error) {
      if (generation === sequence.current) {
        setFailed(true);
        setStatus(error instanceof Error ? error.message : 'Gemini недоступен');
      }
    } finally {
      if (generation === sequence.current) setRunning(false);
    }
  }

  useEffect(() => {
    const generation = ++sequence.current;
    setStatus('');
    setProposal(null);
    setRunning(false);
    setFailed(false);
    if (assetId)
      void api<Config>('/ai')
        .then((config) => {
          if (generation !== sequence.current) return;
          const current = latest.current;
          if (
            config.configured &&
            config.auto &&
            !current.title.trim() &&
            !current.caption
              .replace(/#[\p{L}\p{N}_]+/gu, (tag) =>
                [...current.fixedTags, '#бубавпн'].some(
                  (fixed) => fixed.toLowerCase() === tag.toLowerCase(),
                )
                  ? ''
                  : tag,
              )
              .trim()
          )
            void generate();
        })
        .catch(() => setStatus('Нет связи с настройками Gemini'));
    return () => {
      sequence.current++;
    };
  }, [assetId]);

  return { status, failed, running, proposal, generate, applyProposal };
}

export function GeminiSettings() {
  const [config, setConfig] = useState<Config | null>(null);
  const [secret, setSecret] = useState('');
  const [status, setStatus] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    void api<Config>('/ai')
      .then(setConfig)
      .catch(() => setStatus('Нет связи с сервером'));
  }, []);
  async function run(task: () => Promise<void>) {
    setBusy(true);
    setStatus('');
    try {
      await task();
      setConfig(await api<Config>('/ai'));
    } catch (e) {
      setStatus(e instanceof Error ? e.message : 'Ошибка Gemini');
    } finally {
      setBusy(false);
    }
  }
  return (
    <div>
      <p className="e-small">
        Исходник со звуком передаётся в Google в уменьшенном виде, до вставки баннера. Результат
        нужно проверить перед отправкой. API может тарифицироваться по условиям твоего проекта.
      </p>
      <label className="e-label">
        API-ключ Gemini
        <input
          className="e-field"
          type="password"
          autoComplete="off"
          value={secret}
          maxLength={300}
          onChange={(e) => setSecret(e.target.value)}
          placeholder={config?.configured ? 'Ключ сохранён защищённо' : 'Вставь ключ локально'}
        />
      </label>
      <div className="e-row">
        <button
          className="e-button"
          disabled={busy || secret.trim().length < 15}
          onClick={() =>
            void run(async () => {
              await api('/ai/key', 'PUT', { api_key: secret.trim() });
              setSecret('');
              setStatus('Ключ сохранён');
            })
          }
        >
          Сохранить ключ
        </button>
        <button
          className="e-button"
          disabled={busy || !config?.configured}
          onClick={() =>
            void run(async () => {
              const result = await api<{ available: string[] }>('/ai/check', 'POST');
              setStatus('Доступны: ' + (result.available.join(' → ') || 'нет выбранных моделей'));
            })
          }
        >
          Проверить модели
        </button>
      </div>
      <p className="e-small e-muted" style={{ overflowWrap: 'anywhere' }}>
        {config?.models.join(' → ')}
      </p>
      <label className="e-check">
        <input
          type="checkbox"
          checked={!!config?.auto}
          disabled={busy || !config}
          onChange={(e) => {
            const auto = e.target.checked;
            void run(async () => {
              await api('/ai/options', 'PUT', { auto });
            });
          }}
        />
        Автоописание для исходника без текста
      </label>
      <p className="e-small e-muted">
        При квоте, тайм-ауте или временной ошибке сервера — следующая модель. Если квота общая для
        проекта, вся цепочка тоже может оказаться недоступной. Готовый результат сохраняется и
        повторно не расходует запрос.
      </p>
      <p className="e-small" role="status" style={{ overflowWrap: 'anywhere' }}>
        {busy ? 'Проверяю…' : status}
      </p>
    </div>
  );
}
