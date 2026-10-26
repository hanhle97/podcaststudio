// Podcast Studio web UI: plain JS, talks to the media-service API on the same origin.

const $ = (sel, root = document) => root.querySelector(sel);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmtTime = (sec) => {
  if (sec == null) return '';
  const s = Math.round(sec), m = Math.floor(s / 60);
  return `${m}:${String(s % 60).padStart(2, '0')}`;
};
const fmtDate = (iso) => new Date(iso).toLocaleString('vi-VN',
  { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });

const STEP_ENDPOINT = {
  tts: 'tts', mix: 'mix', align: 'align', subtitles: 'subtitles', metadata: 'metadata',
  clips: 'clips/select', thumbnail: 'thumbnail', render: 'render?format=all',
};
const HEALTH_LABELS = { voice_studio: 'Voice Studio', claude: 'Claude', n8n: 'n8n', tiktok: 'TikTok', facebook: 'Facebook' };

async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  let data = null;
  try { data = await res.json(); } catch { /* empty body */ }
  if (!res.ok) {
    const err = new Error((data && (typeof data.detail === 'string' ? data.detail : null)) ||
      `HTTP ${res.status}`);
    err.status = res.status; err.data = data;
    throw err;
  }
  return data;
}

function toast(msg, ms = 3500) {
  const el = document.createElement('div');
  el.className = 'toast'; el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), ms);
}

async function copy(text, btn) {
  try { await navigator.clipboard.writeText(text); } catch {
    const ta = document.createElement('textarea'); ta.value = text;
    document.body.appendChild(ta); ta.select(); document.execCommand('copy'); ta.remove();
  }
  if (btn) { const old = btn.textContent; btn.textContent = 'Đã copy'; setTimeout(() => (btn.textContent = old), 1200); }
}

// --- health ---------------------------------------------------------------------------

async function loadHealth() {
  const box = $('#health');
  try {
    const s = await api('/ui/status');
    box.innerHTML = Object.entries(HEALTH_LABELS).map(([k, label]) => {
      const v = s[k] || {};
      const cls = v.ok ? 'ok' : (v.optional ? 'optional' : 'bad');
      return `<span class="chip ${cls}" title="${esc(v.message)}">${label}</span>`;
    }).join('');
  } catch {
    box.innerHTML = '<span class="chip bad">Media service không phản hồi</span>';
  }
}

// --- episode list ---------------------------------------------------------------------

let episodes = [];

async function loadEpisodes() {
  try { episodes = await api('/episodes/overview'); } catch { return; }
  const current = currentEpisodeId();
  const list = $('#episode-list');
  if (!episodes.length) {
    list.innerHTML = '<li class="empty">Chưa có tập nào. Bấm <b>+ Tập mới</b> để bắt đầu.</li>';
    return;
  }
  list.innerHTML = episodes.map((e) => `
    <li><a href="#/ep/${e.id}" class="${e.id === current ? 'active' : ''}">
      ${e.thumbnail ? `<img class="thumb" src="${e.thumbnail}?v=${Date.parse(e.created_at)}" alt="">` : '<div class="thumb"></div>'}
      <div>
        <div class="ep-title">${esc(e.title)}</div>
        <span class="badge ${e.status.state}">${esc(e.status.label)}${e.status.state === 'processing'
          ? ` ${Math.round((e.status.progress || 0) * 100)}%` : ''}</span>
        <div class="muted small">${fmtDate(e.created_at)}${e.duration ? ' · ' + fmtTime(e.duration) : ''}</div>
      </div></a></li>`).join('');
}

// --- new episode ------------------------------------------------------------------------

function renderNew() {
  const view = $('#view');
  view.innerHTML = '';
  view.appendChild($('#tpl-new').content.cloneNode(true));
  const ta = $('#script'), drop = $('#drop'), sendBtn = $('#send-btn');
  const fname = $('#filename');
  ta.value = sessionStorage.getItem('draft') || '';
  ta.addEventListener('input', () => {
    sessionStorage.setItem('draft', ta.value); sendBtn.disabled = true;
  });
  ['dragenter', 'dragover'].forEach((ev) => drop.addEventListener(ev, (e) => {
    e.preventDefault(); drop.classList.add('dragging');
  }));
  ['dragleave', 'drop'].forEach((ev) => drop.addEventListener(ev, () => drop.classList.remove('dragging')));
  drop.addEventListener('drop', async (e) => {
    e.preventDefault();
    const file = e.dataTransfer.files[0];
    if (!file) return;
    ta.value = await file.text(); fname.value = file.name;
    sessionStorage.setItem('draft', ta.value); sendBtn.disabled = true;
    check();
  });
  $('#check-btn').addEventListener('click', check);
  sendBtn.addEventListener('click', send);
}

