(() => {
  document.querySelectorAll('[data-confirm]').forEach(button => button.addEventListener('click', event => {
    if (!window.confirm(button.dataset.confirm)) event.preventDefault();
  }));
  document.querySelector('[data-profile-share]')?.addEventListener('click', async event => {
    const url = new URL(event.currentTarget.dataset.profileShare, location.origin).href;
    try {
      if (navigator.share) await navigator.share({title: document.title, url});
      else { await navigator.clipboard.writeText(url); const toast=document.querySelector('.toast'); toast.textContent='Ссылка на профиль скопирована'; toast.hidden=false; setTimeout(()=>toast.hidden=true,3500); }
    } catch(error) { if(error.name !== 'AbortError') window.prompt('Скопируйте ссылку на профиль:',url); }
  });
  document.querySelector('[data-star-search]')?.addEventListener('input', event => {
    const q=event.target.value.toLocaleLowerCase('ru').replaceAll('ё','е').trim();
    document.querySelectorAll('[data-star-name]').forEach(label => label.hidden=!label.dataset.starName.toLocaleLowerCase('ru').replaceAll('ё','е').includes(q));
  });
})();
