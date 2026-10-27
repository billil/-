/* =========================================================================
 * util.js —— 通用工具：转义、时间、提示、对话框、本地存储
 * ========================================================================= */

/** HTML 转义。所有来自后端的文字（群名、消息原文、模型输出）都必须经过它 */
export function esc(value) {
  if (value === null || value === undefined) return '';
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/** 把 plain text 渲染成保留换行的 HTML（先转义） */
export function nl2br(text) {
  return esc(text).replace(/\r?\n/g, '<br>');
}

/** 在原文里高亮一个精确片段（两边都先转义，仅用于展示）。
 *  用「先找原文位置、再分别转义」的方式，避免转义后长度变化导致标错位置。 */
export function highlight(text, needle) {
  const src = String(text ?? '');
  if (!needle) return esc(src);
  const at = src.indexOf(needle);
  if (at < 0) return esc(src);
  return (
    esc(src.slice(0, at)) +
    '<mark>' + esc(needle) + '</mark>' +
    esc(src.slice(at + needle.length))
  );
}

/** 高亮所有出现位置（用于引用片段可能重复的场合） */
export function highlightAll(text, needle) {
  const src = String(text ?? '');
  if (!needle || src.indexOf(needle) < 0) return esc(src);
  return src
    .split(needle)
    .map(esc)
    .join('<mark>' + esc(needle) + '</mark>');
}

const pad = (n) => String(n).padStart(2, '0');

/** ISO（后端统一 UTC）-> 本地 "2026-11-01 09:00" */
export function fmtTime(iso, withSeconds = false) {
  if (!iso) return '未提供时间';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  const base = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  return withSeconds ? `${base}:${pad(d.getSeconds())}` : base;
}

export function fmtDay(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/** 相对时间：刚刚 / 5 分钟前 / 3 小时前 / 2 天前 */
export function relTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  const diff = Date.now() - d.getTime();
  if (diff < 0) return '未来 ' + Math.round(-diff / 86400000) + ' 天';
  if (diff < 60000) return '刚刚';
  if (diff < 3600000) return Math.floor(diff / 60000) + ' 分钟前';
  if (diff < 86400000) return Math.floor(diff / 3600000) + ' 小时前';
  if (diff < 2592000000) return Math.floor(diff / 86400000) + ' 天前';
  return fmtDay(iso);
}

/** ISO -> <input type="datetime-local"> 的值（本地时区） */
export function toLocalInput(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function truncate(text, n = 60) {
  const s = String(text || '').replace(/\s+/g, ' ').trim();
  return s.length > n ? s.slice(0, n) + '…' : s;
}

export function debounce(fn, wait = 250) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
}

let seq = 0;
export function uid(prefix = 'ui') {
  seq += 1;
  return `${prefix}_${Date.now().toString(36)}${seq.toString(36)}`;
}

/* ---- Toast ------------------------------------------------------------- */
export function toast(message, type = 'info', ttl = 3600) {
  const host = document.getElementById('toast-host');
  if (!host) return;
  const node = document.createElement('div');
  node.className = `toast toast--${type}`;
  node.innerHTML = `<span class="toast__dot"></span><div class="toast__body">${esc(message)}</div>`;
  host.appendChild(node);
  requestAnimationFrame(() => node.classList.add('is-in'));
  setTimeout(() => {
    node.classList.remove('is-in');
    setTimeout(() => node.remove(), 200);
  }, ttl);
  return node;
}

/* ---- 对话框 ------------------------------------------------------------ */
/**
 * 通用对话框。
 * @returns {{close:Function, root:HTMLElement, onClose:Function}}
 */
export function dialog({ title, body, footer, width = 560, onMount }) {
  const host = document.getElementById('modal-host');
  const wrap = document.createElement('div');
  wrap.className = 'modal';
  wrap.innerHTML = `
    <div class="modal__mask" data-close="1"></div>
    <div class="modal__panel" style="max-width:${width}px" role="dialog" aria-modal="true">
      <header class="modal__head">
        <h2>${esc(title)}</h2>
        <button class="icon-btn" data-close="1" title="关闭" aria-label="关闭">✕</button>
      </header>
      <div class="modal__body">${body || ''}</div>
      <footer class="modal__foot">${footer || ''}</footer>
    </div>`;
  host.appendChild(wrap);
  requestAnimationFrame(() => wrap.classList.add('is-open'));

  const close = () => {
    wrap.classList.remove('is-open');
    setTimeout(() => wrap.remove(), 160);
    if (typeof wrap._onClose === 'function') wrap._onClose();
  };
  const onClose = (fn) => { wrap._onClose = fn; };
  wrap.addEventListener('click', (e) => {
    if (e.target.closest('[data-close]')) close();
  });
  document.addEventListener('keydown', function onKey(e) {
    if (e.key === 'Escape') { close(); document.removeEventListener('keydown', onKey); }
  });

  if (typeof onMount === 'function') onMount(wrap.querySelector('.modal__panel'), close);
  return { root: wrap, close, onClose, panel: wrap.querySelector('.modal__panel') };
}

/** 二次确认 */
export function confirmDialog({ title, message, confirmText = '确定', danger = false }) {
  return new Promise((resolve) => {
    let decided = false;
    const dlg = dialog({
      title,
      width: 440,
      body: `<p class="confirm-text">${message}</p>`,
      footer: `<button class="btn btn--ghost" data-act="cancel">取消</button>
               <button class="btn ${danger ? 'btn--danger' : 'btn--primary'}" data-act="ok">${esc(confirmText)}</button>`,
      onMount(panel, close) {
        panel.querySelector('[data-act="cancel"]').onclick = () => close();
        panel.querySelector('[data-act="ok"]').onclick = () => { decided = true; close(); resolve(true); };
      },
    });
    dlg.onClose(() => { if (!decided) resolve(false); });
  });
}

/* ---- 小工具 ------------------------------------------------------------ */
export function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) {
    return navigator.clipboard.writeText(text);
  }
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.style.position = 'fixed';
  ta.style.opacity = '0';
  document.body.appendChild(ta);
  ta.select();
  try { document.execCommand('copy'); } finally { ta.remove(); }
  return Promise.resolve();
}

export function downloadJson(filename, data) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** 状态中文名 */
export const STATUS_TEXT = {
  answer: '已找到答案',
  clarify: '需要补充条件',
  not_found: '范围内无匹配',
  uncertain: '证据不足或冲突',
  error: '调用故障',
};

export const STATUS_TONE = {
  answer: 'ok',
  clarify: 'info',
  not_found: 'muted',
  uncertain: 'warn',
  error: 'danger',
};
