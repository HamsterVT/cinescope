(() => {
  'use strict';
  document.querySelectorAll('[data-show-password]').forEach(button => {
    button.addEventListener('click', () => {
      const input = document.getElementById(button.getAttribute('aria-controls'));
      const visible = input.type === 'password';
      input.type = visible ? 'text' : 'password';
      button.textContent = visible ? 'Скрыть' : 'Показать';
      button.setAttribute('aria-pressed', String(visible));
    });
  });
  document.querySelectorAll('[data-password-rules]').forEach(input => {
    const update = () => {
      const value = input.value, length = Array.from(value).length;
      const valid = {length: length >= 12 && length <= 128, lower: /[a-z]/.test(value),
        upper: /[A-Z]/.test(value), digit: /[0-9]/.test(value),
        symbol: /[!@#$%^&*()_+\-=\[\]{};'"\\:|<>?,./`~]/.test(value)};
      input.closest('.password-group').querySelectorAll('[data-rule]').forEach(item => {
        item.classList.toggle('rule-met', valid[item.dataset.rule]);
      });
      input.setCustomValidity(value && !Object.values(valid).every(Boolean)
        ? 'От 12 до 128 символов: латинские буквы a–z и A–Z, цифра 0–9 и спецсимвол, например ! или #.' : '');
    };
    input.addEventListener('input', update); update();
  });
})();
