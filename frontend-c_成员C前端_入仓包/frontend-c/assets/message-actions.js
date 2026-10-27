/* =========================================================================
 * message-actions.js —— 导入 / 编辑 / 删除 的共用交互
 * 被「消息库」和「原文详情」两个页面复用
 *
 * 与后端的约定（摘自 docs/API.md 与 qunyi/service.py）：
 *   POST  /v1/imports        单次最多 200 条、整包 <= 1 MiB；逐条校验，部分失败仍返回 200
 *   PATCH /v1/messages/{id}  只接受 content / group_label / sent_at / metadata 的子集，
 *                            metadata 是整体替换；成功后该空间全部会话失效
 *   DELETE /v1/messages/{id} 删除原文与索引，并清理该空间全部派生会话
 * ========================================================================= */

import { buildMessage, checkFields } from './api.js';
import { dialog, esc, toast, confirmDialog, toLocalInput, fmtTime } from './util.js';

const KIND_LABEL = {
  initial: '首发通知',
  correction: '更正',
  cancellation: '取消',
};

/* ------------------------------------------------------------------ 字段行 */
function fieldRow(name = '', value = '') {
  return `<div class="field-row" data-field-row style="display:grid;grid-template-columns:92px minmax(0,1fr) 30px;gap:6px;margin-bottom:6px">
    <input class="input" data-f-name placeholder="字段名" value="${esc(name)}" />
    <input class="input" data-f-value placeholder="字段值（必须逐字出现在原文中）" value="${esc(value)}" />
    <button type="button" class="icon-btn" data-f-remove title="删除该字段">✕</button>
  </div>`;
}

function readFields(scope) {
  const out = {};
  scope.querySelectorAll('[data-field-row]').forEach((row) => {
    const n = row.querySelector('[data-f-name]').value.trim();
    const v = row.querySelector('[data-f-value]').value.trim();
    if (n && v) out[n] = v;
  });
  return out;
}

function wireFieldRows(scope, container) {
  container.addEventListener('click', (e) => {
    if (e.target.closest('[data-f-remove]')) {
      e.target.closest('[data-field-row]').remove();
    }
  });
  const addBtn = scope.querySelector('[data-f-add]');
  if (addBtn) addBtn.onclick = () => container.insertAdjacentHTML('beforeend', fieldRow());
}

/** 字段值未逐字出现时，就地标红（后端会直接判 INVALID_INPUT） */
function liveCheckFields(scope, contentEl, container) {
  const warn = scope.querySelector('[data-field-warn]');
  const run = () => {
    const bad = checkFields(contentEl.value, readFields(container));
    if (!bad.length) {
      warn.innerHTML = '';
      warn.hidden = true;
      return;
    }
    warn.hidden = false;
    warn.className = 'notice notice--warn';
    warn.innerHTML = `<span class="notice__icon">!</span><div>以下字段值没有逐字出现在原文里，后端会判为 <code>INVALID_INPUT</code>：
      <div style="margin-top:4px">${bad.map((b) => `<span class="chip" style="margin:2px 4px 0 0">${esc(b.name)}：${esc(b.value)}</span>`).join('')}</div></div>`;
  };
  contentEl.addEventListener('input', run);
  container.addEventListener('input', run);
  run();
}

