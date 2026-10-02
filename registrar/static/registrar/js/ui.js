/* Pure UI helpers shared by every registrar page: modals and the toast
   notification. No application data lives here — every clergy record and
   portal account is rendered server-side by Django and saved via real form
   POSTs. */

function openModal(id){
 const el=document.getElementById(id);
 if(el)el.classList.add('show');
}

function closeModal(id){
 const el=document.getElementById(id);
 if(el)el.classList.remove('show');
}

let toastTimer;
function showToast(message,level){
 const el=document.getElementById('toast');
 if(!el)return;
 el.textContent=message;
 el.className='toast show'+(level?' toast-'+level:'');
 clearTimeout(toastTimer);
 toastTimer=setTimeout(()=>el.classList.remove('show'),level==='error'?4200:2600);
}

function quickPrintFile(url){
 const win=window.open(url,'_blank');
 if(!win){showToast('Please allow pop-ups to print this file.');return;}
 win.addEventListener('load',()=>{win.focus();win.print();});
}

document.querySelectorAll('.modal-bg').forEach(modal=>{
 modal.addEventListener('click',e=>{
  if(e.target===modal)modal.classList.remove('show');
 });
});
