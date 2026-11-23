(() => {
  const form = document.querySelector('.checkout-form');
  if (!form) return;
  const button = form.querySelector('button[type="submit"]');
  const text = button.textContent;
  form.addEventListener('submit', () => {
    button.disabled = true;
    button.textContent = 'Создаём счёт…';
    button.setAttribute('aria-busy', 'true');
  });
  window.addEventListener('pageshow', () => {
    button.disabled = false;
    button.textContent = text;
    button.removeAttribute('aria-busy');
  });
})();

(() => {
  const panel = document.querySelector('[data-order-state]');
  if (!panel || panel.hasAttribute('data-stale-order')) return;

  const statusUrl = new URL(window.location.href);
  statusUrl.searchParams.set('status', '1');
  const deadline = Date.now() + 180_000;

  async function checkStatus() {
    if (Date.now() >= deadline) return;
    if (!document.hidden) {
      try {
        const response = await fetch(statusUrl, { cache: 'no-store' });
        if (response.status === 401 || response.status === 404) return;
        if (response.ok) {
          const { state } = await response.json();
          if (state && state !== panel.dataset.orderState) {
            window.location.reload();
            return;
          }
        }
      } catch {
        // The manual status check remains available on intermittent connections.
      }
    }
    window.setTimeout(checkStatus, 15_000);
  }

  window.setTimeout(checkStatus, 5_000);
})();