function lineJump(line) {
  const ta = $('#script');
  const lines = ta.value.split('\n');
  const start = lines.slice(0, line - 1).join('\n').length + (line > 1 ? 1 : 0);
  ta.focus(); ta.setSelectionRange(start, start + (lines[line - 1] || '').length);
  ta.scrollTop = Math.max(0, (line - 5) * 22);
}

function issuesHtml(items) {
  return `<ul class="issues">${items.map((i) => `<li>
    ${i.line ? `<span class="line" data-line="${i.line}">Dòng ${i.line}${i.column ? ', cột ' + i.column : ''}</span>: ` : ''}
    ${esc(i.message)}${i.hint ? `<div class="hint">Gợi ý: ${esc(i.hint)}</div>` : ''}</li>`).join('')}</ul>`;
}

async function check() {
  const out = $('#check-result'), text = $('#script').value, sendBtn = $('#send-btn');
  sendBtn.disabled = true;
  if (!text.trim()) { out.innerHTML = '<div class="alert warn">Kịch bản đang trống.</div>'; return false; }
  try {
    const r = await api('/scripts/validate', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text }),
    });
    out.innerHTML = `<div class="alert ok"><b>Kịch bản hợp lệ.</b>
      ${r.sentences} câu · ${r.chapters.length} chương · ước tính ${fmtTime(r.estimated_seconds)} lời đọc
      ${r.title ? `<br>Tiêu đề: ${esc(r.title)}` : '<br>Chưa có tiêu đề: Claude sẽ tự đặt.'}
      ${r.has_headings ? `<br>Chương: ${r.chapters.map(esc).join(' · ')}` : '<br>Chưa có heading <code>##</code>: Claude sẽ tự chia chương.'}</div>
      ${r.warnings.length ? `<div class="alert warn"><b>Lưu ý</b>${issuesHtml(r.warnings)}</div>` : ''}`;
    sendBtn.disabled = false;
    return true;
  } catch (e) {
    const errs = e.data && e.data.errors;
    out.innerHTML = `<div class="alert err"><b>Kịch bản có lỗi, sửa rồi kiểm tra lại.</b>
      ${errs ? issuesHtml(errs) : `<div>${esc(e.message)}</div>`}</div>`;
    return false;
  } finally {
    out.querySelectorAll('.line').forEach((el) => el.addEventListener('click', () => lineJump(+el.dataset.line)));
  }
}

async function send() {
  if (!(await check())) return;
  const form = new FormData();
  form.append('text', $('#script').value);
  form.append('filename', $('#filename').value || 'kich-ban.md');
  try {
    const r = await api('/inbox/upload', { method: 'POST', body: form });
    sessionStorage.removeItem('draft');
    $('#check-result').innerHTML = `<div class="alert info"><b>Đã gửi vào hàng chờ</b> (<code>${esc(r.inbox_file)}</code>).
      Tập mới sẽ hiện ở cột bên trái sau vài giây. Khi xong, bản nháp đến Telegram và nút
      <b>Duyệt</b> cũng hiện trong trang của tập.</div>`;
    $('#send-btn').disabled = true;
    setTimeout(loadEpisodes, 4000);
  } catch (e) {
    toast('Gửi thất bại: ' + e.message, 6000);
  }
}

// --- episode detail -----------------------------------------------------------------------

