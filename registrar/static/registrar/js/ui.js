/* Pure UI helpers shared by every registrar page: modals, the toast
   notification, and the data-* attributes templates use instead of inline
   handlers (data-confirm, data-print-url, data-close-modal, data-autosubmit).
   No application data lives here — every clergy record and
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

// The sidebar's menu button on narrow screens
document.querySelectorAll('.nav-toggle').forEach(btn=>{
 btn.addEventListener('click',()=>{
  const open=btn.closest('.sidebar').classList.toggle('nav-open');
  btn.setAttribute('aria-expanded',open);
 });
});

// <button data-close-modal="id"> closes that modal; <button data-print-url="url"> prints that file
document.addEventListener('click',e=>{
 const closer=e.target.closest('[data-close-modal]');
 if(closer)closeModal(closer.dataset.closeModal);
 const printer=e.target.closest('[data-print-url]');
 if(printer)quickPrintFile(printer.dataset.printUrl);
});

// <form data-confirm="Question?"> asks before it submits. Runs in the capture
// phase so a cancelled submit is stopped before skeleton.js shows it loading.
document.addEventListener('submit',e=>{
 const question=e.target.dataset.confirm;
 if(question&&!confirm(question))e.preventDefault();
},true);

// <select data-autosubmit> submits its form as soon as it changes
document.querySelectorAll('select[data-autosubmit]').forEach(sel=>{
 sel.addEventListener('change',()=>sel.form.submit());
});

// Messages Django attached to this response ("Record saved", errors), rendered
// into #flash-messages by base.html, shown as toasts
document.querySelectorAll('#flash-messages [data-level]').forEach(msg=>showToast(msg.textContent,msg.dataset.level));
