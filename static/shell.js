(() => {
  'use strict';
  const drawer = document.querySelector('#cs-drawer');
  const toggle = document.querySelector('.cs-menu-toggle');
  toggle?.addEventListener('click', () => { drawer.showModal(); toggle.setAttribute('aria-expanded','true'); document.body.classList.add('cs-menu-open'); document.dispatchEvent(new Event('cinema:modal')); });
  drawer?.querySelector('[data-close-menu]').addEventListener('click', () => drawer.close());
  drawer?.addEventListener('click', event => { const r=drawer.getBoundingClientRect(); if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom) drawer.close(); });
  drawer?.addEventListener('close', () => { toggle.setAttribute('aria-expanded','false'); document.body.classList.remove('cs-menu-open'); toggle.focus(); document.dispatchEvent(new Event('cinema:modal')); });
  const slides=[...document.querySelectorAll('.store-slide')], dots=[...document.querySelectorAll('[data-slide]')];
  let current=0;
  function show(index){current=(index+slides.length)%slides.length;slides.forEach((slide,i)=>slide.hidden=i!==current);dots.forEach((dot,i)=>dot.setAttribute('aria-pressed',String(i===current)));const status=document.querySelector('.store-position');if(status)status.textContent=`${current+1} / ${slides.length}`;}
  if(slides.length){document.querySelector('[data-slide-prev]').addEventListener('click',()=>show(current-1));document.querySelector('[data-slide-next]').addEventListener('click',()=>show(current+1));dots.forEach((dot,i)=>dot.addEventListener('click',()=>show(i)));}
})();
