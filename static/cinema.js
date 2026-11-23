(() => {
  'use strict';
  const videos = [...document.querySelectorAll('[data-cinema]')];
  if (!videos.length) return;
  const feed = document.querySelector('.trailer-feed');
  let active = null, player = null, generation = 0, ready = false, paused = false, muted = true, failed = false, apiPromise;
  let timer, watchdog;
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const visible = new Map();
  function api() {
    if (window.YT?.Player) return Promise.resolve();
    if (!apiPromise) apiPromise = new Promise((resolve, reject) => {
      window.onYouTubeIframeAPIReady = resolve;
      const script = document.createElement('script');
      script.src = 'https://www.youtube.com/iframe_api';
      script.onerror = error => { apiPromise = undefined; script.remove(); reject(error); };
      document.head.append(script);
    });
    return apiPromise;
  }
  function status(text) { if (active) active.querySelector('.cinema-status').textContent = text; }
  function soundLabel() {
    if (!active) return;
    const button = active.querySelector('[data-sound]');
    button.textContent = muted ? 'Включить звук' : 'Выключить звук';
    button.setAttribute('aria-pressed', String(!muted));
  }
  function stop() {
    generation++; clearTimeout(watchdog); ready = false;
    if (player) { try { player.destroy(); } catch {} player = null; }
    if (active) {
      active.classList.remove('is-playing');
      active.querySelector('.cinema-mount').replaceChildren();
      active.querySelector('[data-play]').textContent = '▶';
      active.querySelector('[data-play]').setAttribute('aria-label', 'Воспроизвести');
      active.querySelector('[data-sound]').textContent = 'Включить звук';
      active.querySelector('[data-sound]').setAttribute('aria-pressed', 'false');
    }
    active = null;
  }
  function playbackError(node, message) {
    failed = true;
    ready = false;
    node.classList.remove('is-playing');
    const play = node.querySelector('[data-play]');
    play.textContent = '▶';
    play.setAttribute('aria-label', 'Повторить воспроизведение');
    status(message);
  }
  async function activate(node, sound = false) {
    if (node === active) return;
    stop(); if (!node) return;
    active = node; paused = false; muted = !sound; failed = false;
    const version = generation;
    const key = node.dataset.cinema;
    if (!/^[\w-]{11}$/.test(key)) return;
    node.querySelector('[data-play]').textContent = 'Ⅱ';
    node.querySelector('[data-play]').setAttribute('aria-label', 'Пауза');
    soundLabel();
    status('Загружаем трейлер…');
    watchdog = setTimeout(() => {
      if (active === node) playbackError(node, 'Трейлер не загрузился. Повторите или откройте на YouTube.');
    }, 12000);
    try {
      await api();
      if (version !== generation || document.hidden) return;
      const frame = document.createElement('iframe');
      frame.title = `Трейлер: ${node.dataset.title}`;
      frame.src = `https://www.youtube-nocookie.com/embed/${key}?autoplay=1&mute=1&playsinline=1&rel=0&enablejsapi=1&origin=${encodeURIComponent(location.origin)}`;
      frame.allow = 'autoplay; encrypted-media; picture-in-picture; fullscreen';
      frame.allowFullscreen = true;
      frame.referrerPolicy = 'strict-origin-when-cross-origin';
      node.querySelector('.cinema-mount').append(frame);
      player = new YT.Player(frame, {events: {
        onReady: event => {
          if (version !== generation) return;
          ready = true; failed = false;
          if (muted) event.target.mute(); else { event.target.unMute(); event.target.setVolume(70); }
          event.target.playVideo(); soundLabel();
        },
        onStateChange: event => {
          if (version !== generation) return;
          if (event.data === 1) {
            clearTimeout(watchdog); node.classList.add('is-playing'); paused = false; ready = true; failed = false;
            status(''); soundLabel(); node.querySelector('[data-play]').textContent = 'Ⅱ';
            node.querySelector('[data-play]').setAttribute('aria-label', 'Пауза');
          } else if (event.data === 2 || event.data === 0) {
            paused = true; node.querySelector('[data-play]').textContent = '▶';
            node.querySelector('[data-play]').setAttribute('aria-label', 'Воспроизвести');
            if (event.data === 0) status(feed ? 'Листайте дальше — новая история уже ждёт' : 'Посмотреть ещё раз — нажмите ▶');
          }
        },
        onAutoplayBlocked: () => {
          if (version !== generation) return;
          clearTimeout(watchdog); paused = true;
          node.querySelector('[data-play]').textContent = '▶';
          node.querySelector('[data-play]').setAttribute('aria-label', 'Воспроизвести');
          status('Нажмите ▶ — браузер приостановил автозапуск');
        },
        onError: () => { if (version === generation) {clearTimeout(watchdog); playbackError(node, 'Видео недоступно здесь. Откройте на YouTube.');} }
      }});
    } catch {
      if (version === generation) { clearTimeout(watchdog); playbackError(node, 'Не удалось загрузить трейлер. Повторите или откройте на YouTube.'); }
    }
  }
  function choose() {
    if (document.hidden || document.querySelector('#trailer-dialog[open], #cs-drawer[open]')) return activate(null);
    const best = [...visible].filter(([,ratio]) => ratio >= .35).sort((a,b) => b[1]-a[1])[0]?.[0];
    activate(best || null);
  }
  const observer = new IntersectionObserver(entries => {
    for (const entry of entries) visible.set(entry.target, entry.intersectionRatio);
    clearTimeout(timer); timer = setTimeout(choose, 120);
  }, {root: feed || null, threshold: [0,.1,.35,.5,.75,1]});
  videos.forEach(node => observer.observe(node));
  document.addEventListener('visibilitychange', choose);
  window.addEventListener('pagehide', stop);
  window.addEventListener('pageshow', choose);
  document.addEventListener('cinema:modal', choose);
  document.addEventListener('click', event => {
    const node = event.target.closest('[data-cinema]');
    if (!node) return;
    if (event.target.closest('[data-sound]')) {
      if (node !== active || failed) { if (node === active) stop(); activate(node, true); return; }
      muted = !muted;
      soundLabel();
      if (ready) {
        if (!muted) { player.unMute(); player.setVolume(70); } else player.mute();
        if (paused) player.playVideo();
      }
    }
    if (event.target.closest('[data-play]')) {
      if (node !== active || !ready) { if (node === active) stop(); activate(node); return; }
      if (paused) player.playVideo(); else { player.pauseVideo(); paused = true; }
    }
  });
  if (feed) {
    function next(card, direction = 1) {
      const target = direction > 0 ? card.nextElementSibling : card.previousElementSibling;
      target?.scrollIntoView({behavior: reduced.matches ? 'instant' : 'smooth', block:'start'});
    }
    feed.addEventListener('click', event => { const button = event.target.closest('[data-next]'); if (button) next(button.closest('.feed-card')); });
    feed.addEventListener('keydown', event => {
      if (event.target.closest('button,a,input,select,textarea')) return;
      if (['ArrowDown','ArrowUp','PageDown','PageUp'].includes(event.key)) {
        event.preventDefault(); const card = active?.closest('.feed-card');
        if (card) next(card, ['ArrowUp','PageUp'].includes(event.key) ? -1 : 1);
      }
    });
    // Recycle previously viewed cards: an endless feed with a bounded DOM and one player.
    let recycleTimer;
    feed.addEventListener('scroll', () => {
      clearTimeout(recycleTimer);
      recycleTimer = setTimeout(() => {
        const cards = [...feed.children], index = cards.indexOf(active?.closest('.feed-card'));
        if (index < 8 || cards.length < 12) return;
        const first = cards[0];
        if (first.contains(document.activeElement)) return;
        const height = first.getBoundingClientRect().height, scroll = feed.scrollTop;
        feed.classList.add('is-recycling'); feed.append(first); feed.scrollTop = scroll - height;
        requestAnimationFrame(() => feed.classList.remove('is-recycling'));
      }, 180);
    }, {passive:true});
  }
})();
