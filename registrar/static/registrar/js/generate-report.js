/* Generate Report: the tick boxes in each table belong to the form named by
   their table's select-all box (data-picks). Its button is off until something
   is ticked and says how many are. A form marked data-confirm-picked asks
   before deleting what is ticked. */
document.querySelectorAll('[data-picks]').forEach(function (all) {
  var form = document.getElementById(all.dataset.picks);
  var button = form.querySelector('button[type=submit]');
  var label = button.lastElementChild.textContent;
  var picks = document.querySelectorAll('input[name="report_ids"][form="' + form.id + '"]');
  function ticked() {
    return document.querySelectorAll('input[name="report_ids"][form="' + form.id + '"]:checked').length;
  }
  function refresh() {
    var count = ticked();
    button.disabled = count === 0;
    button.lastElementChild.textContent = count ? label + ' (' + count + ')' : label;
    all.checked = count > 0 && count === picks.length;
    all.indeterminate = count > 0 && count < picks.length;
  }
  all.addEventListener('change', function () {
    picks.forEach(function (pick) { pick.checked = all.checked; });
    refresh();
  });
  picks.forEach(function (pick) { pick.addEventListener('change', refresh); });
  if ('confirmPicked' in form.dataset) {
    form.addEventListener('submit', function (event) {
      var count = ticked();
      if (!confirm('Delete ' + count + ' archived report' + (count === 1 ? '' : 's') + ' now? This cannot be undone.')) {
        event.preventDefault();
      }
    });
  }
  refresh();
});
