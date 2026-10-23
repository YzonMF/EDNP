/* Record detail page: the Personal Information / Documents / Ember Letters
   tab switcher, and initializing the Deanery -> Assignment cascade with this
   record's existing values (the shared populateDeaneries/updateParishOptions/
   toggleLeaveReason helpers come from clergy-records.js). The tab shown
   first is the tab bar's data-active-tab. */

function selectTab(tab) {
  var map = {
    personal: { content: 'tabPersonal', btn: 'tabBtnPersonal' },
    documents: { content: 'tabDocuments', btn: 'tabBtnDocuments' },
    ember: { content: 'tabEmber', btn: 'tabBtnEmber' },
  };
  var chosen = map[tab] || map.personal;
  document.querySelectorAll('.tab-content').forEach(function (el) { el.classList.remove('active'); });
  document.querySelectorAll('.page-tabs button').forEach(function (el) { el.classList.remove('active'); });
  var contentEl = document.getElementById(chosen.content);
  var btnEl = document.getElementById(chosen.btn);
  if (contentEl) contentEl.classList.add('active');
  if (btnEl) btnEl.classList.add('active');
  var topSaveBtn = document.getElementById('topSaveRecordBtn');
  if (topSaveBtn) {
    topSaveBtn.style.display = (tab === 'personal' || !tab) ? '' : 'none';
  }
}

function initRecordDetailTabs(activeTab) {
  selectTab(activeTab);

  var recordDataEl = document.getElementById('record-data');
  if (!recordDataEl) return;
  var recordData = JSON.parse(recordDataEl.textContent);
  populateDeaneries('editDeanery');
  if (recordData.deanery_id) {
    document.getElementById('editDeanery').value = String(recordData.deanery_id);
  }
  updateParishOptions('editDeanery', 'editAssignment');

  var assignmentSelect = document.getElementById('editAssignment');
  if (recordData.assignment) {
    var hasOption = Array.from(assignmentSelect.options).some(function (o) { return o.value === recordData.assignment; });
    if (!hasOption) {
      var opt = document.createElement('option');
      opt.value = recordData.assignment;
      opt.textContent = recordData.assignment;
      assignmentSelect.appendChild(opt);
    }
    assignmentSelect.value = recordData.assignment;
  }

  toggleLeaveReason('editStatus', 'editLeaveReasonField');
}

document.querySelectorAll('.page-tabs [data-tab]').forEach(function (button) {
  button.addEventListener('click', function () { selectTab(button.dataset.tab); });
});

initRecordDetailTabs(document.querySelector('[data-active-tab]').dataset.activeTab);
