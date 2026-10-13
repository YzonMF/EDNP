/* Bulk Create Accounts, review step: the Create button says how many accounts
   the ticked names will make (and is off when none are ticked), and goes off
   once pressed so a double-click can't send the form twice. */
(function () {
  var form = document.getElementById('bulkConfirmForm');
  if (!form) return;
  var btn = document.getElementById('bulkConfirmBtn');
  function refresh() {
    var n = form.querySelectorAll('.pick:checked').length;
    btn.disabled = n === 0;
    btn.lastChild.textContent = ' Create ' + n + ' account' + (n === 1 ? '' : 's') + ' & open slips';
  }
  form.addEventListener('change', refresh);
  form.addEventListener('submit', function () { btn.disabled = true; });
  refresh();
})();
