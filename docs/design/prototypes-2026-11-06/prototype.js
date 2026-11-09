/* Everything is browser-local. No production API, accounts, or worker are used. */
(() => {
  'use strict';
  const theme = document.body.dataset.theme;
  const titles = { studio: 'Studio', precision: 'Precision', focus: 'Focus' };
  const paths = {
    frame: '<path d="M4 8V4h4m8 0h4v4M4 16v4h4m8 0h4v-4M8 8h8v8H8z"/>',
    edit: '<rect x="3" y="5" width="18" height="15" rx="2"/><path d="m3 10 18-5M7 4l3 5m3-6 3 4M10 13l5 2.5-5 2.5z"/>',
    queue: '<rect x="4" y="4" width="16" height="5" rx="1"/><path d="M4 14h16M4 19h10"/>',
    users:
      '<circle cx="9" cy="8" r="3"/><path d="M3 20v-2a6 6 0 0 1 12 0v2m1-15a3 3 0 0 1 0 6m2 3a5 5 0 0 1 3 5"/>',
    chart: '<path d="M4 4v16h16M8 15v-4m5 4V7m5 8v-6"/>',
    settings:
      '<path d="M4 7h9m4 0h3M4 17h3m4 0h9"/><circle cx="15" cy="7" r="2"/><circle cx="9" cy="17" r="2"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    upload: '<path d="M12 16V4m-4 4 4-4 4 4M4 16v4h16v-4"/>',
    play: '<path d="m9 5 10 7-10 7z"/>',
    pause: '<path d="M9 5v14m6-14v14"/>',
    check: '<path d="m5 12 4 4L19 6"/>',
    back: '<path d="m15 6-6 6 6 6"/>',
    forward: '<path d="m9 6 6 6-6 6"/>',
    close: '<path d="m6 6 12 12M6 18 18 6"/>',
    save: '<path d="M5 3h12l4 4v14H3V3h2zm1 0v6h10V3M7 21v-8h10v8"/>',
    expand: '<path d="M4 9V4h5m6 0h5v5M4 15v5h5m6 0h5v-5"/>',
    image:
      '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="8" cy="9" r="1.5"/><path d="m3 17 6-5 4 3 3-4 5 6"/>',
    filter:
      '<path d="M5 5h14M7 12h10m-7 7h4"/><circle cx="8" cy="5" r="2"/><circle cx="15" cy="12" r="2"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    folder: '<path d="M3 7V5h7l2 2h9v13H3z"/>',
    eye: '<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
    reset: '<path d="M4 10a8 8 0 1 1 1 8M4 4v6h6"/>',
    download: '<path d="M12 3v13m-4-4 4 4 4-4M4 17v4h16v-4"/>',
    info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10v.1"/>',
    sound: '<path d="m4 9 4 0 5-4v14l-5-4H4zm13-1a7 7 0 0 1 0 8m2-11a11 11 0 0 1 0 14"/>',
    copy: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M15 8V4H4v11h4"/>',
  };
  const icon = (name) =>
    `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">${paths[name] || paths.frame}</svg>`;
  const esc = (value) =>
    String(value).replace(
      /[&<>"']/g,
      (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c],
    );
  const clock = (n) =>
    `${String(Math.floor(n / 60)).padStart(2, '0')}:${String(Math.floor(n % 60)).padStart(2, '0')}`;
  const defaults = () => ({
    start: 6,
    duration: 2.5,
    width: 84,
    position: 74,
    opacity: 100,
    text: 'Твоя история. Твой следующий кадр.',
    preset: 'original',
    brightness: 100,
    contrast: 100,
    saturation: 100,
    warmth: 0,
    mirror: false,
    timing: 'middle',
    title: 'Закрой дверь | Эпизод 01',
    caption: 'Новая история начинается с одного решения.',
    visibility: 'private',
    date: '2026-11-06T19:00',
    channels: [true, true, true, true, true, true],
    auto: false,
  });
  let saved = null;
  try {
    saved = JSON.parse(localStorage.getItem('frameflow-prototype-settings'));
  } catch {
    /* A restricted storage mode still supports the prototype. */
  }
  const initial = { ...defaults(), ...(saved || {}) };
  let edits = [initial, defaults(), defaults()];
  let selected = 0,
    tab = 'banner',
    section = 'editor',
    before = false,
    safe = false,
    playTime = 6,
    toastTimer;
  let queue = [];
  let lastTrigger = null;
  const sources = [
    {
      title: 'Закрой дверь',
      file: 'episode_01.mp4',
      poster: 'assets/source-1.png',
      video: 'assets/preview.mp4',
      duration: 12,
    },
    {
      title: 'Не открывай',
      file: 'episode_02.mp4',
      poster: 'assets/source-2.png',
      video: 'assets/preview-2.mp4',
      duration: 12,
    },
    {
      title: 'Эпизод 03',
      file: 'episode_03.mp4',
      poster: 'assets/source-3.png',
      video: 'assets/preview-3.mp4',
      duration: 12,
    },
  ];
  const current = () => edits[selected];
  const channels = [
    'TikTok 01',
    'YouTube 01',
    'TikTok 02',
    'YouTube 02',
    'TikTok 03',
    'YouTube 03',
  ];
  const count = () => current().channels.filter(Boolean).length * sources.length;
  const app = document.querySelector('#app');
  const navItem = (id, name, glyph) =>
    `<button class="nav-button" data-nav="${id}" ${id === section ? 'aria-current="page"' : ''}>${icon(glyph)}<span>${name}</span></button>`;
  app.innerHTML = `
    <a class="skip" href="#workspace">К редактору</a>
    <div class="prototype-bar"><div class="prototype-label"><strong>FrameFlow / ${titles[theme]}</strong><span>Прототип · без публикаций</span></div><nav class="prototype-links" aria-label="Варианты дизайна">${Object.entries(
      titles,
    )
      .map(
        ([id, name]) =>
          `<a href="${id}.html" ${id === theme ? 'aria-current="page"' : ''}>${name}</a>`,
      )
      .join('')}</nav><a class="overview-link" href="index.html">Все варианты</a></div>
    <div class="shell">
      <header class="topbar"><a href="index.html" class="brand" aria-label="FrameFlow — все прототипы"><span class="brand-mark">${icon('frame')}</span>FrameFlow</a><span class="project-name"><span class="muted">Проект /</span>Новый выпуск</span><div class="header-actions"><span class="saved"><i class="dot"></i><span id="saved-label">Изменения сохранены</span></span><button class="btn" data-action="save" aria-label="Сохранить настройки макета">${icon('save')}<span class="save-label">Сохранить</span></button><button class="icon-btn" data-action="about" aria-label="О прототипе">${icon('info')}</button></div></header>
      <main id="workspace" class="workspace" tabindex="-1">
        <nav class="rail" aria-label="Основные разделы">${navItem('editor', 'Монтажная', 'edit')}${navItem('queue', 'Очередь', 'queue')}${navItem('channels', 'Каналы', 'users')}${navItem('stats', 'Статистика', 'chart')}<div class="rail-bottom">${navItem('settings', 'Настройки', 'settings')}</div></nav>
        <aside class="sources" aria-label="Исходники"><div class="panel-heading"><h2>Исходники <span class="count">3</span></h2><span class="tag">9:16</span></div><p class="sources-summary">Пакет на 6 ноября</p><div class="source-list">${sources.map((s, i) => `<button class="source" data-source="${i}" aria-pressed="${i === 0}"><img src="${s.poster}" alt="Кадр: ${esc(s.title)}"><span><span class="source-title">${esc(s.title)}</span><span class="source-subtitle numbers">${clock(s.duration)} · 1080 × 1920</span></span><span class="source-check">${icon('check')}</span></button>`).join('')}</div><div class="source-footer"><button class="btn import-btn" data-action="import">${icon('plus')}Добавить видео</button><p>MP4, MOV, WebM</p></div></aside>
        <section class="editor" aria-label="Предпросмотр и таймлайн">
          <div class="viewer-top"><span class="viewer-title">${icon('play')}<span id="viewer-file">episode_01.mp4</span></span><span class="viewer-meta numbers">1080 × 1920 / 30 fps</span></div>
          <div class="stage"><div class="frame" id="video-frame"><img class="frame-media" id="poster" src="assets/source-1.png" alt="Предпросмотр выбранного исходника"><video class="frame-media" id="video" src="assets/preview.mp4" poster="assets/source-1.png" preload="metadata" playsinline muted hidden aria-label="Короткий просмотр исходника"></video><div class="safe-guide" id="safe-guide" hidden><span>Безопасная область</span></div><div class="banner-overlay" id="banner-overlay">Твоя история. Твой следующий кадр.</div></div><span class="stage-note">${icon('eye')}Предпросмотр макета</span><div class="stage-tools"><button class="icon-btn" data-action="safe" aria-label="Показать безопасную область" aria-pressed="false">${icon('frame')}</button><button class="icon-btn" data-action="fullscreen" aria-label="Развернуть предпросмотр">${icon('expand')}</button></div></div>
          <div class="transport"><div class="row"><button class="icon-btn" data-action="rewind" aria-label="Назад на секунду">${icon('back')}</button><button class="icon-btn play-button" data-action="play" aria-label="Воспроизвести">${icon('play')}</button><button class="icon-btn" data-action="forward" aria-label="Вперёд на секунду">${icon('forward')}</button><span class="timecode numbers"><span id="timecode">00:06</span><span class="muted">/ <span id="duration-label">00:12</span></span></span></div><div class="transport-right"><button class="btn quiet" data-action="before" aria-pressed="false">До / после</button><span class="tag">9:16</span></div></div>
          <div class="timeline"><span class="timeline-name">Таймлайн</span><div class="ruler"><span>00:00</span><span>00:03</span><span>00:06</span><span>00:09</span><span id="ruler-end">00:12</span></div><span class="timeline-name">Видео</span><div class="timeline-scrub"><div class="filmstrip" id="filmstrip">${Array.from({ length: 9 }, () => '<img src="assets/source-1.png" alt="">').join('')}</div><div class="playhead" id="playhead"></div><input class="scrub" id="scrub" type="range" min="0" max="12" value="6" step=".1" aria-label="Позиция просмотра в секундах"></div><span class="timeline-name">Баннер</span><div class="track-banner"><div class="track-region" id="track-region">2,5 с</div></div></div>
        </section>
        <aside class="inspector" aria-label="Настройки ролика"><div class="inspector-heading"><h2>Настройки ролика</h2><span class="tag" id="source-index">1 из 3</span></div><div class="tabs" role="tablist" aria-label="Настройки">${[
          ['banner', 'Баннер', 'image'],
          ['filters', 'Фильтры', 'filter'],
          ['publish', 'Выпуск', 'upload'],
        ]
          .map(
            ([id, name, glyph]) =>
              `<button class="tab" id="tab-${id}" role="tab" data-tab="${id}" aria-controls="panel-${id}" aria-selected="${id === 'banner'}" tabindex="${id === 'banner' ? 0 : -1}">${icon(glyph)}${name}</button>`,
          )
          .join(
            '',
          )}</div><div id="inspector-content"></div><div class="inspector-footer"><div class="channel-heading">Куда публикуем <span>Демо-каналы</span></div><div class="channel-grid">${channels.map((name, i) => `<label class="channel"><input type="checkbox" data-channel="${i}" checked><span class="platform ${i % 2 ? 'yt' : ''}" aria-hidden="true">${i % 2 ? 'Y' : 'T'}</span><span>${name}</span></label>`).join('')}</div><button class="btn primary launch" data-action="launch"><span class="row">${icon('upload')}Подготовить пакет</span><small id="batch-count">18 вариантов</small></button><div class="batch-note"><span>3 исходника × <span id="channel-count">6</span> каналов</span><span>Только макет</span></div></div></aside>
        <section class="section-view" id="section-view" aria-label="Раздел приложения" hidden></section>
      </main><footer class="statusbar"><div class="row"><i class="dot"></i><span>Локальный прототип</span><span>·</span><span id="status-message">Настройки применяются к выбранному ролику</span></div><span>${titles[theme]} / Концепция от 06.11.2026</span></footer>
    </div>
    <dialog id="dialog" aria-labelledby="dialog-title"></dialog><div class="toast" id="toast" role="status" aria-live="polite"></div><input type="file" id="video-input" accept="video/mp4,video/quicktime,video/webm" hidden><input type="file" id="banner-input" accept="image/png,image/jpeg,image/webp" hidden>`;
  const byId = (id) => document.getElementById(id);
  const video = byId('video');
  function notify(message) {
    const node = byId('toast');
    node.textContent = message;
    node.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => node.classList.remove('show'), 3200);
  }
  function changed() {
    byId('saved-label').textContent = 'Есть изменения';
  }
  function numberField(label, key, value, extra = '') {
    return `<label class="field"><span class="field-label">${label}</span><input type="number" data-setting="${key}" value="${value}" ${extra}></label>`;
  }
  function rangeField(label, key, value, min, max, unit = '%') {
    return `<label class="field range-row"><span class="field-label">${label}<output id="value-${key}">${value}${unit}</output></span><input type="range" data-setting="${key}" min="${min}" max="${max}" value="${value}" aria-label="${label}"></label>`;
  }
  function renderTab() {
    const s = current();
    document.querySelectorAll('[data-tab]').forEach((n) => {
      const active = n.dataset.tab === tab;
      n.setAttribute('aria-selected', String(active));
      n.tabIndex = active ? 0 : -1;
    });
    let html = '';
    if (tab === 'banner')
      html = `<div class="field"><span class="field-label">Материал баннера</span><div class="file-box"><span class="banner-icon">${icon('image')}</span><span><strong id="banner-file-name">Плашка FrameFlow</strong><small>Текст · прозрачность · позиция</small></span><button class="icon-btn" data-action="banner-import" aria-label="Загрузить изображение баннера">${icon('upload')}</button></div></div><div class="field"><span class="field-label">Когда показывать</span><div class="segmented" role="group" aria-label="Время баннера"><button data-timing="middle" aria-pressed="${s.timing === 'middle'}">В середине</button><button data-timing="custom" aria-pressed="${s.timing === 'custom'}">Свой таймкод</button></div></div><div class="two-col">${numberField('Начало, сек', 'start', s.start, `min="0" max="${sources[selected].duration}" step=".1" ${s.timing === 'middle' ? 'disabled' : ''}`)}${numberField('Показ, сек', 'duration', s.duration, 'min=".5" max="12" step=".5"')}</div><label class="field"><span class="field-label">Положение в кадре</span><select data-setting="position"><option value="22" ${s.position === 22 ? 'selected' : ''}>Сверху</option><option value="50" ${s.position === 50 ? 'selected' : ''}>По центру</option><option value="74" ${s.position === 74 ? 'selected' : ''}>Внизу</option></select></label>${rangeField('Ширина баннера', 'width', s.width, 25, 95)}<div class="panel-actions"><button class="text-button" data-action="appearance">Оформление баннера</button><button class="text-button" data-action="reset">Сбросить</button></div>`;
    if (tab === 'filters')
      html = `<div class="field"><span class="field-label">Цветовой пресет</span><div class="preset-list">${[
        ['original', 'Исходный', 'none'],
        ['warm', 'Тёплый', 'sepia(.3) saturate(.8)'],
        ['contrast', 'Контраст', 'contrast(1.2)'],
      ]
        .map(
          ([id, name, filter]) =>
            `<button class="preset" data-preset="${id}" aria-pressed="${s.preset === id}"><img src="${esc(sources[selected].poster)}" alt="" style="filter:${filter}"><span>${name}</span></button>`,
        )
        .join(
          '',
        )}</div></div><div class="filters-grid">${rangeField('Яркость', 'brightness', s.brightness, 85, 115)}${rangeField('Контраст', 'contrast', s.contrast, 85, 125)}${rangeField('Насыщенность', 'saturation', s.saturation, 70, 130)}${rangeField('Теплота', 'warmth', s.warmth, 0, 40)}</div><label class="check-row"><input type="checkbox" data-setting="mirror" ${s.mirror ? 'checked' : ''}>Отразить по горизонтали</label><div class="publish-hint">${icon('info')} Фильтры видны в предпросмотре. Экспорт видео в прототипе не выполняется.</div><div class="panel-actions"><button class="text-button" data-action="reset-filters">Сбросить фильтры</button></div>`;
    if (tab === 'publish')
      html = `<label class="field"><span class="field-label">Название выпуска</span><input type="text" data-setting="title" value="${esc(s.title)}" maxlength="100"></label><label class="field"><span class="field-label">Описание</span><textarea data-setting="caption" maxlength="300">${esc(s.caption)}</textarea></label><label class="field"><span class="field-label">Видимость</span><select data-setting="visibility"><option value="private" ${s.visibility === 'private' ? 'selected' : ''}>Только я</option><option value="unlisted" ${s.visibility === 'unlisted' ? 'selected' : ''}>По ссылке · YouTube</option><option value="public" ${s.visibility === 'public' ? 'selected' : ''}>Открытый доступ</option></select></label><div class="publish-hint"><div class="row">${icon('clock')}<strong>Расписание на следующем шаге</strong></div><p>Перед запуском — материалы, каналы и время в одном окне.</p></div>`;
    byId('inspector-content').innerHTML = ['banner', 'filters', 'publish']
      .map(
        (id) =>
          `<div class="tab-panel" role="tabpanel" id="panel-${id}" aria-labelledby="tab-${id}" ${id === tab ? '' : 'hidden'}>${id === tab ? html : ''}</div>`,
      )
      .join('');
    updatePreview();
  }
  function updatePreview() {
    const s = current(),
      src = sources[selected],
      overlay = byId('banner-overlay');
    overlay.style.width = `${s.width}%`;
    overlay.style.top = `${s.position}%`;
    overlay.style.opacity = String(s.opacity / 100);
    if (s.bannerUrl) {
      overlay.innerHTML = `<img src="${esc(s.bannerUrl)}" alt="Загруженный баннер" style="width:100%;display:block">`;
      overlay.style.background = 'transparent';
      overlay.style.border = '0';
      overlay.style.padding = '0';
    } else {
      overlay.textContent = s.text;
      overlay.style.background = '';
      overlay.style.border = '';
      overlay.style.padding = '';
    }
    overlay.hidden = before || playTime < s.start || playTime > s.start + s.duration;
    const preset =
      s.preset === 'warm' ? 'sepia(.25) ' : s.preset === 'contrast' ? 'contrast(1.15) ' : '';
    const filter = before
      ? 'none'
      : `${preset}brightness(${s.brightness / 100}) contrast(${s.contrast / 100}) saturate(${s.saturation / 100}) sepia(${s.warmth / 100})`;
    [byId('poster'), video].forEach((n) => {
      n.style.filter = filter;
      n.style.transform = !before && s.mirror ? 'scaleX(-1)' : '';
    });
    byId('track-region').style.left = `${(s.start / src.duration) * 100}%`;
    byId('track-region').style.width =
      `${Math.max(1, (Math.min(s.duration, src.duration - s.start) / src.duration) * 100)}%`;
    byId('track-region').textContent = `${s.duration.toLocaleString('ru-RU')} с`;
    byId('timecode').textContent = clock(playTime);
    byId('playhead').style.left = `${(playTime / src.duration) * 100}%`;
    byId('scrub').value = playTime;
    byId('batch-count').textContent = `${count()} вариантов`;
    byId('channel-count').textContent = s.channels.filter(Boolean).length;
    document
      .querySelectorAll('[data-channel]')
      .forEach((n) => (n.checked = !!s.channels[Number(n.dataset.channel)]));
    byId('status-message').textContent =
      s.start + s.duration > src.duration
        ? 'Баннер выходит за конец ролика — уменьшите время показа'
        : 'Настройки применяются к выбранному ролику';
  }
  function selectSource(index) {
    video.pause();
    selected = index;
    const src = sources[index];
    playTime = current().start;
    before = false;
    document.querySelector('[data-action="before"]').setAttribute('aria-pressed', 'false');
    byId('poster').src = src.poster;
    byId('poster').hidden = false;
    video.hidden = true;
    video.src = src.video;
    video.poster = src.poster;
    byId('viewer-file').textContent = src.file;
    byId('source-index').textContent = `${index + 1} из 3`;
    byId('duration-label').textContent = clock(src.duration);
    byId('ruler-end').textContent = clock(src.duration);
    byId('scrub').max = src.duration;
    document
      .querySelectorAll('[data-source]')
      .forEach((n) => n.setAttribute('aria-pressed', String(Number(n.dataset.source) === index)));
    byId('filmstrip').innerHTML = Array.from(
      { length: 9 },
      () => `<img src="${esc(src.poster)}" alt="">`,
    ).join('');
    renderTab();
  }
  function openDialog(content, trigger) {
    lastTrigger = trigger || document.activeElement;
    byId('dialog').innerHTML = content;
    byId('dialog').showModal();
  }
  const dialogHeader = (title) =>
    `<div class="dialog-header"><h2 id="dialog-title">${title}</h2><button class="icon-btn" data-action="close" aria-label="Закрыть окно">${icon('close')}</button></div>`;
  function closeDialog() {
    byId('dialog').close();
  }
  function renderSection() {
    document
      .querySelectorAll('.sources,.editor,.inspector')
      .forEach((n) => (n.hidden = section !== 'editor'));
    const panel = byId('section-view');
    panel.hidden = section === 'editor';
    document.querySelectorAll('[data-nav]').forEach((n) => {
      if (n.dataset.nav === section) n.setAttribute('aria-current', 'page');
      else n.removeAttribute('aria-current');
    });
    if (section === 'editor') return;
    if (section === 'queue')
      panel.innerHTML = `<div class="section-toolbar"><div><h1>Очередь</h1><p class="section-description">Демонстрационные задания хранятся только до перезагрузки страницы.</p></div><button class="btn" data-action="clear-queue" ${queue.length ? '' : 'disabled'}>Очистить демо</button></div>${
        queue.length
          ? `<div class="row"><span class="tag">${queue.length} вариантов</span><span class="muted small">Рендер и публикация не запускаются</span></div><div class="table-wrap"><table><thead><tr><th>Материал</th><th>Канал</th><th>Время</th><th>Статус</th></tr></thead><tbody>${queue
              .slice(0, 6)
              .map(
                (j) =>
                  `<tr><td>${esc(j.title)}</td><td>${esc(j.channel)}</td><td class="numbers">${esc(j.time)}</td><td><span class="tag">Демо · запланировано</span></td></tr>`,
              )
              .join(
                '',
              )}</tbody></table></div><p class="muted small">Показаны первые ${Math.min(6, queue.length)} из ${queue.length}. Это образец списка для сравнения дизайна.</p>`
          : `<div class="empty-state">${icon('queue')}<h2>Пакет ещё не подготовлен</h2><p>Выберите каналы в монтажной и подготовьте демопакет.</p><button class="btn primary" data-action="to-editor">В монтажную</button></div>`
      }`;
    if (section === 'channels')
      panel.innerHTML = `<div><h1>Каналы</h1><p class="section-description">Образцы состояний подключения. Настоящие аккаунты не подключены.</p></div><div class="channels-list">${channels.map((name, i) => `<article class="connection"><div class="row"><span class="platform ${i % 2 ? 'yt' : ''}">${i % 2 ? 'Y' : 'T'}</span><h3>${name}</h3></div><p>${i === 2 ? 'Сессия сохранена, автопубликация ещё не проверена.' : 'Демонстрационная карточка подключения.'}</p><span class="tag">${i === 2 ? 'Нужна проверка отправки' : 'Демо-канал'}</span><button class="btn" data-action="connection">Проверить подключение</button></article>`).join('')}</div>`;
    if (section === 'stats')
      panel.innerHTML = `<div><h1>Статистика</h1><p class="section-description">Иллюстративные показатели для оценки интерфейса, не данные аккаунтов.</p></div><div class="stat-summary"><div><strong class="numbers">1 280</strong><span>Просмотры · демо</span></div><div><strong class="numbers">18</strong><span>Варианты · демо</span></div><div><strong class="numbers">6</strong><span>Каналы · демо</span></div></div><div class="chart" role="img" aria-label="Демопросмотры по шести каналам: 110, 340, 180, 290, 120, 240">${[110, 340, 180, 290, 120, 240].map((v) => `<div class="chart-bar" style="height:${v / 3.4}%"><span>${v}</span></div>`).join('')}</div><div class="chart-labels">${channels.map((n) => `<span>${n}</span>`).join('')}</div>`;
    if (section === 'settings')
      panel.innerHTML = `<div><h1>Настройки</h1><p class="section-description">Настройки этой демонстрации.</p></div><div class="field" style="max-width:420px"><span class="field-label">Дизайн монтажной</span><div class="segmented">${Object.entries(
        titles,
      )
        .map(
          ([id, name]) =>
            `<button data-theme-link="${id}" aria-pressed="${id === theme}">${name}</button>`,
        )
        .join(
          '',
        )}</div></div><div class="publish-hint" style="max-width:520px">Все материалы остаются на компьютере. «Сохранить» запоминает настройки макета в этом браузере. Прототип не обращается к FrameFlow API, TikTok или YouTube.</div><div><button class="btn" data-action="clear-storage">Сбросить сохранённый макет</button></div>`;
  }
  function launchDialog(trigger) {
    const s = current(),
      n = count();
    openDialog(
      `<div class="dialog-body">${dialogHeader('Подготовить пакет')}<p>Проверьте материалы и назначения. Это демонстрация — реальные видео отправлены не будут.</p><div class="dialog-summary"><div><strong>${n}</strong><br><span>вариантов</span></div><span>3 исходника × ${s.channels.filter(Boolean).length} каналов</span></div><label class="field"><span class="field-label">Дата и время</span><input type="datetime-local" id="schedule-date" value="${esc(s.date)}" required></label><label class="field"><span class="field-label">Видимость</span><select id="schedule-visibility"><option value="private" ${s.visibility === 'private' ? 'selected' : ''}>Только я</option><option value="unlisted" ${s.visibility === 'unlisted' ? 'selected' : ''}>По ссылке · YouTube</option><option value="public" ${s.visibility === 'public' ? 'selected' : ''}>Открытый доступ</option></select></label><label class="check-row"><input type="checkbox" id="schedule-auto" ${s.auto ? 'checked' : ''}>Автозалив после рендера · демонстрация</label><p class="dialog-error" id="dialog-error" role="alert" hidden></p><p class="small muted">В рабочей программе запуск также проверяет вход в выбранные каналы и готовность отправки.</p></div><div class="dialog-footer"><button class="btn" data-action="close">Назад</button><button class="btn primary" data-action="demo-launch">Добавить в демоочередь</button></div>`,
      trigger,
    );
  }
  app.addEventListener('click', async (event) => {
    const source = event.target.closest('[data-source]');
    if (source) {
      selectSource(Number(source.dataset.source));
      return;
    }
    const nav = event.target.closest('[data-nav]');
    if (nav) {
      video.pause();
      section = nav.dataset.nav;
      renderSection();
      return;
    }
    const themeLink = event.target.closest('[data-theme-link]');
    if (themeLink) {
      location.href = `${themeLink.dataset.themeLink}.html`;
      return;
    }
    const tabButton = event.target.closest('[data-tab]');
    if (tabButton) {
      tab = tabButton.dataset.tab;
      renderTab();
      return;
    }
    const timing = event.target.closest('[data-timing]');
    if (timing) {
      current().timing = timing.dataset.timing;
      if (current().timing === 'middle') current().start = sources[selected].duration / 2;
      renderTab();
      changed();
      return;
    }
    const preset = event.target.closest('[data-preset]');
    if (preset) {
      current().preset = preset.dataset.preset;
      renderTab();
      changed();
      return;
    }
    const button = event.target.closest('[data-action]');
    if (!button) return;
    switch (button.dataset.action) {
      case 'play':
        if (!video.paused) {
          video.pause();
        } else {
          video.hidden = false;
          byId('poster').hidden = true;
          video.currentTime = playTime >= sources[selected].duration ? 0 : playTime;
          try {
            await video.play();
          } catch {
            notify('Не удалось открыть видео. Выберите другой исходник.');
          }
        }
        break;
      case 'rewind':
        setTime(playTime - 1);
        break;
      case 'forward':
        setTime(playTime + 1);
        break;
      case 'safe':
        safe = !safe;
        byId('safe-guide').hidden = !safe;
        button.setAttribute('aria-pressed', String(safe));
        break;
      case 'before':
        before = !before;
        button.setAttribute('aria-pressed', String(before));
        updatePreview();
        break;
      case 'fullscreen':
        try {
          if (document.fullscreenElement) await document.exitFullscreen();
          else await byId('video-frame').requestFullscreen();
        } catch {
          notify('Полный экран недоступен в этой области просмотра.');
        }
        break;
      case 'save':
        try {
          const clean = { ...current() };
          delete clean.bannerUrl;
          localStorage.setItem('frameflow-prototype-settings', JSON.stringify(clean));
          byId('saved-label').textContent = 'Изменения сохранены';
          notify('Настройки макета сохранены в браузере');
        } catch {
          notify('Браузер запретил локальное сохранение');
        }
        break;
      case 'reset': {
        const s = current();
        ['start', 'duration', 'width', 'position', 'opacity', 'text', 'timing'].forEach(
          (k) => (s[k] = defaults()[k]),
        );
        if (s.bannerUrl) URL.revokeObjectURL(s.bannerUrl);
        delete s.bannerUrl;
        renderTab();
        changed();
        break;
      }
      case 'reset-filters':
        ['preset', 'brightness', 'contrast', 'saturation', 'warmth', 'mirror'].forEach(
          (k) => (current()[k] = defaults()[k]),
        );
        renderTab();
        changed();
        break;
      case 'appearance':
        openDialog(
          `<div class="dialog-body">${dialogHeader('Оформление баннера')}<label class="field"><span class="field-label">Текст плашки</span><input type="text" data-setting="text" maxlength="80" value="${esc(current().text)}"></label>${rangeField('Непрозрачность', 'opacity', current().opacity, 10, 100)}<p>Изменения сразу видны на кадре. Текст внутри загруженного изображения не меняется.</p></div><div class="dialog-footer"><button class="btn primary" data-action="close">Готово</button></div>`,
          button,
        );
        break;
      case 'about':
        openDialog(
          `<div class="dialog-body">${dialogHeader(`FrameFlow ${titles[theme]}`)}<p>Интерактивная концепция монтажной от 6 ноября 2026 года. Все каналы и показатели демонстрационные.</p><p>Работают исходники, воспроизведение коротких фрагментов без звука, баннер, фильтры и сценарий демоочереди. Настоящий рендер и публикация не выполняются.</p><p>Локальные файлы не загружаются в интернет. Шрифты и материалы находятся рядом с макетом.</p></div><div class="dialog-footer"><button class="btn primary" data-action="close">Понятно</button></div>`,
          button,
        );
        break;
      case 'launch':
        launchDialog(button);
        break;
      case 'close':
        closeDialog();
        break;
      case 'demo-launch': {
        const s = current(),
          date = byId('schedule-date');
        let error = '';
        if (!s.channels.some(Boolean)) error = 'Выберите хотя бы один канал в монтажной.';
        else if (!date.value || !date.validity.valid) error = 'Укажите дату и время запуска.';
        else if (edits.some((e, i) => e.start + e.duration > sources[i].duration))
          error = 'Баннер выходит за конец ролика. Уменьшите время показа в настройках.';
        else if (
          byId('schedule-visibility').value === 'unlisted' &&
          s.channels.some((v, i) => v && i % 2 === 0)
        )
          error =
            'Видимость «По ссылке» доступна только для YouTube. Выберите «Только я» или отключите TikTok.';
        if (error) {
          byId('dialog-error').textContent = error;
          byId('dialog-error').hidden = false;
          return;
        }
        s.date = date.value;
        s.visibility = byId('schedule-visibility').value;
        s.auto = byId('schedule-auto').checked;
        if (s.visibility === 'unlisted' && s.channels.some((v, i) => v && i % 2 === 0)) {
          byId('dialog-error').textContent =
            'Видимость «По ссылке» доступна только для YouTube. Выберите «Только я» или отключите TikTok.';
          byId('dialog-error').hidden = false;
          return;
        }
        queue = sources.flatMap((src) =>
          channels.flatMap((channel, i) =>
            s.channels[i] ? [{ title: src.title, channel, time: s.date.replace('T', ' ') }] : [],
          ),
        );
        closeDialog();
        section = 'queue';
        renderSection();
        notify(`${queue.length} вариантов добавлены в демоочередь`);
        break;
      }
      case 'import':
        byId('video-input').click();
        break;
      case 'banner-import':
        byId('banner-input').click();
        break;
      case 'to-editor':
        section = 'editor';
        renderSection();
        break;
      case 'clear-queue':
        queue = [];
        renderSection();
        notify('Демоочередь очищена');
        break;
      case 'connection':
        notify('Это образец канала. Проверка настоящего подключения доступна в рабочем FrameFlow.');
        break;
      case 'clear-storage':
        try {
          localStorage.removeItem('frameflow-prototype-settings');
        } catch {}
        edits = [defaults(), defaults(), defaults()];
        renderTab();
        notify('Сохранённый макет сброшен');
        break;
    }
  });
  app.addEventListener('input', (event) => {
    const input = event.target,
      key = input.dataset.setting;
    if (key) {
      current()[key] =
        input.type === 'checkbox'
          ? input.checked
          : input.type === 'range' || input.type === 'number' || key === 'position'
            ? Number(input.value)
            : input.value;
      const output = byId(`value-${key}`);
      if (output) output.textContent = `${input.value}%`;
      updatePreview();
      changed();
    }
    if (input.dataset.channel !== undefined) {
      current().channels[Number(input.dataset.channel)] = input.checked;
      updatePreview();
      changed();
    }
    if (input.id === 'scrub') setTime(Number(input.value));
  });
  document.querySelector('.tabs').addEventListener('keydown', (event) => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const tabs = ['banner', 'filters', 'publish'];
    let index = tabs.indexOf(tab);
    index =
      event.key === 'Home'
        ? 0
        : event.key === 'End'
          ? 2
          : (index + (event.key === 'ArrowRight' ? 1 : -1) + 3) % 3;
    tab = tabs[index];
    renderTab();
    byId(`tab-${tab}`).focus();
  });
  function setTime(time) {
    playTime = Math.max(0, Math.min(sources[selected].duration, time));
    if (video.readyState > 0) {
      video.currentTime = playTime;
      video.hidden = false;
      byId('poster').hidden = true;
    }
    updatePreview();
  }
  video.addEventListener('timeupdate', () => {
    playTime = video.currentTime;
    updatePreview();
  });
  video.addEventListener('play', () => {
    const b = document.querySelector('[data-action="play"]');
    b.innerHTML = icon('pause');
    b.setAttribute('aria-label', 'Приостановить');
  });
  video.addEventListener('pause', () => {
    const b = document.querySelector('[data-action="play"]');
    b.innerHTML = icon('play');
    b.setAttribute('aria-label', 'Воспроизвести');
  });
  video.addEventListener('ended', () => {
    playTime = 0;
    updatePreview();
  });
  byId('dialog').addEventListener('close', () => lastTrigger?.isConnected && lastTrigger.focus());
  byId('video-input').addEventListener('change', (event) => {
    const file = event.target.files[0];
    if (!file) return;
    if (!file.type.startsWith('video/')) {
      notify('Выберите MP4, MOV или WebM');
      return;
    }
    const src = sources[selected],
      index = selected;
    if (src.video.startsWith('blob:')) URL.revokeObjectURL(src.video);
    src.video = URL.createObjectURL(file);
    src.file = file.name;
    src.title = file.name.replace(/\.[^.]+$/, '');
    selectSource(selected);
    video.addEventListener(
      'loadedmetadata',
      () => {
        if (index !== selected) return;
        if (!Number.isFinite(video.duration) || video.duration <= 0) {
          notify('Файл не удалось прочитать. Выберите другое видео.');
          return;
        }
        src.duration = video.duration;
        current().start = video.duration / 2;
        selectSource(index);
        setTime(current().start);
        notify('Выбранный исходник заменён локальным видео');
      },
      { once: true },
    );
    document.querySelector(`[data-source="${index}"] .source-title`).textContent = src.title;
    event.target.value = '';
  });
  byId('banner-input').addEventListener('change', (event) => {
    const file = event.target.files[0];
    if (!file) return;
    if (!file.type.startsWith('image/')) {
      notify('Выберите PNG, JPG или WebP');
      return;
    }
    if (current().bannerUrl) URL.revokeObjectURL(current().bannerUrl);
    current().bannerUrl = URL.createObjectURL(file);
    updatePreview();
    byId('banner-file-name').textContent = file.name;
    changed();
    event.target.value = '';
    notify('Баннер открыт локально');
  });
  renderTab();
})();
