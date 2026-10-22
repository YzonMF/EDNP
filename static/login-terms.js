/* Opens and closes the Terms and Conditions modal on the login screen
   (templates/index.html) and lets the user accept directly from the modal. */
(function () {
  var modal = document.getElementById('terms-modal');
  var openBtn = document.getElementById('open-terms-btn');
  var agreeBtn = document.getElementById('agree-terms-btn');
  var checkbox = document.getElementById('accept-terms');
  var termsBox = document.getElementById('terms-row-box');

  if (!modal || !openBtn) return;

  function openModal() {
    modal.classList.remove('hidden');
    modal.setAttribute('aria-hidden', 'false');
    document.body.style.overflow = 'hidden';
    if (agreeBtn) agreeBtn.focus();
  }

  function closeModal() {
    modal.classList.add('hidden');
    modal.setAttribute('aria-hidden', 'true');
    document.body.style.overflow = '';
    openBtn.focus();
  }

  openBtn.addEventListener('click', function (e) {
    e.preventDefault();
    openModal();
  });

  modal.querySelectorAll('[data-close="terms-modal"]').forEach(function (btn) {
    btn.addEventListener('click', function (e) {
      e.preventDefault();
      closeModal();
    });
  });

  modal.addEventListener('click', function (e) {
    if (e.target === modal) closeModal();
  });

  if (agreeBtn) {
    agreeBtn.addEventListener('click', function (e) {
      e.preventDefault();
      if (checkbox) {
        checkbox.checked = true;
        try {
          checkbox.dispatchEvent(new Event('change', { bubbles: true }));
        } catch (_) {}
      }
      if (termsBox) {
        termsBox.classList.add('has-focus');
        setTimeout(function () {
          termsBox.classList.remove('has-focus');
        }, 1200);
      }
      closeModal();
    });
  }

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && !modal.classList.contains('hidden')) {
      closeModal();
    }
  });
})();
