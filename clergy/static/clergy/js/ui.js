/* Pure UI helpers shared by every clergy page: the toast notification, used
   to surface messages Django sends back after a form submit (e.g. "Profile
   updated successfully"), and the data-* attributes templates use instead of
   inline handlers (data-confirm, data-print-url). No application data lives here — the profile and
   record are rendered server-side by Django and saved via a real form POST. */

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

// The sidebar's menu button on narrow screens
document.querySelectorAll('.nav-toggle').forEach(btn=>{
 btn.addEventListener('click',()=>{
  const open=btn.closest('.sidebar').classList.toggle('nav-open');
  btn.setAttribute('aria-expanded',open);
 });
});

// <button data-print-url="url"> prints that file
document.addEventListener('click',e=>{
 const printer=e.target.closest('[data-print-url]');
 if(printer)quickPrintFile(printer.dataset.printUrl);
});

// <form data-confirm="Question?"> asks before it submits. Runs in the capture
// phase so a cancelled submit is stopped before skeleton.js shows it loading.
document.addEventListener('submit',e=>{
 const question=e.target.dataset.confirm;
 if(question&&!confirm(question))e.preventDefault();
},true);

// Messages Django attached to this response ("Record saved", errors), rendered
// into #flash-messages by base.html, shown as toasts
document.querySelectorAll('#flash-messages [data-level]').forEach(msg=>showToast(msg.textContent,msg.dataset.level));

// Live preview when choosing a profile picture on My Record & Profile
const profilePicInput=document.getElementById('profilePictureInput');
if(profilePicInput){
 profilePicInput.addEventListener('change',()=>{
  const file=profilePicInput.files&&profilePicInput.files[0];
  if(!file||!file.type.startsWith('image/'))return;
  const img=document.getElementById('profilePicPreviewImg');
  const initials=document.getElementById('profilePicInitials');
  if(img){img.src=URL.createObjectURL(file);img.classList.remove('hidden');}
  if(initials)initials.classList.add('hidden');
 });
}
