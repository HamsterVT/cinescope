(() => {
  'use strict';
  const KEY = 'cinescope.watchlist.v1';
  const toast = document.querySelector('.toast');
  let toastTimer;
  function notify(message) { clearTimeout(toastTimer); toast.textContent = message; toast.hidden = false; toastTimer = setTimeout(() => { toast.hidden = true; }, 3500); }
  const valid = m => m && typeof m.id === 'string' && /^[a-z0-9-]{1,100}$/.test(m.id) && typeof m.title === 'string' && m.title.length < 250;
  function readSaved() { try { const data = JSON.parse(localStorage.getItem(KEY) || '[]'); return Array.isArray(data) ? data.filter(valid).slice(0,500) : []; } catch { return []; } }
  const authenticated = document.body.dataset.authenticated === 'true';
  let saved = authenticated ? [] : readSaved();
  let cloudItems = [], cloudReady = !authenticated;
  const csrf = () => document.querySelector('#csrf-form input')?.value || '';
  async function saveCloud(movieId, changes) {
    const r = await fetch('/api/v1/library/', {method:'POST', headers:{'Content-Type':'application/json','X-CSRFToken':csrf()},body:JSON.stringify({movie_id:movieId,...changes})});
    const data = await r.json(); if (!r.ok) throw new Error(data.error || 'Не удалось сохранить.');
    const index = cloudItems.findIndex(m => m.movie_id === movieId);
    if (index < 0) cloudItems.push(data.item); else cloudItems[index] = data.item;
    saved = cloudItems.filter(m => m.saved).map(m => m.movie);
    updateButtons(); updatePersonal(); return data.item;
  }
  function updatePersonal() {
    document.querySelectorAll('[data-watched]').forEach(b => { const row = cloudItems.find(m => m.movie_id === b.dataset.watched); b.setAttribute('aria-pressed',String(!!row?.watched)); b.textContent = row?.watched ? '✓ Просмотрено' : '✓ Уже посмотрел'; });
    document.querySelectorAll('[data-rating]').forEach(s => { const row = cloudItems.find(m => m.movie_id === s.dataset.rating); s.value = row?.rating ?? ''; });
  }
  const libraryControls = document.querySelectorAll('[data-save],[data-watched],[data-rating]');
  if (authenticated && (libraryControls.length || document.querySelector('[data-import],[data-saved-count]'))) {
    libraryControls.forEach(control => control.disabled = true);
    fetch('/api/v1/library/',{cache:'no-store'}).then(async r => { if (!r.ok) throw new Error(); return r.json(); }).then(data => {
      cloudItems = data.items; saved = cloudItems.filter(m => m.saved).map(m => m.movie); cloudReady = true;
      updateButtons(); updatePersonal(); libraryControls.forEach(control => control.disabled = false);
    }).catch(() => notify('Не удалось загрузить вашу библиотеку. Обновите страницу.'));
  }
  document.querySelectorAll('[data-watched]').forEach(b => b.addEventListener('click', async () => { if (!cloudReady) return; b.disabled = true; try { await saveCloud(b.dataset.watched,{watched:b.getAttribute('aria-pressed') !== 'true'}); notify('Статус сохранён в профиле'); } catch(e) {notify(e.message);} finally {b.disabled=false;} }));
  document.querySelectorAll('[data-rating]').forEach(s => s.addEventListener('change', async () => { if (!cloudReady) return; s.disabled=true; try { await saveCloud(s.dataset.rating,{rating:s.value ? Number(s.value) : null}); notify('Оценка сохранена'); } catch(e) {updatePersonal();notify(e.message);} finally {s.disabled=false;} }));
  const importButton = document.querySelector('[data-import]');
  if (importButton && readSaved().length) { importButton.hidden = false; importButton.addEventListener('click',async () => {
    if (!cloudReady) return; importButton.disabled=true; let count=0;
    try { for (const m of readSaved()) { await saveCloud(m.id,{saved:true}); count++; } localStorage.removeItem(KEY); location.reload(); }
    catch(e) { notify(`Перенесено ${count}. ${e.message} Можно повторить.`); importButton.disabled=false; }
  }); }

  function writeSaved(next) { try { localStorage.setItem(KEY, JSON.stringify(next)); saved = next; return true; } catch { notify('Не удалось сохранить: проверьте доступ к памяти браузера.'); return false; } }
  function updateButtons() {
    document.querySelectorAll('[data-save]').forEach(button => {
      const exists = saved.some(m => m.id === button.dataset.save);
      button.setAttribute('aria-pressed', String(exists));
      button.setAttribute('aria-label', `${exists ? 'Убрать' : 'Добавить'} ${button.dataset.title} ${exists ? 'из списка' : 'в список'}`);
      const symbol = button.querySelector('.save-symbol'); if (symbol) symbol.textContent = exists ? '✓' : '+';
      const label = button.querySelector('.save-label'); if (label) label.textContent = exists ? 'В моём списке' : 'Буду смотреть';
    });
    document.querySelectorAll('[data-saved-count]').forEach(b => { b.textContent = saved.length; b.hidden = !saved.length; });
  }
  function el(tag, cls, text) { const n = document.createElement(tag); if (cls) n.className = cls; if (text !== undefined) n.textContent = text; return n; }
  function safePoster(url) {
    if (typeof url !== 'string') return '';
    if (/^\/static\/(?:open-films|featured)\/[a-z0-9-]+\.webp$/.test(url)) return url;
    try {
      const poster = new URL(url);
      return poster.origin === 'https://image.tmdb.org' ? poster.href : '';
    } catch {
      return '';
    }
  }
  function watchlistCard(movie) {
    const card = el('article', 'movie-card');
    const wrap = el('div', 'poster-wrap');
    const link = el('a', 'poster-link');
    link.href = `/movie/${movie.id}/`;
    link.tabIndex = -1;
    link.setAttribute('aria-hidden', 'true');
    const poster = safePoster(movie.poster);
    if (poster) {
      const img = el('img');
      img.src = poster;
      img.alt = '';
      img.loading = 'lazy';
      img.width = 500;
      img.height = 750;
      img.addEventListener('error', () => img.classList.add('broken'));
      link.append(img);
    }
    link.append(el('span', 'poster-fallback', movie.title));
    const button = el('button', 'save-button compact');
    button.type = 'button';
    button.dataset.save = movie.id;
    button.dataset.title = movie.title;
    button.append(el('span', 'save-symbol', '✓'));
    wrap.append(link);
    if (Number(movie.rating) > 0) {
      wrap.append(el('span', 'rating', `★ ${Number(movie.rating).toFixed(1).replace('.', ',')}`));
    }
    wrap.append(button);
    const title = el('a', 'movie-title', movie.title);
    title.href = link.href;
    card.append(wrap, title, el('p', 'movie-meta', [movie.year, movie.genre].filter(Boolean).join(' · ')));
    return card;
  }
  function renderWatchlist() { const grid = document.querySelector('#watchlist'); if (!grid) return; grid.replaceChildren(...saved.map(watchlistCard)); document.querySelector('#watchlist-empty').hidden = saved.length > 0; }
  document.addEventListener('click', async event => {
    const button = event.target.closest('[data-save]'); if (!button) return;
    if (button.disabled) return;
    const id = button.dataset.save; const exists = saved.some(m => m.id === id);
    if (authenticated) {
      if (!cloudReady) return;
      button.disabled = true;
      try { await saveCloud(id,{saved:!exists}); notify(exists ? 'Убрано из облачного списка' : 'Сохранено в вашем профиле'); if (location.pathname === '/profile/') location.reload(); }
      catch(e) { notify(e.message); }
      finally { button.disabled=false; }
      return;
    }
    if (!exists && saved.length >= 500) { notify('В списке уже 500 фильмов. Уберите один, чтобы добавить новый.'); return; }
    const movie = {id, title:button.dataset.title, poster:button.dataset.poster || '', year:button.dataset.year || '', rating:button.dataset.rating?.replace(',','.') || 0, genre:button.dataset.genre || ''};
    if (!valid(movie)) return;
    if (writeSaved(exists ? saved.filter(m => m.id !== id) : [...saved,movie])) { renderWatchlist(); updateButtons(); notify(exists ? 'Фильм убран из списка' : 'Добавлено в «Моё кино»'); }
  });
  window.addEventListener('storage',event => { if (!authenticated && event.key === KEY) { saved = readSaved(); renderWatchlist(); updateButtons(); } });
  renderWatchlist(); updateButtons();
  document.querySelectorAll('img').forEach(img => { img.addEventListener('error',() => img.classList.add('broken')); if (img.complete && !img.naturalWidth) img.classList.add('broken'); });
  const search = document.querySelector('#catalog-search');
  if (search) {
    function focusSearch() {
      if (location.hash === '#catalog-search') search.focus({preventScroll: true});
    }
    window.addEventListener('hashchange', focusSearch);
    focusSearch();
  }
  document.querySelector('[data-share]')?.addEventListener('click',async () => { try { if (navigator.share) await navigator.share({title:document.title,url:location.href}); else { await navigator.clipboard.writeText(location.href); notify('Ссылка скопирована'); } } catch (error) { if (error.name !== 'AbortError') notify('Скопируйте адрес страницы из строки браузера.'); } });
})();
