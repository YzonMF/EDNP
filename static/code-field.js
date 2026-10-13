/* Counts the Resend button (templates/_code_field.html) down to when another
   code may be asked for. The server enforces the same wait; this only shows it. */
(function () {
  var btn = document.querySelector('.code-send');
  if (!btn) return;
  var left = parseInt(btn.dataset.wait, 10) || 0;
  if (left <= 0 || !btn.disabled) return;
  var timer = setInterval(function () {
    left -= 1;
    if (left <= 0) {
      clearInterval(timer);
      btn.disabled = false;
      btn.textContent = btn.dataset.label;
    } else {
      btn.textContent = 'Resend in ' + left + 's';
    }
  }, 1000);
})();
