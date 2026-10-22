const modal = document.querySelector('#uploadModal');
const form = document.querySelector('#uploadForm');
const filesInput = document.querySelector('#documentFiles');
const drop = document.querySelector('#uploadDropzone');
const preview = document.querySelector('#uploadPreview');
const error = document.querySelector('#uploadError');
const submit = document.querySelector('#uploadSubmit');
let selectedFiles = [];

const open = () => {
  modal.hidden = false;
  document.body.style.overflow = 'hidden';
  setTimeout(() => filesInput.click(), 20);
};
const close = () => {
  modal.hidden = true;
  document.body.style.overflow = '';
  selectedFiles = [];
  preview.hidden = true;
  preview.innerHTML = '';
  error.textContent = '';
  form.reset();
};
const escapeHtml = value => value.replace(/[&<>'"]/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]));
const formatBytes = bytes => bytes < 1048576 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / 1048576).toFixed(1)} MB`;
const setFiles = list => {
  const pdfs = [...list].filter(file => file.type === 'application/pdf' || file.name.toLowerCase().endsWith('.pdf'));
  if (!pdfs.length) {
    error.textContent = 'Choose PDF files only.';
    return;
  }
  selectedFiles = pdfs;
  preview.hidden = false;
  preview.innerHTML = '';
  pdfs.forEach(file => {
    const row = document.createElement('div');
    row.className = 'preview-item';
    row.innerHTML = `<strong>${escapeHtml(file.name)}</strong><span>${formatBytes(file.size)}</span>`;
    preview.appendChild(row);
  });
  error.textContent = '';
};

const loadDocuments = async () => {
  const response = await fetch('/api/documents', { credentials: 'same-origin' });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error);
  const docs = data.documents;
  const grid = document.querySelector('#documentGrid');
  document.querySelector('#documentCount').textContent = `${docs.length} document${docs.length === 1 ? '' : 's'}`;
  grid.innerHTML = docs.length ? docs.map(doc => `
    <article class="document-card">
      <div class="doc-top"><span class="doc-file">PDF</span><div><h3 title="${escapeHtml(doc.original_name)}">${escapeHtml(doc.original_name)}</h3><p>${formatBytes(doc.size)} · ${doc.chunk_count} chunks</p></div></div>
      <div class="doc-card-footer"><span>${new Date(doc.uploaded_at).toLocaleDateString()}</span><button class="delete-button" data-id="${doc.id}" aria-label="Delete ${escapeHtml(doc.original_name)}">Delete</button></div>
    </article>`).join('') : '<div class="empty-state"><span>?</span><h3>No documents yet</h3><p>Upload a PDF to build your private knowledge base.</p><button class="button secondary" type="button">Upload your first PDF</button></div>';
  grid.querySelectorAll('.delete-button').forEach(button => button.addEventListener('click', async () => {
    if (!confirm('Delete this document and its indexed data?')) return;
    try {
      await fetch(`/api/documents/${button.dataset.id}`, { method: 'DELETE', credentials: 'same-origin' });
      showToast('Document deleted.', 'Deleted');
      await loadDocuments();
    } catch (event) {
      showToast(event.message, 'Error', 'error');
    }
  }));
};

document.querySelector('#uploadTrigger')?.addEventListener('click', open);
document.querySelector('#emptyUpload')?.addEventListener('click', open);
document.querySelectorAll('[data-close-modal]').forEach(button => button.addEventListener('click', close));
modal?.addEventListener('click', event => { if (event.target === modal) close(); });
drop?.addEventListener('dragover', event => { event.preventDefault(); drop.classList.add('drag'); });
drop?.addEventListener('dragleave', () => drop.classList.remove('drag'));
drop?.addEventListener('drop', event => { event.preventDefault(); drop.classList.remove('drag'); setFiles(event.dataTransfer.files); });
filesInput?.addEventListener('change', () => setFiles(filesInput.files));
form?.addEventListener('submit', async event => {
  event.preventDefault();
  if (!selectedFiles.length) {
    error.textContent = 'Select at least one PDF.';
    return;
  }
  const data = new FormData();
  selectedFiles.forEach(file => data.append('files', file));
  submit.disabled = true;
  submit.innerHTML = '<span class="loading-spinner"></span><span>Indexing…</span>';
  error.textContent = '';
  try {
    const response = await fetch('/upload', { method: 'POST', body: data, credentials: 'same-origin' });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error);
    showToast(`${result.message} · ${result.files} files indexed.`, 'Documents uploaded');
    close();
    await loadDocuments();
  } catch (event) {
    error.textContent = event.message;
  } finally {
    submit.disabled = false;
    submit.innerHTML = 'Index documents';
  }
});
document.querySelector('#documentSearch')?.addEventListener('input', event => {
  const query = event.target.value.toLowerCase();
  document.querySelectorAll('.document-card').forEach(card => card.hidden = !card.textContent.toLowerCase().includes(query));
});
loadDocuments().catch(() => showToast('Document list is unavailable.', 'Error', 'error'));
