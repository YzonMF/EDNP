/* Interactive skeleton loading: when the user follows an in-site link, the
   page body is swapped for a shimmering placeholder until the next page
   arrives; submitting a form puts its button into a busy state. */
(function () {
  var SKIP_PATH = /\/(download|view|preview|database)\//;

  var bar = null;
  function progress(on) {
    if (!bar) {
      bar = document.createElement('div');
      bar.className = 'sk-progress';
      bar.setAttribute('aria-hidden', 'true');
      document.body.appendChild(bar);
    }
    bar.classList.toggle('on', on);
  }

  function line(w, cls) { return '<span class="sk-line ' + (w || '') + ' ' + (cls || '') + '"></span>'; }

  function rows(n) {
    var out = '';
    for (var i = 0; i < n; i++) {
      out += '<div class="sk-row">' + line('w90') + line('w70') + line('w50') + '<span class="sk-btn"></span></div>';
    }
    return out;
  }

  function buildSkeleton(withCards) {
    var wrap = document.createElement('div');
    wrap.className = 'sk-wrap';
    wrap.setAttribute('aria-hidden', 'true');
    var html = '<div class="sk-status"><span class="sk-spinner"></span>Loading…</div>';
    if (withCards) {
      html += '<div class="sk-cards">' +
        '<div class="sk-card">' + line('w50') + line('w30', 'lg') + line('w70') + '</div>' +
        '<div class="sk-card">' + line('w50') + line('w30', 'lg') + line('w70') + '</div></div>';
    }
    html += '<div class="sk-panel">' + line('w30', 'lg') + line('w50') + '<div style="margin-top:16px">' + rows(6) + '</div></div>';
    wrap.innerHTML = html;
    return wrap;
  }

  function showSkeleton(link) {
    var main = document.querySelector('main');
    if (!main || main.classList.contains('is-loading')) return;
    var path = link ? link.getAttribute('href') : '';
    var withCards = /\/dashboard\/?$/.test(path);
    progress(true);
    if (link) link.classList.add('is-pending');
    main.classList.add('is-loading');
    main.appendChild(buildSkeleton(withCards));
    window.scrollTo(0, 0);
  }

  function clearSkeleton() {
    progress(false);
    document.querySelectorAll('.is-pending').forEach(function (n) { n.classList.remove('is-pending'); });
    var main = document.querySelector('main');
    if (!main) return;
    main.classList.remove('is-loading');
    main.querySelectorAll('.sk-wrap').forEach(function (n) { n.remove(); });
    document.querySelectorAll('.is-busy').forEach(function (b) { b.classList.remove('is-busy'); });
  }

  document.addEventListener('click', function (e) {
    var a = e.target.closest && e.target.closest('a[href]');
    if (!a || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    if (a.target && a.target !== '_self') return;
    if (a.hasAttribute('download')) return;
    var href = a.getAttribute('href');
    if (!href || href.charAt(0) === '#' || /^(mailto:|tel:|javascript:)/i.test(href)) return;
    if (a.origin !== location.origin || SKIP_PATH.test(a.pathname)) return;
    if (a.pathname === location.pathname && a.search === location.search) return;
    showSkeleton(a);
  });

  document.addEventListener('submit', function (e) {
    if (e.defaultPrevented) return;
    var form = e.target;
    if (form.target && form.target !== '_self') return;
    var btn = e.submitter || form.querySelector('button[type=submit],button:not([type]),input[type=submit]');
    progress(true);
    // A POST that answers with a file download never leaves the page — don't spin forever
    setTimeout(clearSkeleton, 20000);
    if (typeof showToast === 'function') {
      showToast(form.querySelector('input[type=file]') ? 'Uploading…' : 'Working on it…', 'working');
    }
    if (btn) {
      // Defer so the button's own value is still submitted
      setTimeout(function () { btn.classList.add('is-busy'); }, 0);
    }
  });

  // Icon-only nav on phones: keep every link labelled for hover/screen readers
  document.querySelectorAll('.sidebar .nav a').forEach(function (a) {
    var label = (a.textContent || '').replace(/\s+/g, ' ').replace(/\d+\/\d+|\d+$/, '').trim();
    if (label) { a.title = label; a.setAttribute('aria-label', label); }
  });

  // Back/forward cache restores the old page with the skeleton still on it
  window.addEventListener('pageshow', clearSkeleton);
})();
