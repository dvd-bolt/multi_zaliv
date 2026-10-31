export type Edit = {
  banner_id: string | null;
  banner_text: string;
  timing: 'middle' | 'manual';
  start: number;
  duration: number;
  freeze_video: boolean;
  banner_audio: boolean;
  banner_height: number | null;
  buba_mode: boolean;
  auto_hashtags: boolean;
  buba_review: Record<string, boolean>;
  x: number;
  y: number;
  width: number;
  opacity: number;
  brightness: number;
  contrast: number;
  saturation: number;
  warmth: number;
  zoom: number;
  mirror: boolean;
  format: '1080' | '720';
  fit: 'pad' | 'crop';
  banner_end: 'hold' | 'loop';
  caption: string;
  title: string;
  visibility: 'private' | 'unlisted' | 'public';
  synthetic: boolean;
  made_for_kids: boolean;
  targets: string[];
  genre: 'auto' | 'horror' | 'mystery' | 'comedy' | 'drama' | 'adventure' | 'scifi' | 'story';
  overrides: Record<string, Record<string, number | boolean>>;
};
export type Asset = {
  id: string;
  name: string;
  kind: 'video' | 'banner';
  media: {
    width: number;
    height: number;
    duration: number;
    has_audio: boolean;
    image?: boolean;
  };
  edit: Edit;
};
export type Account = {
  id: string;
  name: string;
  platform: 'youtube' | 'tiktok';
  connected: boolean;
  external_id: string | null;
  method: string;
  auth?: string;
};
export type Job = {
  id: string;
  external_id: string | null;
  status: string;
  progress: number;
  scheduled: string | null;
  approved: boolean;
  auto_publish: boolean;
  validation_upload: boolean;
  error: string | null;
  url: string | null;
  asset_name: string;
  asset_id: string;
  account: Account;
  config: Edit;
  has_output: boolean;
  updated: string;
  created: string;
  upload_started_at?: string | null;
};
export type Settings = {
  buba_required: boolean;
  paused: boolean;
  encoder: string;
  quality: string;
  timezone: string;
  interval: number;
  retention_days: number;
  project_name: string;
  notifications: boolean;
  youtube_category?: '1' | '24' | '23' | '22';
  one_per_platform?: boolean;
};
export type State = {
  default_hashtags: string[];
  genres?: Record<string, { label: string; tags: string[] }>;
  assets: Asset[];
  accounts: Account[];
  jobs: Job[];
  settings: Settings;
  worker: boolean;
  google_configured: boolean;
  telegram_configured?: boolean;
};
export type Metric = {
  id: string;
  job_id: string;
  captured: string;
  values: Record<string, number | null>;
  source: string;
};
export async function api<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const response = await fetch('/api' + path, {
    method,
    headers: body instanceof FormData ? {} : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : body instanceof FormData ? body : JSON.stringify(body),
  });
  if (!response.ok) {
    let message = 'Ошибка запроса';
    try {
      const data = await response.json();
      message = typeof data.detail === 'string' ? data.detail : 'Проверьте введённые значения';
    } catch {
      message = `Сервер недоступен (${response.status})`;
    }
    throw new Error(message);
  }
  return response.json();
}

export type Publication = {
  job_id: string;
  platform: 'youtube' | 'tiktok';
  account: string;
  url: string | null;
  views: number | null;
  captured: string | null;
  published_at: string;
  state: string | null;
};
export type Payout = {
  asset_id: string;
  name: string;
  title: string;
  publications: Publication[];
  chosen: string | null;
  recommended: string | null;
  deadline: string | null;
  progress: number;
  phase: 'choose' | 'collecting' | 'ready' | 'submitted' | 'paid' | 'rejected' | 'expired';
};
