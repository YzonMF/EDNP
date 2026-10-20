/* Ember Letters interactive uploader (shared by Registrar & Clergy portals).
   - 3 to 4 year blocks (or more as needed), newest-first year dropdown
   - Sequential quarter unlocking (Q1 -> Q2 -> Q3 -> Q4 -> next year block)
   - Skip quarter option when a past quarter letter is unavailable
   - Instant client-side validation (PDF/JPG/PNG, max 5 MB)
   - Single-concurrency upload queue (1 upload at a time per page)
   - Replace in-flight cancels active request via AbortController
   - beforeunload guard while uploads are queued or in flight */

(function () {
  var configEl = document.getElementById('ember-config-data');
  var container = document.getElementById('emberYearsContainer');
  if (!configEl || !container) return;

  var config = JSON.parse(configEl.textContent);
  var MAX_BYTES = config.max_bytes || (5 * 1024 * 1024);
  var ALLOWED_EXT = /\.(pdf|jpe?g|png)$/i;
  var ALLOWED_MIMES = ['application/pdf', 'image/jpeg', 'image/png', ''];

  var blocks = [];
  var nextBlockId = 1;
  var uploadQueue = [];
  var activeJob = null;

  function getCsrfToken() {
    var input = document.querySelector('input[name="csrfmiddlewaretoken"]');
    if (input && input.value) return input.value;
    var match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : '';
  }

  function makeEmptyQuarters() {
    var q = {};
    for (var i = 1; i <= 4; i++) {
      q[i] = {
        status: 'empty', // 'empty' | 'skipped' | 'queued' | 'uploading' | 'saved' | 'error'
        letter: null,
        error: '',
        pendingFile: null,
      };
    }
    return q;
  }

  function createBlock(year) {
    return {
      id: nextBlockId++,
      year: year ? Number(year) : null,
      quarters: makeEmptyQuarters(),
    };
  }

  function initBlocks() {
    var byYear = {};
    var yearsOrder = [];
    (config.letters || []).forEach(function (letter) {
      var y = Number(letter.year);
      if (!byYear[y]) {
        byYear[y] = [];
        yearsOrder.push(y);
      }
      byYear[y].push(letter);
    });

    yearsOrder.sort(function (a, b) { return b - a; });

    yearsOrder.forEach(function (y) {
      var b = createBlock(y);
      byYear[y].forEach(function (letter) {
        var qNum = Number(letter.quarter);
        if (qNum >= 1 && qNum <= 4) {
          b.quarters[qNum].status = 'saved';
          b.quarters[qNum].letter = letter;
        }
      });
      blocks.push(b);
    });

    var lastHasQ4 = blocks.length > 0 && blocks[blocks.length - 1].quarters[4].status === 'saved';
    var targetCount = Math.max(3, blocks.length + (lastHasQ4 ? 1 : 0));
    while (blocks.length < targetCount) {
      blocks.push(createBlock(null));
    }
  }

  function blockHasSaved(block) {
    for (var q = 1; q <= 4; q++) {
      if (block.quarters[q].status === 'saved' || block.quarters[q].status === 'uploading' || block.quarters[q].status === 'queued') {
        return true;
      }
    }
    return false;
  }

  function blockSavedCount(block) {
    var count = 0;
    for (var q = 1; q <= 4; q++) {
      if (block.quarters[q].status === 'saved') count++;
    }
    return count;
  }

  function isQuarterDoneOrSkipped(block, q) {
    var st = block.quarters[q].status;
    if (st === 'saved' || st === 'skipped') return true;
    // On returning records where a later quarter already has a saved file,
    // treat earlier empty quarters as implicitly passed so later ones stay accessible.
    for (var later = q + 1; later <= 4; later++) {
      if (block.quarters[later].status === 'saved') return true;
    }
    return false;
  }

  function isBlockUnlocked(index) {
    if (index === 0) return true;
    var block = blocks[index];
    if (block.year !== null || blockHasSaved(block)) return true;
    var prev = blocks[index - 1];
    return prev.year !== null && isQuarterDoneOrSkipped(prev, 4);
  }

  function isQuarterUnlocked(block, blockIndex, q) {
    if (!isBlockUnlocked(blockIndex)) return false;
    if (!block.year) return false;
    var st = block.quarters[q].status;
    if (st === 'saved' || st === 'uploading' || st === 'queued' || st === 'error' || st === 'skipped') {
      return true;
    }
    if (q === 1) return true;
    return isQuarterDoneOrSkipped(block, q - 1);
  }

  function ensureNextBlockIfQ4Done(blockIndex) {
    var block = blocks[blockIndex];
    if (blockIndex === blocks.length - 1 && block.year && isQuarterDoneOrSkipped(block, 4) && blocks.length < 4) {
      blocks.push(createBlock(null));
    }
  }

  function computeSummary() {
    var uploaded = 0;
    var yearsMap = {};
    blocks.forEach(function (b) {
      for (var q = 1; q <= 4; q++) {
        if (b.quarters[q].status === 'saved') {
          uploaded++;
          if (b.year) yearsMap[b.year] = true;
        }
      }
    });
    var yearCount = Object.keys(yearsMap).length;
    var target = Math.max(12, yearCount * 4);
    return { uploaded: uploaded, years: yearCount, target: target };
  }

  function updateSummaryUI(summary) {
    summary = summary || computeSummary();
    var overallEl = document.getElementById('emberOverallText');
    if (overallEl) {
      if (summary.uploaded > 0) {
        overallEl.innerHTML = '<b>' + summary.uploaded + '</b> of <b>' + summary.target + '</b> uploaded (' + summary.years + ' year' + (summary.years === 1 ? '' : 's') + ')';
      } else {
        overallEl.innerHTML = '<b>0</b> of <b>12</b> uploaded';
      }
    }
    document.querySelectorAll('[data-ember-tab-badge]').forEach(function (el) {
      el.textContent = summary.uploaded + '/' + summary.target;
    });
    document.querySelectorAll('[data-ember-card-badge]').forEach(function (el) {
      el.textContent = summary.uploaded + ' of ' + summary.target + ' uploaded';
    });
    document.querySelectorAll('[data-ember-card-detail]').forEach(function (el) {
      if (summary.uploaded > 0) {
        el.innerHTML = '<i class="fa-solid fa-file-lines"></i> ' + summary.uploaded + ' of ' + summary.target + ' quarterly letters uploaded across ' + summary.years + ' year' + (summary.years === 1 ? '' : 's') + '.';
      } else {
        el.textContent = 'No quarterly Ember letters uploaded yet.';
      }
    });
  }

  function updateQueueBadge() {
    var badge = document.getElementById('emberQueueBadge');
    if (!badge) return;
    var totalPending = uploadQueue.length + (activeJob ? 1 : 0);
    if (totalPending > 0) {
      badge.classList.remove('hidden');
      badge.innerHTML = '<span class="ember-spinner"></span> Uploading (' + totalPending + ' remaining)…';
    } else {
      badge.classList.add('hidden');
      badge.textContent = '';
    }
  }

  function validateFile(file) {
    if (!file) return 'Please choose a file.';
    if (!ALLOWED_EXT.test(file.name || '')) {
      return 'Only PDF, JPG and PNG files are allowed.';
    }
    if (file.type && ALLOWED_MIMES.indexOf(file.type.toLowerCase()) === -1) {
      return 'Only PDF, JPG and PNG files are allowed.';
    }
    if (file.size > MAX_BYTES) {
      return 'File is too large — the maximum is 5 MB.';
    }
    return null;
  }

  function findBlock(blockId) {
    for (var i = 0; i < blocks.length; i++) {
      if (blocks[i].id === blockId) return { block: blocks[i], index: i };
    }
    return null;
  }

  function enqueueUpload(blockId, quarter, file) {
    var found = findBlock(blockId);
    if (!found || !found.block.year) return;
    var block = found.block;
    var qSlot = block.quarters[quarter];

    var err = validateFile(file);
    if (err) {
      qSlot.status = 'error';
      qSlot.error = err;
      qSlot.pendingFile = null;
      if (typeof showToast === 'function') showToast(err, 'error');
      render();
      return;
    }

    // Remove any queued job for this same slot
    uploadQueue = uploadQueue.filter(function (job) {
      return !(job.blockId === blockId && job.quarter === quarter);
    });

    // If this exact slot is currently uploading, abort the in-flight request
    if (activeJob && activeJob.blockId === blockId && activeJob.quarter === quarter) {
      activeJob.abortedByReplace = true;
      activeJob.controller.abort();
    }

    qSlot.error = '';
    qSlot.pendingFile = file;
    qSlot.status = 'queued';

    uploadQueue.push({
      blockId: blockId,
      year: block.year,
      quarter: quarter,
      file: file,
      prevLetter: qSlot.letter,
    });

    render();
    processQueue();
  }

  function processQueue() {
    if (activeJob || uploadQueue.length === 0) {
      updateQueueBadge();
      return;
    }

    var job = uploadQueue.shift();
    var found = findBlock(job.blockId);
    if (!found) {
      processQueue();
      return;
    }

    var block = found.block;
    var qSlot = block.quarters[job.quarter];
    qSlot.status = 'uploading';
    qSlot.error = '';

    var controller = new AbortController();
    activeJob = {
      blockId: job.blockId,
      quarter: job.quarter,
      file: job.file,
      prevLetter: job.prevLetter,
      controller: controller,
      abortedByReplace: false,
    };

    render();
    updateQueueBadge();

    var formData = new FormData();
    formData.append('year', String(block.year));
    formData.append('quarter', String(job.quarter));
    formData.append('file', job.file);

    fetch(config.upload_url, {
      method: 'POST',
      headers: {
        'X-CSRFToken': getCsrfToken(),
      },
      body: formData,
      signal: controller.signal,
      credentials: 'same-origin',
    })
      .then(function (resp) {
        return resp.json().then(function (data) {
          return { status: resp.status, data: data };
        }).catch(function () {
          return { status: resp.status, data: { ok: false, error: 'Upload failed. Please try again.' } };
        });
      })
      .then(function (result) {
        var finishedJob = activeJob;
        activeJob = null;
        if (finishedJob && finishedJob.abortedByReplace) {
          processQueue();
          return;
        }
        if (result.status >= 200 && result.status < 300 && result.data && result.data.ok) {
          qSlot.status = 'saved';
          qSlot.letter = result.data.letter;
          qSlot.pendingFile = null;
          qSlot.error = '';
          ensureNextBlockIfQ4Done(found.index);
          updateSummaryUI(result.data.summary);
          if (typeof showToast === 'function') {
            showToast('Ember Letter (' + block.year + ' Q' + job.quarter + ') saved.');
          }
        } else {
          var errMsg = (result.data && result.data.error) || 'Upload failed — Try again.';
          qSlot.status = 'error';
          qSlot.error = errMsg;
          if (typeof showToast === 'function') showToast(errMsg, 'error');
        }
        render();
        processQueue();
      })
      .catch(function (err) {
        var finishedJob = activeJob;
        activeJob = null;
        if ((err && err.name === 'AbortError') || (finishedJob && finishedJob.abortedByReplace)) {
          processQueue();
          return;
        }
        qSlot.status = 'error';
        qSlot.error = 'Network error — could not upload file.';
        if (typeof showToast === 'function') showToast(qSlot.error, 'error');
        render();
        processQueue();
      });
  }

  function handleDelete(blockId, quarter) {
    var found = findBlock(blockId);
    if (!found) return;
    var block = found.block;
    var qSlot = block.quarters[quarter];
    var letter = qSlot.letter;
    if (!letter || !letter.delete_url) return;

    if (!window.confirm('Remove Ember Letter for ' + block.year + ' Q' + quarter + '?')) {
      return;
    }

    fetch(letter.delete_url, {
      method: 'POST',
      headers: {
        'X-CSRFToken': getCsrfToken(),
      },
      credentials: 'same-origin',
    })
      .then(function (resp) { return resp.json(); })
      .then(function (data) {
        if (data && data.ok) {
          qSlot.status = 'empty';
          qSlot.letter = null;
          qSlot.pendingFile = null;
          qSlot.error = '';
          updateSummaryUI(data.summary);
          if (typeof showToast === 'function') {
            showToast('Ember Letter (' + block.year + ' Q' + quarter + ') removed.');
          }
          render();
        } else {
          var msg = (data && data.error) || 'Could not remove Ember Letter.';
          if (typeof showToast === 'function') showToast(msg, 'error');
        }
      })
      .catch(function () {
        if (typeof showToast === 'function') showToast('Network error while removing file.', 'error');
      });
  }

  function escapeHtml(str) {
    return String(str == null ? '' : str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function selectedYearsSet(excludeBlockId) {
    var used = {};
    blocks.forEach(function (b) {
      if (b.id !== excludeBlockId && b.year) {
        used[b.year] = true;
      }
    });
    return used;
  }

  function render() {
    var html = '';
    blocks.forEach(function (block, idx) {
      var blockUnlocked = isBlockUnlocked(idx);
      var hasSaved = blockHasSaved(block);
      var savedCount = blockSavedCount(block);
      var usedYears = selectedYearsSet(block.id);

      var yearChoices = (config.years_choices || []).slice();
      if (block.year && yearChoices.indexOf(block.year) === -1) {
        yearChoices.push(block.year);
        yearChoices.sort(function (a, b) { return b - a; });
      }

      var optionsHtml = '<option value="">Select Year…</option>';
      yearChoices.forEach(function (y) {
        if (usedYears[y] && y !== block.year) return;
        var sel = block.year === y ? ' selected' : '';
        optionsHtml += '<option value="' + y + '"' + sel + '>' + y + '</option>';
      });

      var selectDisabled = (!blockUnlocked || hasSaved) ? ' disabled' : '';
      var badgeClass = savedCount === 4 ? 'green active-b' : (savedCount > 0 ? 'blue abroad-b' : 'amber pending-b');

      html += '<div class="ember-year-block' + (blockUnlocked ? '' : ' is-locked-block') + '" data-block-id="' + block.id + '">';
      html += '  <div class="ember-year-head">';
      html += '    <div class="ember-year-title-group">';
      html += '      <span class="ember-year-label">Year ' + (idx + 1) + '</span>';
      html += '      <select class="ember-year-select" data-action="select-year" data-block-id="' + block.id + '"' + selectDisabled + '>' + optionsHtml + '</select>';
      if (hasSaved) {
        html += '    <span class="ember-year-lock-hint" title="Remove all uploaded quarters in this block to change its year."><i class="fa-solid fa-lock"></i> Year locked</span>';
      } else if (!blockUnlocked) {
        html += '    <span class="ember-year-lock-hint"><i class="fa-solid fa-lock"></i> Complete or skip Year ' + idx + ' Q4 to unlock</span>';
      }
      html += '    </div>';
      html += '    <span class="badge ' + badgeClass + '">' + savedCount + '/4 uploaded</span>';
      html += '  </div>';

      html += '  <div class="ember-quarters">';
      for (var q = 1; q <= 4; q++) {
        html += renderQuarterRow(block, idx, q);
      }
      html += '  </div>';
      html += '</div>';
    });

    container.innerHTML = html;
    updateQueueBadge();
  }

  function renderQuarterRow(block, blockIdx, q) {
    var qSlot = block.quarters[q];
    var unlocked = isQuarterUnlocked(block, blockIdx, q);
    var fileInputId = 'emberFile_' + block.id + '_' + q;
    var rowStateClass = !unlocked ? 'state-locked' : ('state-' + qSlot.status);

    var leftInfo = '';
    var rightActions = '';

    if (!unlocked) {
      var reason = !block.year ? 'Select a year above to unlock Q1' : ('Upload or skip Q' + (q - 1) + ' to unlock');
      leftInfo = '<div class="ember-q-status-line"><span class="muted"><i class="fa-solid fa-lock"></i> Locked — ' + reason + '</span></div>';
    } else if (qSlot.status === 'saved' && qSlot.letter) {
      var l = qSlot.letter;
      leftInfo =
        '<div class="ember-q-status-line">' +
        '  <span class="badge green active-b"><i class="fa-solid fa-check"></i> Saved</span>' +
        '  <span class="ember-q-filename" title="' + escapeHtml(l.file_name) + '">' + escapeHtml(l.file_name) + '</span>' +
        '</div>' +
        '<div class="ember-q-meta">' + escapeHtml(l.file_size_label) + ' · Submitted ' + escapeHtml(l.submitted_date) + '</div>';

      rightActions =
        '<a class="btn secondary small" href="' + escapeHtml(l.view_url) + '" target="_blank" rel="noopener"><i class="fa-solid fa-eye"></i> View</a>' +
        '<a class="btn secondary small" href="' + escapeHtml(l.download_url) + '"><i class="fa-solid fa-download"></i> Download</a>' +
        '<button type="button" class="btn secondary small" data-action="pick-file" data-input-id="' + fileInputId + '"><i class="fa-solid fa-rotate"></i> Replace</button>' +
        '<button type="button" class="btn danger small" data-action="delete-quarter" data-block-id="' + block.id + '" data-quarter="' + q + '"><i class="fa-solid fa-trash-can"></i> Remove</button>';
    } else if (qSlot.status === 'queued') {
      var qName = qSlot.pendingFile ? qSlot.pendingFile.name : '';
      leftInfo =
        '<div class="ember-q-status-line">' +
        '  <span class="badge blue abroad-b"><i class="fa-solid fa-clock"></i> Waiting…</span>' +
        '  <span class="ember-q-filename">' + escapeHtml(qName) + '</span>' +
        '</div>' +
        '<div class="ember-q-meta">Queued — will upload automatically</div>';

      rightActions =
        '<button type="button" class="btn secondary small" data-action="pick-file" data-input-id="' + fileInputId + '"><i class="fa-solid fa-rotate"></i> Replace</button>';
    } else if (qSlot.status === 'uploading') {
      var uName = qSlot.pendingFile ? qSlot.pendingFile.name : '';
      leftInfo =
        '<div class="ember-q-status-line">' +
        '  <span class="ember-spinner"></span>' +
        '  <b>Uploading…</b>' +
        '  <span class="ember-q-filename">' + escapeHtml(uName) + '</span>' +
        '</div>';

      rightActions =
        '<button type="button" class="btn secondary small" data-action="pick-file" data-input-id="' + fileInputId + '"><i class="fa-solid fa-rotate"></i> Replace</button>';
    } else if (qSlot.status === 'error') {
      leftInfo =
        '<div class="ember-q-status-line">' +
        '  <span class="badge red inactive-b"><i class="fa-solid fa-circle-exclamation"></i> Failed</span>' +
        '  <span class="ember-q-error">' + escapeHtml(qSlot.error || 'Upload failed — Try again.') + '</span>' +
        '</div>';

      rightActions = '';
      if (qSlot.pendingFile) {
        rightActions += '<button type="button" class="btn small" data-action="retry-quarter" data-block-id="' + block.id + '" data-quarter="' + q + '"><i class="fa-solid fa-rotate-right"></i> Retry</button>';
      }
      rightActions += '<button type="button" class="btn secondary small" data-action="pick-file" data-input-id="' + fileInputId + '"><i class="fa-solid fa-upload"></i> Choose file</button>';
    } else if (qSlot.status === 'skipped') {
      leftInfo =
        '<div class="ember-q-status-line">' +
        '  <span class="badge amber pending-b">Skipped</span>' +
        '  <span class="muted">Quarter skipped — you can still upload a file here any time.</span>' +
        '</div>';

      rightActions =
        '<button type="button" class="btn secondary small" data-action="pick-file" data-input-id="' + fileInputId + '"><i class="fa-solid fa-upload"></i> Upload Q' + q + '</button>';
    } else {
      // Unlocked empty slot
      leftInfo =
        '<div class="ember-q-status-line">' +
        '  <span class="muted">Ready to upload ' + (block.year ? (block.year + ' ') : '') + 'Quarter ' + q + ' (PDF, JPG or PNG, max 5 MB)</span>' +
        '</div>';

      rightActions =
        '<button type="button" class="btn small" data-action="pick-file" data-input-id="' + fileInputId + '"><i class="fa-solid fa-upload"></i> Upload Q' + q + '</button>' +
        '<button type="button" class="btn secondary small" data-action="skip-quarter" data-block-id="' + block.id + '" data-quarter="' + q + '">Skip quarter</button>';
    }

    return (
      '<div class="ember-quarter-row ' + rowStateClass + '" data-block-id="' + block.id + '" data-quarter="' + q + '">' +
      '  <div class="ember-q-left">' +
      '    <span class="ember-q-pill">Q' + q + '</span>' +
      '    <div class="ember-q-info">' + leftInfo + '</div>' +
      '  </div>' +
      '  <div class="ember-q-actions">' +
      '    <input type="file" id="' + fileInputId + '" class="ember-file-input" accept=".pdf,.jpg,.jpeg,.png" data-block-id="' + block.id + '" data-quarter="' + q + '">' +
           rightActions +
      '  </div>' +
      '</div>'
    );
  }

  // Event delegation on container
  container.addEventListener('change', function (e) {
    var target = e.target;
    if (target.matches('[data-action="select-year"]')) {
      var blockId = Number(target.getAttribute('data-block-id'));
      var found = findBlock(blockId);
      if (!found || blockHasSaved(found.block)) return;
      var val = target.value ? Number(target.value) : null;
      found.block.year = val;
      render();
      return;
    }

    if (target.matches('.ember-file-input')) {
      var fBlockId = Number(target.getAttribute('data-block-id'));
      var quarter = Number(target.getAttribute('data-quarter'));
      var file = target.files && target.files[0];
      if (file) {
        enqueueUpload(fBlockId, quarter, file);
      }
      target.value = '';
    }
  });

  container.addEventListener('click', function (e) {
    var btn = e.target.closest('[data-action]');
    if (!btn) return;
    var action = btn.getAttribute('data-action');

    if (action === 'pick-file') {
      var inputId = btn.getAttribute('data-input-id');
      var input = document.getElementById(inputId);
      if (input) input.click();
      return;
    }

    var blockId = Number(btn.getAttribute('data-block-id'));
    var quarter = Number(btn.getAttribute('data-quarter'));
    var found = findBlock(blockId);
    if (!found) return;

    if (action === 'skip-quarter') {
      found.block.quarters[quarter].status = 'skipped';
      ensureNextBlockIfQ4Done(found.index);
      render();
      return;
    }

    if (action === 'retry-quarter') {
      var pending = found.block.quarters[quarter].pendingFile;
      if (pending) {
        enqueueUpload(blockId, quarter, pending);
      }
      return;
    }

    if (action === 'delete-quarter') {
      handleDelete(blockId, quarter);
    }
  });

  var addYearBtn = document.getElementById('emberAddYearBtn');
  if (addYearBtn) {
    addYearBtn.addEventListener('click', function () {
      blocks.push(createBlock(null));
      render();
    });
  }

  // Warn before closing tab if uploads are still running or queued
  window.addEventListener('beforeunload', function (e) {
    if (activeJob || uploadQueue.length > 0) {
      e.preventDefault();
      e.returnValue = '';
    }
  });

  // Generic tab switcher fallback if a page uses .page-tabs [data-tab] without record-detail.js
  var pageTabsEl = document.querySelector('.page-tabs[data-active-tab]');
  if (pageTabsEl && typeof window.selectTab !== 'function') {
    window.selectTab = function (tab) {
      document.querySelectorAll('.tab-content').forEach(function (el) { el.classList.remove('active'); });
      document.querySelectorAll('.page-tabs [data-tab]').forEach(function (btn) { btn.classList.remove('active'); });
      var btn = document.querySelector('.page-tabs [data-tab="' + tab + '"]') || document.querySelector('.page-tabs [data-tab]');
      if (!btn) return;
      btn.classList.add('active');
      var targetId = btn.getAttribute('data-target');
      var panel = targetId ? document.getElementById(targetId) : null;
      if (panel) panel.classList.add('active');
    };
    document.querySelectorAll('.page-tabs [data-tab]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        window.selectTab(btn.getAttribute('data-tab'));
      });
    });
    window.selectTab(pageTabsEl.getAttribute('data-active-tab') || 'checklist');
  }

  // Also support "[data-open-tab]" buttons (e.g. from the Ember Letters summary card)
  document.addEventListener('click', function (e) {
    var tabTrigger = e.target.closest('[data-open-tab]');
    if (!tabTrigger) return;
    e.preventDefault();
    var tabName = tabTrigger.getAttribute('data-open-tab');
    if (typeof window.selectTab === 'function') {
      window.selectTab(tabName);
      window.scrollTo({ top: 0, behavior: 'smooth' });
    }
  });

  initBlocks();
  render();
})();
