(() => {
  'use strict';
  const dialog = document.getElementById('trailer-dialog');
  if (!dialog?.showModal) return;
  const player = document.getElementById('trailer-player');
  const title = document.getElementById('trailer-dialog-title');
  const credit = document.getElementById('trailer-credit');
  const external = document.getElementById('trailer-external');
  let trigger;
  document.addEventListener('click', event => {
    const link = event.target.closest('[data-trailer]');
    if (!link || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    const key = link.dataset.trailer;
    if (!/^[A-Za-z0-9_-]{11}$/.test(key)) return;
    event.preventDefault(); trigger = link;
    title.textContent = link.dataset.trailerName || 'Трейлер';
    credit.textContent = link.dataset.trailerChannel ? `Канал: ${link.dataset.trailerChannel}` : 'Видео с YouTube';
    if (['Trailer Blend','Mathew Perry'].includes(link.dataset.trailerChannel)) credit.textContent += ' · фанатский трейлер';
    external.href = `https://www.youtube.com/watch?v=${key}`;
    const frame = document.createElement('iframe');
    frame.title = `Трейлер: ${title.textContent}`;
    frame.src = `https://www.youtube-nocookie.com/embed/${key}?autoplay=1&mute=1&rel=0&playsinline=1`;
    frame.allow = 'autoplay; encrypted-media; picture-in-picture; fullscreen';
    frame.allowFullscreen = true;
    frame.referrerPolicy = 'strict-origin-when-cross-origin';
    player.replaceChildren(frame);
    dialog.showModal(); document.dispatchEvent(new Event('cinema:modal')); document.body.classList.add('trailer-is-open');
  });
  dialog.querySelector('.trailer-close').addEventListener('click', () => dialog.close());
  dialog.addEventListener('click', event => {
    if (event.target !== dialog) return;
    const rect = dialog.getBoundingClientRect();
    if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close();
  });
  dialog.addEventListener('close', () => {
    player.replaceChildren(); document.dispatchEvent(new Event('cinema:modal')); document.body.classList.remove('trailer-is-open');
    trigger?.focus({preventScroll:true});
  });
})();