/* ------------------------------------------------------------- 导入对话框 */
export function openImportDialog({ api, onDone }) {
  const idem = 'ui-' + Date.now().toString(36);
  const dlg = dialog({
    title: '导入消息',
    width: 720,
    body: `
      <div class="stack">
        <div class="notice notice--muted">
          <span class="notice__icon">i</span>
          <div>仅导入你有权限的文本。本应用不会读取任何聊天软件，所有内容都由你手动粘贴。
          带「事件标注」的消息才能被结构化检索；未标注的只做原文检索。</div>
        </div>

        <div class="row" style="gap:16px">
          <label class="checkbox"><input type="radio" name="imp-mode" value="single" checked />单条</label>
          <label class="checkbox"><input type="radio" name="imp-mode" value="batch" />批量（每行一条）</label>
          <span class="spacer"></span>
          <span class="small muted">单次上限 200 条 / 1 MiB</span>
        </div>

        <div class="grid grid--2">
          <div class="field">
            <label>群标签（<=100 字，可留空）</label>
            <input class="input" data-group placeholder="如：2024级计科1班群" />
          </div>
          <div class="field">
            <label>发送时间（可留空）</label>
            <input class="input" type="datetime-local" data-sent />
            <span class="hint">留空表示该条不参与日期筛选</span>
          </div>
        </div>

        <div class="field">
          <label>消息原文</label>
          <textarea class="textarea" data-content placeholder="粘贴群通知原文…"></textarea>
          <span class="hint" data-count>0 字</span>
        </div>

        <div data-single-only>
          <details class="details-fold" open>
            <summary>▾ 事件标注（可选，用于结构化检索与更正追踪）</summary>
            <div class="details-fold__body stack stack--sm">
              <div class="grid grid--2">
                <div class="field">
                  <label>事件标识 event_key</label>
                  <input class="input" data-key placeholder="如 service" />
                </div>
                <div class="field">
                  <label>事件名称 title</label>
                  <input class="input" data-title placeholder="如 外出志愿服务" />
                </div>
              </div>
              <div class="grid grid--2">
                <div class="field">
                  <label>修订类型</label>
                  <select class="select" data-kind>
                    <option value="initial">首发通知</option>
                    <option value="correction">更正</option>
                    <option value="cancellation">取消</option>
                  </select>
                </div>
                <div class="field">
                  <label>更正目标 supersedes</label>
                  <input class="input" data-supersedes placeholder="填被更正的 message_id，逗号分隔" />
                  <span class="hint">只能指向同一 event_key 的已有消息</span>
                </div>
              </div>
              <label class="checkbox"><input type="checkbox" data-confirmed />我已核对：这条更正确实指向该通知（source_confirmed）</label>

              <div>
                <div class="row" style="margin-bottom:6px">
                  <strong style="font-size:12.5px">结构化字段</strong>
                  <span class="small muted">值必须逐字出现在原文中，最多 20 项</span>
                  <span class="spacer"></span>
                  <button type="button" class="btn btn--sm" data-f-add>+ 添加字段</button>
                </div>
                <div data-fields></div>
                <div hidden data-field-warn></div>
              </div>
            </div>
          </details>
        </div>

        <div class="field">
          <label>幂等键（可选）</label>
          <input class="input mono" data-idem value="${esc(idem)}" />
          <span class="hint">同键同内容会复用上次结果（replayed=true）；同键不同内容返回 409</span>
        </div>

        <div hidden data-result></div>
      </div>`,
    footer: `<button class="btn btn--ghost" data-close="1">关闭</button>
             <button class="btn btn--primary" data-submit>导入</button>`,
  });

  const panel = dlg.panel;
  const contentEl = panel.querySelector('[data-content]');
  const fieldsBox = panel.querySelector('[data-fields]');
  const resultBox = panel.querySelector('[data-result]');
  const countEl = panel.querySelector('[data-count]');
  const singleOnly = panel.querySelector('[data-single-only]');

  fieldsBox.innerHTML = fieldRow('地点', '') + fieldRow('时间', '');
  wireFieldRows(panel, fieldsBox);
  liveCheckFields(panel, contentEl, fieldsBox);

  contentEl.addEventListener('input', () => {
    countEl.textContent = contentEl.value.length + ' 字';
  });

  panel.querySelectorAll('input[name="imp-mode"]').forEach((radio) => {
    radio.onchange = () => {
      const isBatch = panel.querySelector('input[name="imp-mode"]:checked').value === 'batch';
      singleOnly.style.display = isBatch ? 'none' : '';
      contentEl.placeholder = isBatch ? '每行一条通知，空行会被忽略…' : '粘贴群通知原文…';
      contentEl.style.minHeight = isBatch ? '180px' : '96px';
    };
  });

  panel.querySelector('[data-submit]').onclick = async () => {
    const btn = panel.querySelector('[data-submit]');
    const mode = panel.querySelector('input[name="imp-mode"]:checked').value;
    const groupLabel = panel.querySelector('[data-group]').value.trim();
    const sentAt = panel.querySelector('[data-sent]').value;
    const raw = contentEl.value;

    let messages = [];
    let meta = null;
    if (mode === 'batch') {
      const lines = raw.split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
      if (!lines.length) return toast('请先粘贴消息内容', 'warn');
      if (lines.length > 200) return toast('一次最多 200 条，请分批导入', 'warn');
      messages = lines.map((line) => buildMessage({ content: line, groupLabel, sentAt, meta: null }));
    } else {
      if (!raw.trim()) return toast('请先粘贴消息内容', 'warn');
      const kind = panel.querySelector('[data-kind]').value;
      meta = {
        eventKey: panel.querySelector('[data-key]').value,
        title: panel.querySelector('[data-title]').value,
        revisionKind: kind,
        supersedes: panel
          .querySelector('[data-supersedes]')
          .value.split(/[,，\s]+/)
          .map((s) => s.trim())
          .filter(Boolean),
        sourceConfirmed: panel.querySelector('[data-confirmed]').checked,
        fields: readFields(fieldsBox),
      };
      const bad = checkFields(raw, meta.fields);
      if (bad.length) {
        resultBox.hidden = false;
        resultBox.className = 'notice notice--danger';
        resultBox.innerHTML = `<span class="notice__icon">✕</span><div>字段值必须逐字出现在原文中，修正后再提交：
          ${bad.map((b) => `<span class="chip" style="margin:2px 4px 0 0">${esc(b.name)}：${esc(b.value)}</span>`).join('')}</div>`;
        return;
      }
      if (kind !== 'initial' && !meta.eventKey) {
        return toast('更正/取消必须填写 event_key', 'warn');
      }
      if (!meta.eventKey) {
        // 没有事件标注时不要提交空的 metadata
        meta = meta.fields && Object.keys(meta.fields).length ? { fields: meta.fields } : null;
        if (meta && !Object.keys(meta.fields).length) meta = null;
      }
      messages = [buildMessage({ content: raw, groupLabel, sentAt, meta })];
    }

    const idempotencyKey = panel.querySelector('[data-idem]').value.trim();
    const payload = { messages };
    if (idempotencyKey) payload.idempotency_key = idempotencyKey;

    btn.disabled = true;
    btn.textContent = '导入中…';
    try {
      const res = await api.importMessages(payload);
      resultBox.hidden = false;
      const ok = res.failure_count === 0;
      resultBox.className = 'notice ' + (ok ? 'notice--info' : 'notice--warn');
      resultBox.innerHTML = `
        <span class="notice__icon">${ok ? '✓' : '!'}</span>
        <div>
          <div>成功 <strong>${res.success_count}</strong> 条，失败 <strong>${res.failure_count}</strong> 条
          ${res.replayed ? '<span class="badge badge--info">幂等重放</span>' : ''}</div>
          ${res.errors && res.errors.length
            ? `<div class="small" style="margin-top:6px">${res.errors
                .map((e) => `第 ${e.index + 1} 条：<code>${esc(e.code)}</code> ${esc(e.message)}`)
                .join('<br>')}</div>`
            : ''}
          <div class="small muted" style="margin-top:6px">batch_id：<code>${esc(res.batch_id)}</code></div>
        </div>`;
      if (res.success_count > 0) {
        toast(`已导入 ${res.success_count} 条`, 'ok');
        if (typeof onDone === 'function') onDone();
        if (mode === 'batch' || res.failure_count === 0) contentEl.value = '';
        countEl.textContent = '0 字';
      }
    } catch (err) {
      resultBox.hidden = false;
      resultBox.className = 'notice notice--danger';
      resultBox.innerHTML = `<span class="notice__icon">✕</span><div><strong>${esc(err.code)}</strong>：${esc(err.message)}
        ${err.hint ? `<div class="small" style="margin-top:4px">${esc(err.hint)}</div>` : ''}</div>`;
    } finally {
      btn.disabled = false;
      btn.textContent = '导入';
    }
  };

  return dlg;
}

