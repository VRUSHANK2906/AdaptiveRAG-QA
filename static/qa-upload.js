const modal=document.querySelector('#uploadModal');
const uploadForm=document.querySelector('#uploadForm');
const filesInput=document.querySelector('#documentFiles');
const drop=document.querySelector('#uploadDropzone');
const preview=document.querySelector('#uploadPreview');
const uploadError=document.querySelector('#uploadError');
const submit=document.querySelector('#uploadSubmit');
let selectedFiles=[];
const getChatId=()=>sessionStorage.getItem('deepReasonerChatId') || createChatId();
const createChatId=()=>{
  const chatId=`chat-${crypto.randomUUID()}`;
  sessionStorage.setItem('deepReasonerChatId',chatId);
  return chatId;
};

const openUpload=()=>{
  modal.hidden=false;
  document.body.style.overflow='hidden';
  setTimeout(()=>filesInput.click(),20);
};

const closeUpload=()=>{
  modal.hidden=true;
  document.body.style.overflow='';
  selectedFiles=[];
  preview.hidden=true;
  preview.innerHTML='';
  uploadError.textContent='';
  uploadForm.reset();
};

const escapeHtml=value=>value.replace(/[&<>'"]/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]));
const formatBytes=bytes=>bytes<1048576?`${Math.max(1,Math.round(bytes/1024))} KB`:`${(bytes/1048576).toFixed(1)} MB`;

const setFiles=list=>{
  const pdfs=[...list].filter(file=>file.type==='application/pdf'||file.name.toLowerCase().endsWith('.pdf'));
  if(!pdfs.length){uploadError.textContent='Choose PDF files only.';return;}
  selectedFiles=pdfs;
  preview.hidden=false;
  preview.innerHTML='';
  pdfs.forEach(file=>{
    const row=document.createElement('div');
    row.className='preview-item';
    row.innerHTML=`<strong>${escapeHtml(file.name)}</strong><span>${formatBytes(file.size)}</span>`;
    preview.appendChild(row);
  });
  uploadError.textContent='';
};

const refreshLibrary=async()=>{
  const response=await fetch('/api/documents',{credentials:'same-origin'});
  const data=await response.json();
  if(!response.ok)throw new Error(data.error);
  const count=data.documents.length;
  document.querySelector('#librarySummary strong').textContent=count?`${count} indexed document${count===1?'':'s'}`:'No documents indexed';
  document.querySelector('#librarySummary small').textContent=count?'Your library is ready for questions.':'Upload documents to begin.';
};

document.querySelector('#uploadTrigger')?.addEventListener('click',openUpload);
document.querySelector('#uploadFromSidebar')?.addEventListener('click',openUpload);
document.querySelectorAll('[data-close-modal]').forEach(button=>button.addEventListener('click',closeUpload));
modal?.addEventListener('click',event=>{if(event.target===modal)closeUpload();});
drop?.addEventListener('dragover',event=>{event.preventDefault();drop.classList.add('drag');});
drop?.addEventListener('dragleave',()=>drop.classList.remove('drag'));
drop?.addEventListener('drop',event=>{event.preventDefault();drop.classList.remove('drag');setFiles(event.dataTransfer.files);});
filesInput?.addEventListener('change',()=>setFiles(filesInput.files));

uploadForm?.addEventListener('submit',async event=>{
  event.preventDefault();
  if(!selectedFiles.length){uploadError.textContent='Select at least one PDF.';return;}
  const data=new FormData();
  data.append('chat_id',getChatId());
  selectedFiles.forEach(file=>data.append('files',file));
  submit.disabled=true;
  submit.innerHTML='<span class="loading-spinner"></span><span>Indexing…</span>';
  uploadError.textContent='';
  try{
    const response=await fetch('/upload',{method:'POST',body:data,credentials:'same-origin'});
    const result=await response.json();
    if(!response.ok)throw new Error(result.error);
    showToast(`${result.message} ${result.files} files indexed.`,'Documents uploaded');
    closeUpload();
    await refreshLibrary();
  }catch(event){
    error.textContent=event.message;
  }finally{
    submit.disabled=false;
    submit.innerHTML='Index documents';
  }
});

refreshLibrary().catch(()=>{
  document.querySelector('#librarySummary strong').textContent='Library unavailable';
  document.querySelector('#librarySummary small').textContent='Refresh after the service is available.';
});
