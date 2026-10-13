/* Hovering a notification's "2 hours ago" shows the exact time, in the reader's own time zone. */
document.querySelectorAll('.notif-meta time[data-epoch]').forEach(function (el) {
  var when = new Date(Number(el.dataset.epoch) * 1000);
  if (!isNaN(when)) el.title = when.toLocaleString();
});
