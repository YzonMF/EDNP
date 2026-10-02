/* Record detail page: the Personal Information / Documents tab switcher,
   and initializing the Deanery -> Assignment cascade with this record's
   existing values (the shared populateDeaneries/updateParishOptions/
   toggleLeaveReason helpers come from clergy-records.js). */

function selectTab(tab) {
  document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.page-tabs button').forEach(el => el.classList.remove('active'));
  document.getElementById('tab' + (tab === 'documents' ? 'Documents' : 'Personal')).classList.add('active');
  document.getElementById('tabBtn' + (tab === 'documents' ? 'Documents' : 'Personal')).classList.add('active');
}

function initRecordDetailTabs(activeTab) {
  selectTab(activeTab === 'documents' ? 'documents' : 'personal');

  const recordData = JSON.parse(document.getElementById('record-data').textContent);
  populateDeaneries('editDeanery');
  if (recordData.deanery_id) {
    document.getElementById('editDeanery').value = String(recordData.deanery_id);
  }
  updateParishOptions('editDeanery', 'editAssignment');

  const assignmentSelect = document.getElementById('editAssignment');
  if (recordData.assignment) {
    const hasOption = Array.from(assignmentSelect.options).some(o => o.value === recordData.assignment);
    if (!hasOption) {
      const opt = document.createElement('option');
      opt.value = recordData.assignment;
      opt.textContent = recordData.assignment;
      assignmentSelect.appendChild(opt);
    }
    assignmentSelect.value = recordData.assignment;
  }

  toggleLeaveReason('editStatus', 'editLeaveReasonField');
}