/* ------------------------------------------------------------- 编辑对话框 */
export function openEditDialog({ api, message, onDone }) {
  const meta = message.metadata || {};
  const fieldHtml = Object.keys(meta.fields || {}).length
    ? Object.entries(meta.fields).map(([k, v]) => fieldRow(k, v)).join('')
    : fieldRow('地点', '');

  const dlg = dialog({
    title: '修订消息',
    width: 720,
    body: `
      <div class="stack">
        <div class="notice notice--warn">
          <span class="notice__icon">!</span>
          <div><strong>修订会重建该条索引，并让当前数据空间的全部会话失效</strong>
          （不是只失效一条引用的会话）。保存后旧会话再查询会返回 404。</div>
        </div>
        <div class="grid grid--2">
          <div class="field">
            <label>群标签</label>
            <input class="input" data-group value="${esc(message.group_label || '')}" />
          </div>
          <div class="field">
            <label>发送时间</label>
            <input class="input" type="datetime-local" data-sent value="${esc(toLocalInput(message.sent_at))}" />
          </div>
        </div>
        <div class="field">
          <label>消息原文</label>
          <textarea class="textarea" data-content>${esc(message.content)}</textarea>
        </div>

        <details class="details-fold">
          <summary>▾ 事件标注</summary>
          <div class="details-fold__body stack stack--sm">
            <div class="grid grid--2">
              <div class="field">
                <label>事件标识 event_key</label>
                <input class="input" data-key value="${esc(meta.event_key || '')}" />
              </div>
              <div class="field">
                <label>事件名称 title</label>
                <input class="input" data-title value="${esc(meta.title || '')}" />
              </div>
            </div>
            <div class="grid grid--2">
              <div class="field">
                <label>修订类型</label>
                <select class="select" data-kind>
                  ${Object.entries(KIND_LABEL)
                    .map(([k, label]) => `<option value="${k}" ${(meta.revision_kind || 'initial') === k ? 'selected' : ''}>${label}</option>`)
                    .join('')}
                </select>
              </div>
              <div class="field">
                <label>更正目标 supersedes</label>
                <input class="input" data-supersedes value="${esc((meta.supersedes || []).join(', '))}" />
              </div>
            </div>
            <label class="checkbox"><input type="checkbox" data-confirmed ${meta.source_confirmed ? 'checked' : ''} />来源关系已由我核对（source_confirmed）</label>
            <div>
              <div class="row" style="margin-bottom:6px">
                <strong style="font-size:12.5px">结构化字段</strong>
                <span class="spacer"></span>
                <button type="button" class="btn btn--sm" data-f-add>+ 添加字段</button>
              </div>
              <div data-fields>${fieldHtml}</div>
              <div hidden data-field-warn></div>
            </div>
          </div>
        </details>
        <div hidden data-result></div>
      </div>`,
    footer: `<button class="btn btn--ghost" data-close="1">取消</button>
             <button class="btn btn--primary" data-save>保存修订</button>`,
  });

  const panel = dlg.panel;
  const fieldsBox = panel.querySelector('[data-fields]');
  const contentEl = panel.querySelector('[data-content]');
  wireFieldRows(panel, fieldsBox);
  liveCheckFields(panel, contentEl, fieldsBox);

  panel.querySelector('[data-save]').onclick = async () => {
    const btn = panel.querySelector('[data-save]');
    const content = contentEl.value;
    const fields = readFields(fieldsBox);
    const bad = checkFields(content, fields);
    const resultBox = panel.querySelector('[data-result]');
    if (!content.trim()) return toast('原文不能为空', 'warn');
    if (bad.length) {
      resultBox.hidden = false;
      resultBox.className = 'notice notice--danger';
      resultBox.innerHTML = `<span class="notice__icon">✕</span><div>字段值必须逐字出现在原文中：
        ${bad.map((b) => `<span class="chip" style="margin:2px 4px 0 0">${esc(b.name)}：${esc(b.value)}</span>`).join('')}</div>`;
      return;
    }

    const newMeta = { ...meta };
    const key = panel.querySelector('[data-key]').value.trim();
    const title = panel.querySelector('[data-title]').value.trim();
    const kind = panel.querySelector('[data-kind]').value;
    const supersedes = panel
      .querySelector('[data-supersedes]')
      .value.split(/[,，\s]+/)
      .map((s) => s.trim())
      .filter(Boolean);
    const confirmed = panel.querySelector('[data-confirmed]').checked;

    key ? (newMeta.event_key = key) : delete newMeta.event_key;
    title ? (newMeta.title = title) : delete newMeta.title;
    newMeta.revision_kind = kind;
    supersedes.length ? (newMeta.supersedes = supersedes) : delete newMeta.supersedes;
    confirmed ? (newMeta.source_confirmed = true) : delete newMeta.source_confirmed;
    Object.keys(fields).length ? (newMeta.fields = fields) : delete newMeta.fields;

    const payload = {
      content,
      group_label: panel.querySelector('[data-group]').value.trim() || '未知来源',
      sent_at: panel.querySelector('[data-sent]').value
        ? new Date(panel.querySelector('[data-sent]').value).toISOString()
        : null,
      metadata: newMeta,
    };

    btn.disabled = true;
    btn.textContent = '保存中…';
    try {
      const updated = await api.patchMessage(message.id, payload);
      toast('修订已保存，会话已失效', 'ok');
      dlg.close();
      if (typeof onDone === 'function') onDone(updated);
    } catch (err) {
      resultBox.hidden = false;
      resultBox.className = 'notice notice--danger';
      resultBox.innerHTML = `<span class="notice__icon">✕</span><div><strong>${esc(err.code)}</strong>：${esc(err.message)}
        ${err.hint ? `<div class="small" style="margin-top:4px">${esc(err.hint)}</div>` : ''}</div>`;
    } finally {
      btn.disabled = false;
      btn.textContent = '保存修订';
    }
  };

  return dlg;
}

