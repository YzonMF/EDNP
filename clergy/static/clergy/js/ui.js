/* Pure UI helper shared by every clergy page: the toast notification, used
   to surface messages Django sends back after a form submit (e.g. "Profile
   updated successfully"). No application data lives here — the profile and
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
