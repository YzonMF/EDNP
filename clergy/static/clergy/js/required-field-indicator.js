/* Colors each field's "*" required-mark red while empty and green once
 * filled. Runs on load (so fields pre-filled from a saved profile start
 * green) and again on every input/change, including values set
 * programmatically by the address picker. */
(function () {
  function isFilled(control) {
    if (!control) return false;
    if (control.tagName === 'SELECT') return control.value !== '';
    return control.value.trim() !== '';
  }

  document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('.field').forEach((field) => {
      const mark = field.querySelector('.req-mark');
      const control = field.querySelector('input, select, textarea');
      if (!mark || !control) return;

      const update = () => mark.classList.toggle('filled', isFilled(control));
      update();
      control.addEventListener('input', update);
      control.addEventListener('change', update);
    });
  });
})();
