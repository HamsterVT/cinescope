(() => {
  const button = document.querySelector('[data-copy-site]');
  button?.addEventListener('click', async () => {
    try { await navigator.clipboard.writeText(button.dataset.copySite); button.textContent = 'Адрес скопирован ✓'; }
    catch { button.textContent = 'Скопируйте адрес под кнопкой'; }
  });
})();
