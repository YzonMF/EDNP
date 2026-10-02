/* Clergy Records page UI: the deanery -> parish cascade dropdown for the New
   Record modal, and the Link Account modal. Personal Information and
   Documents are now a real per-record page (record-detail.html), not a
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
 document.getElementById(fieldId).style.display=(status==='On Leave')?'':'none';
}

function openNewRecordModal(){
 populateDeaneries('newDeanery');
 document.getElementById('newDeanery').value='';
 updateParishOptions('newDeanery','newAssignment');
 newStatus.value='Active';
 newLeaveReason.value='';
 toggleLeaveReason('newStatus','newLeaveReasonField');
 openModal('recordModal');
}

function openLinkAccountModal(button){
 const id=button.dataset.recordId;
 document.getElementById('linkAccountName').textContent=button.dataset.recordName;
 document.getElementById('linkAccountForm').action=`/registrar/clergy-records/${id}/link-account/`;
 openModal('linkAccountModal');
}
