/* The Print button on the printable record and report pages (data-print-page). */
document.querySelectorAll('[data-print-page]').forEach(function (button) {
  button.addEventListener('click', function () { window.print(); });
});