/* --------------------------------------------------------- 删除确认流程 */
export async function deleteMessageFlow({ api, message, onDone, onSessionsInvalidated }) {
  const ok = await confirmDialog({
    title: '删除这条消息？',
    danger: true,
    confirmText: '删除',
    message: `将删除 <strong>${esc(message.group_label || '未知来源')}</strong> 的这条原文与索引：
      <div class="quote-block" style="margin:10px 0;font-size:13px">${esc(message.content.slice(0, 180))}</div>
      <strong>该数据空间内全部会话、轮次与幂等批次都会失效</strong>，旧会话再查询返回 404。
      备份文件中的旧副本不会被清除（需要单独按保留期限清理）。`,
  });
  if (!ok) return false;

  try {
    const res = await api.deleteMessage(message.id);
    toast('已删除，派生会话已清理', 'ok');
    if (typeof onSessionsInvalidated === 'function') onSessionsInvalidated();
    if (typeof onDone === 'function') onDone(res);
    return true;
  } catch (err) {
    toast(`${err.code}：${err.message}`, 'error');
    return false;
  }
}

/* ------------------------------------------------------------- 单条详情抽屉 */
export function openMessageDrawer({ api, messageId, onOpenFull, onChanged, onSessionsInvalidated }) {
  const host = document.getElementById('modal-host');
  const wrap = document.createElement('div');
  wrap.className = 'drawer';
  wrap.innerHTML = `
    <div class="drawer__mask" data-close="1"></div>
    <div class="drawer__panel">
      <div class="drawer__head">
        <strong style="font-size:14px">原文详情</strong>
        <span class="badge" data-badge>载入中</span>
        <span class="spacer"></span>
        <button class="icon-btn" data-close="1">✕</button>
      </div>
      <div class="drawer__body"><div class="skeleton" style="height:16px;width:60%"></div></div>
      <div class="drawer__foot">
        <button class="btn btn--sm" data-edit disabled>编辑</button>
        <button class="btn btn--sm btn--danger" data-del disabled>删除</button>
        <span class="spacer"></span>
        <button class="btn btn--sm btn--ghost" data-full>在详情页打开 →</button>
      </div>
    </div>`;
  host.appendChild(wrap);
  requestAnimationFrame(() => wrap.classList.add('is-open'));
  const close = () => {
    wrap.classList.remove('is-open');
    setTimeout(() => wrap.remove(), 180);
  };
  wrap.addEventListener('click', (e) => {
    if (e.target.closest('[data-close]')) close();
  });

  const body = wrap.querySelector('.drawer__body');
  let current = null;

  const paint = (msg) => {
    current = msg;
    const m = msg.metadata || {};
    const fields = Object.entries(m.fields || {});
    wrap.querySelector('[data-badge]').textContent = m.event_key ? `事件 ${m.event_key}` : '未标注';
    wrap.querySelector('[data-badge]').className = 'badge ' + (m.event_key ? 'badge--accent' : '');
    wrap.querySelector('[data-edit]').disabled = false;
    wrap.querySelector('[data-del]').disabled = false;

    body.innerHTML = `
      <div class="stack">
        <div class="row small muted">
          <span class="chip">${esc(msg.group_label || '未知来源')}</span>
          <span>发送 ${esc(fmtTime(msg.sent_at))}</span>
          <span>导入 ${esc(fmtTime(msg.imported_at))}</span>
          <span class="badge">修订 v${esc(msg.revision)}</span>
        </div>
        <div class="quote-block">${esc(msg.content)}</div>
        ${
          fields.length
            ? `<div><div class="small muted" style="margin-bottom:6px">结构化字段（值必须逐字出现在原文中）</div>
                <div class="fields">${fields
                  .map(
                    ([k, v]) => `<div class="field-item">
                      <div class="field-item__name">${esc(k)}</div>
                      <div><div class="field-item__value">${esc(v)}</div>
                      <div class="field-item__quote">${esc(msg.content.includes(v) ? '✓ 原文可核对' : '⚠ 原文中未逐字出现')}</div></div>
                    </div>`,
                  )
                  .join('')}</div></div>`
            : ''
        }
        <dl class="kv-list">
          <dt>message_id</dt><dd class="mono">${esc(msg.id)}</dd>
          <dt>batch_id</dt><dd class="mono">${esc(msg.batch)}</dd>
          <dt>event_key</dt><dd class="mono">${esc(m.event_key || '—')}</dd>
          <dt>修订类型</dt><dd>${esc(KIND_LABEL[m.revision_kind || 'initial'] || '首发通知')}</dd>
          <dt>supersedes</dt><dd class="mono">${esc((m.supersedes || []).join(', ') || '—')}</dd>
          <dt>来源已确认</dt><dd>${m.source_confirmed ? '是（导入者标注）' : '否'}</dd>
        </dl>
        <div class="notice notice--muted small"><span class="notice__icon">i</span>
          <div>来源与更正关系由导入者标注，系统未做发布者身份的独立认证。</div></div>
      </div>`;
  };

  api
    .getMessage(messageId)
    .then(paint)
    .catch((err) => {
      body.innerHTML = `<div class="notice notice--danger"><span class="notice__icon">✕</span>
        <div><strong>${esc(err.code)}</strong>：${esc(err.message)}</div></div>`;
      wrap.querySelector('[data-badge]').textContent = '加载失败';
    });

  wrap.querySelector('[data-edit]').onclick = () => {
    if (!current) return;
    openEditDialog({
      api,
      message: current,
      onDone: (updated) => {
        paint(updated);
        close();
        if (typeof onChanged === 'function') onChanged(updated);
        if (typeof onSessionsInvalidated === 'function') onSessionsInvalidated();
      },
    });
  };
  wrap.querySelector('[data-del]').onclick = async () => {
    if (!current) return;
    const done = await deleteMessageFlow({
      api,
      message: current,
      onSessionsInvalidated,
      onDone: () => {
        close();
        if (typeof onChanged === 'function') onChanged(null);
      },
    });
    if (!done) return;
  };
  wrap.querySelector('[data-full]').onclick = () => {
    close();
    if (typeof onOpenFull === 'function') onOpenFull(messageId);
  };

  return { close };
}
