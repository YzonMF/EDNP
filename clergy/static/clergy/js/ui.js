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

// Checks a chosen profile picture the moment it is selected, with the same rules the
// server applies (registrar.forms.ProfilePictureForm): JPG/PNG, at most 5 MB, and the
// file's first bytes must really be a JPG or PNG. Resolves to an error message, or null.
const PROFILE_PIC_MAX_BYTES=5*1024*1024;
function profilePicError(file){
 if(!/\.(jpe?g|png)$/i.test(file.name))return Promise.resolve('Only JPG and PNG images are allowed for a profile picture.');
 if(file.size>PROFILE_PIC_MAX_BYTES)return Promise.resolve('Image is too large - the maximum is 5 MB.');
 return file.slice(0,8).arrayBuffer().then(buf=>{
  const b=new Uint8Array(buf);
  const isPng=b.length>=4&&b[0]===0x89&&b[1]===0x50&&b[2]===0x4E&&b[3]===0x47;
  const isJpg=b.length>=3&&b[0]===0xFF&&b[1]===0xD8&&b[2]===0xFF;
  const wantsPng=/\.png$/i.test(file.name);
  return (wantsPng?isPng:isJpg)?null:'That file does not look like a real JPG or PNG image.';
 }).catch(()=>'Could not read that file. Please choose another image.');
}

// Live preview when choosing a profile picture on My Record & Profile
const profilePicInput=document.getElementById('profilePictureInput');
if(profilePicInput){
 profilePicInput.addEventListener('change',()=>{
  const file=profilePicInput.files&&profilePicInput.files[0];
  if(!file)return;
  profilePicError(file).then(error=>{
   if(error){
    profilePicInput.value='';
    showToast(error,'error');
    return;
   }
   const img=document.getElementById('profilePicPreviewImg');
   const initials=document.getElementById('profilePicInitials');
   if(img){img.src=URL.createObjectURL(file);img.classList.remove('hidden');}
   if(initials)initials.classList.add('hidden');
  });
 });
}