function currentEpisodeId() {
  const m = location.hash.match(/^#\/ep\/([\w-]+)/);
  return m ? m[1] : null;
}

let detailTimer = null;

async function renderEpisode(id, quiet = false) {
  const view = $('#view');
  let d;
  try { d = await api(`/episodes/${id}/details`); } catch (e) {
    view.innerHTML = `<div class="alert err">${esc(e.message)}</div>`; return;
  }
  if (currentEpisodeId() !== id) return;
  const s = d.status, sum = d.summary, meta = d.metadata || {}, job = d.jobs[0];
  const base = `/episodes/${id}/files`;
  const v = Date.now();
  const published = d.published && d.published.state === 'published' ? d.published : null;
  const shortsHtml = (sum.shorts || []).map((c, i) => {
    const sid = published && published.short_ids && published.short_ids[i];
    return `<div class="short"><video src="${base}/shorts/${c.name}#t=1.5" controls preload="metadata"></video>
      <div class="cap"><b>${esc(c.title)}</b>${sid ? ` · <a href="https://youtube.com/shorts/${sid}" target="_blank">YouTube</a>` : ''}</div></div>`;
  }).join('');
  const tiktok = d.tiktok ? Object.values(d.tiktok) : [];
  const ttHtml = tiktok.length ? tiktok.map((t) => `<div><b>${esc(t.name)}</b>
      <span class="muted small">${t.status === 'SEND_TO_USER_INBOX' ? 'trong hộp nháp TikTok' : esc(t.status)}</span>
      <div class="copy-box">${esc(t.caption)}<button class="btn small" data-copy="${esc(t.caption)}">Copy</button></div></div>`).join('')
    : '<p class="muted small">Chưa gửi lên TikTok.</p>';
  const fb = d.facebook || {};
  const fbPosts = [...(fb.video ? [{ label: 'Video tập', ...fb.video }] : []),
    ...(fb.reels || []).map((r) => ({ label: `Reel: ${r.title || r.name}`, ...r }))];
  const fbHtml = fbPosts.length ? fbPosts.map((p) => `<div><b>${esc(p.label)}</b>
      <span class="muted small">${p.status === 'published' ? '' : esc(p.status)}</span>
      <a href="${esc(p.url)}" target="_blank">mở</a></div>`).join('')
    : '<p class="muted small">Chưa đăng lên Facebook.</p>';

  const keepScroll = quiet ? window.scrollY : 0;
  view.innerHTML = `<div class="panel">
    <div class="ep-head">
      <div><h1>${esc(d.title)}</h1>
        <span class="badge ${s.state}">${esc(s.label)}</span>
        <span class="muted small">${fmtDate(d.created_at)}${d.duration ? ' · ' + fmtTime(d.duration) : ''}</span>
        ${s.detail ? `<div class="muted small">${esc(s.detail)}</div>` : ''}</div>
      <div class="ep-actions">
        ${s.state === 'waiting' ? `<button class="btn ok" data-act="approve">✅ Duyệt</button>
          <button class="btn danger" data-act="reject">❌ Từ chối</button>` : ''}
        <button class="btn" data-act="pipeline" ${s.state === 'processing' ? 'disabled' : ''}>Chạy pipeline</button>
        <select id="step-select" ${s.state === 'processing' ? 'disabled' : ''}>
          <option value="">Chạy lại bước…</option>
          ${d.steps.filter((x) => STEP_ENDPOINT[x.name]).map((x) => `<option value="${x.name}">${esc(x.label)}</option>`).join('')}
        </select>
        <button class="btn" data-act="tiktok" ${s.state === 'processing' ? 'disabled' : ''}>Gửi TikTok</button>
        <button class="btn" data-act="facebook" ${s.state === 'processing' ? 'disabled' : ''}>Đăng Facebook</button>
      </div>
    </div>

    ${s.state === 'waiting' ? `<div class="alert warn">Bản nháp đang chờ duyệt. Duyệt ở đây hoặc trên Telegram đều được:
      video chính + Shorts lên YouTube (riêng tư), Short gửi vào hộp nháp TikTok, video + Reels lên fanpage Facebook.</div>` : ''}
    ${published ? `<div class="alert ok"><b>Đã đăng.</b> YouTube: <a href="https://youtu.be/${esc(published.youtube_id)}" target="_blank">youtu.be/${esc(published.youtube_id)}</a>
      ${published.short_ids && published.short_ids.length ? ` · ${published.short_ids.length} Shorts` : ''}
      ${published.tiktok ? `<br>${esc(published.tiktok)}` : ''}
      ${published.facebook ? `<br>${esc(published.facebook)}` : ''}</div>` : ''}

    ${job && (job.status === 'running' || job.status === 'queued' || job.status === 'failed') ? `<div class="card">
      <h3>Tiến độ · ${esc(job.kind)}</h3>
      <div class="row"><div class="progress" style="flex:1"><div style="width:${Math.round(job.progress * 100)}%"></div></div>
        <b>${Math.round(job.progress * 100)}%</b></div>
      <div class="muted small">${esc(job.step_label || '')} ${esc(job.message || '')}</div>
      ${job.error ? `<div class="alert err"><b>Lỗi ở bước ${esc(job.step_label || job.error.step)}:</b> ${esc(job.error.message)}</div>` : ''}
      <details><summary class="muted small">Nhật ký</summary><div class="log">${esc((job.log || []).slice(-40).join('\n'))}</div></details>
    </div>` : ''}

    <div class="grid2">
      <div>
        ${sum.missing && sum.missing.includes('episode_16x9.mp4') ? '<div class="card muted">Video chưa render.</div>'
          : `<div class="card"><video src="${base}/episode_16x9.mp4?v=${v}#t=2" controls preload="metadata"></video></div>`}
        ${shortsHtml ? `<div class="card"><h3>Shorts</h3><div class="shorts">${shortsHtml}</div></div>` : ''}
        <div class="card"><h3>TikTok</h3>${ttHtml}</div>
        <div class="card"><h3>Facebook${fb.page ? ' · ' + esc(fb.page) : ''}</h3>${fbHtml}</div>
      </div>
      <div>
        ${sum.missing && sum.missing.includes('thumbnail.png') ? '' : `<div class="card"><img src="${base}/thumbnail.png?v=${v}" alt="thumbnail" style="width:100%;border-radius:8px"></div>`}
        <div class="card"><h3>YouTube</h3>
          ${meta.title ? `<dl class="kv"><dt>Tiêu đề</dt><dd>${esc(meta.title)}</dd></dl>
          <h3 style="margin-top:12px">Mô tả</h3>
          <div class="copy-box">${esc(meta.description)}<button class="btn small" data-copy="${esc(meta.description)}">Copy</button></div>
          <div class="tags">${(meta.tags || []).map((t) => `<span class="tag">${esc(t)}</span>`).join('')}</div>
          <h3 style="margin-top:12px">Text thumbnail</h3>
          <ol class="small">${(meta.thumbnail_texts || []).map((t) => `<li>${esc(t)}</li>`).join('')}</ol>`
          : '<p class="muted small">Chưa có metadata.</p>'}
        </div>
        <div class="card"><h3>File</h3><ul class="files">
          ${d.files.filter((f) => !f.path.startsWith('tts/') && !f.path.startsWith('render/') && !f.path.startsWith('e2e/'))
            .map((f) => `<li><a href="${base}/${f.path}" target="_blank">${esc(f.path)}</a></li>`).join('')}
        </ul></div>
      </div>
    </div>
  </div>`;
  if (quiet) window.scrollTo(0, keepScroll);

  view.querySelectorAll('[data-copy]').forEach((b) => b.addEventListener('click', () => copy(b.dataset.copy, b)));
  view.querySelectorAll('[data-act]').forEach((b) => b.addEventListener('click', () => act(id, b.dataset.act, b)));
  $('#step-select').addEventListener('change', (e) => { if (e.target.value) act(id, 'step:' + e.target.value, e.target); });

  clearTimeout(detailTimer);
  if (['processing', 'waiting'].includes(s.state)) {
    detailTimer = setTimeout(() => currentEpisodeId() === id && renderEpisode(id, true), 4000);
  }
}

async function act(id, action, el) {
  const confirmText = {
    approve: 'Duyệt và đăng: video chính + Shorts lên YouTube (riêng tư), Short vào hộp nháp TikTok, video + Reels công khai lên fanpage Facebook?',
    reject: 'Từ chối bản nháp này? Sẽ không đăng gì.',
    pipeline: 'Chạy pipeline cho tập này? Các bước đã có kết quả hợp lệ sẽ được bỏ qua.',
    tiktok: 'Gửi các Short vào hộp nháp TikTok? Clip nào đã gửi sẽ không gửi lại.',
    facebook: 'Đăng CÔNG KHAI video tập + Reels lên fanpage Facebook? Phần nào đã đăng sẽ không đăng lại.',
  }[action] || 'Chạy lại bước này? Kết quả cũ của bước sẽ bị thay.';
  if (!confirm(confirmText)) { if (el.tagName === 'SELECT') el.value = ''; return; }
  el.disabled = true;
  try {
    if (action === 'approve' || action === 'reject') {
      await api(`/episodes/${id}/approval/decide`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ decision: action }),
      });
      toast(action === 'approve' ? 'Đã duyệt, đang đăng…' : 'Đã từ chối.');
    } else if (action === 'pipeline') {
      await api(`/episodes/${id}/pipeline/run`, { method: 'POST' }); toast('Đã bắt đầu pipeline.');
    } else if (action === 'tiktok') {
      await api(`/episodes/${id}/publish/tiktok`, { method: 'POST' }); toast('Đang gửi lên TikTok…');
    } else if (action === 'facebook') {
      await api(`/episodes/${id}/publish/facebook`, { method: 'POST' }); toast('Đang đăng lên Facebook…');
    } else if (action.startsWith('step:')) {
      const step = action.slice(5);
      await api(`/episodes/${id}/${STEP_ENDPOINT[step]}`, { method: 'POST' }); toast('Đã bắt đầu chạy lại bước.');
    }
  } catch (e) {
    toast(e.message, 7000);
  }
  setTimeout(() => { renderEpisode(id, true); loadEpisodes(); }, 800);
}

// --- router -------------------------------------------------------------------------------

function route() {
  clearTimeout(detailTimer);
  const id = currentEpisodeId();
  if (id) renderEpisode(id);
  else if (location.hash === '#/new' || !episodes.length) renderNew();
  else location.hash = `#/ep/${episodes[0].id}`;
  loadEpisodes();
}

$('#new-btn').addEventListener('click', () => { location.hash = '#/new'; });
$('#refresh-btn').addEventListener('click', () => { loadEpisodes(); loadHealth(); });
window.addEventListener('hashchange', route);

(async () => {
  await loadEpisodes();
  route();
  loadHealth();
  setInterval(loadHealth, 30000);
  setInterval(() => {
    if (episodes.some((e) => ['processing', 'waiting'].includes(e.status.state))) loadEpisodes();
  }, 5000);
})();
