/* Clergy Records page UI: the deanery -> parish cascade dropdown for the New
   Record modal, and the Link Account and Lost Phone modals. The record page
   (record-detail.html) loads this too, for the same cascade. Personal
   Information and Documents are now a real per-record page, not a
   JS-populated modal. Every value below comes from what Django already
   rendered into the page (the embedded deaneries JSON) — nothing here
   holds or computes application data on its own. Depends on ui.js
   (openModal, closeModal). */

const deaneries=JSON.parse(document.getElementById('deaneries-data').textContent);

function populateDeaneries(selectId){
 const sel=document.getElementById(selectId);
 if(!sel)return;
 sel.innerHTML='<option value="">Select Deanery</option>'+Object.entries(deaneries).map(([id,d])=>`<option value="${id}">${d.name}</option>`).join('');
}

function updateParishOptions(deanerySelectId,assignmentSelectId){
 const deaneryId=document.getElementById(deanerySelectId).value;
 const parishSel=document.getElementById(assignmentSelectId);
 if(!deaneryId){
  parishSel.innerHTML='<option value="">Select Deanery first</option>';
  parishSel.disabled=true;
  return;
 }
 parishSel.innerHTML='<option value="">Select Parish / Assignment</option>'+deaneries[deaneryId].parishes.map(p=>`<option>${p}</option>`).join('');
 parishSel.disabled=false;
}

function toggleLeaveReason(statusSelectId,fieldId){
 const status=document.getElementById(statusSelectId).value;
 document.getElementById(fieldId).classList.toggle('hidden',status!=='On Leave');
}

function openNewRecordModal(e){
 if(e)e.preventDefault();
 populateDeaneries('newDeanery');
 document.getElementById('newDeanery').value='';
 updateParishOptions('newDeanery','newAssignment');
 newStatus.value='Active';
 newLeaveReason.value='';
 toggleLeaveReason('newStatus','newLeaveReasonField');
 openModal('recordModal');
}

function openResetAccountModal(button){
 const id=button.dataset.recordId;
 document.getElementById('resetAccountName').textContent=button.dataset.recordName;
 document.getElementById('resetAccountForm').action=`/registrar/clergy-records/${id}/reset-account/`;
 openModal('resetAccountModal');
}

function openLinkAccountModal(button){
 const id=button.dataset.recordId;
 document.getElementById('linkAccountName').textContent=button.dataset.recordName;
 document.getElementById('linkAccountForm').action=`/registrar/clergy-records/${id}/link-account/`;
 openModal('linkAccountModal');
}

// A deanery <select data-parish-target="id"> refills that parish list; a status
// <select data-leave-field="id"> shows that Leave Reason field only for On Leave
document.querySelectorAll('[data-parish-target]').forEach(sel=>{
 sel.addEventListener('change',()=>updateParishOptions(sel.id,sel.dataset.parishTarget));
});
document.querySelectorAll('[data-leave-field]').forEach(sel=>{
 sel.addEventListener('change',()=>toggleLeaveReason(sel.id,sel.dataset.leaveField));
});

const newRecordButton=document.getElementById('newRecordButton');
if(newRecordButton)newRecordButton.addEventListener('click',openNewRecordModal);
document.querySelectorAll('[data-reset-account]').forEach(btn=>btn.addEventListener('click',()=>openResetAccountModal(btn)));
document.querySelectorAll('[data-link-account]').forEach(btn=>btn.addEventListener('click',()=>openLinkAccountModal(btn)));
