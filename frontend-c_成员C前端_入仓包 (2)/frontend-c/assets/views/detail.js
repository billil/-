/* 原文详情：单条原文 + 所属事件的版本与变更记录 */
import { esc, fmtTime, toast, highlightAll } from '../util.js';
import { openEditDialog, deleteMessageFlow } from '../message-actions.js';

const KIND = { initial: '首发', correction: '更正', cancellation: '取消' };

export async function render(root, ctx, messageId) {
  root.innerHTML = `<div class="empty"><span class="empty__icon">◌</span>正在读取原文…</div>`;

  let msg;
  try {
    msg = await ctx.api.getMessage(messageId);
  } catch (err) {
    root.innerHTML = `
      <button class="btn btn--sm btn--ghost" data-act="back">← 返回</button>
      <div class="notice notice--danger" style="margin-top:12px"><span class="notice__icon">✕</span>
        <div><strong>${esc(err.code)}</strong>：${esc(err.message)}
        ${err.status === 404 ? '<div class="small" style="margin-top:4px">消息可能已被删除，或该页面链接属于其他数据空间。</div>' : ''}</div>
      </div>`;
    root.querySelector('[data-act="back"]').onclick = () => history.back();
    return;
  }

  const meta = msg.metadata || {};
  const fields = Object.entries(meta.fields || {});
  let ev = null;
  if (meta.event_key) {
    try {
      const list = await ctx.api.events();
      ev = (list.items || []).find((e) => e.event_key === meta.event_key) || null;
    } catch {
      ev = null;
    }
  }

  root.innerHTML = `
    <div class="row" style="margin-bottom:12px">
      <button class="btn btn--sm btn--ghost" data-act="back">← 返回</button>
      <span class="spacer"></span>
      <button class="btn btn--sm" data-act="copy">复制 message_id</button>
      <button class="btn btn--sm" data-act="edit">修订</button>
      <button class="btn btn--sm btn--danger" data-act="del">删除</button>
    </div>

    <div class="grid" style="grid-template-columns:minmax(0,1.55fr) minmax(0,1fr);align-items:start">
      <section class="card">
        <div class="card__head">
          <h2>消息原文</h2>
          <span class="badge">修订 v${esc(msg.revision)}</span>
          <span class="spacer"></span>
          ${meta.revision_kind && meta.revision_kind !== 'initial'
            ? `<span class="badge badge--warn">${esc(KIND[meta.revision_kind] || meta.revision_kind)}</span>`
            : ''}
        </div>
        <div class="card__body stack">
          <div class="row small muted">
            <span class="chip">${esc(msg.group_label || '未知来源')}</span>
            <span>发送 ${esc(fmtTime(msg.sent_at))}</span>
            <span>导入 ${esc(fmtTime(msg.imported_at, true))}</span>
          </div>
          <div class="quote-block" id="quote-block">${esc(msg.content)}</div>
          <div class="small muted">所有字段值都必须在这段原文里逐字出现，否则后端会拒绝该答案。<strong>鼠标移到右侧字段上，可在原文中定位它。</strong></div>
        </div>
      </section>

      <div class="stack">
        <section class="card">
          <div class="card__head"><h2>结构化字段</h2><span class="spacer"></span>
            <span class="small muted">${fields.length} 项</span></div>
          <div class="card__body">
            ${fields.length
              ? `<div class="fields">${fields
                  .map(
                    ([k, v]) => `<div class="field-item" data-focus="${esc(v)}" tabindex="0" role="button"
                      aria-label="在原文中定位 ${esc(k)}">
                      <div class="field-item__name">${esc(k)}</div>
                      <div><div class="field-item__value">${esc(v)}</div>
                        <div class="field-item__quote">${msg.content.includes(v) ? '✓ 原文可核对（悬停定位）' : '⚠ 原文中未逐字出现'}</div></div>
                    </div>`,
                  )
                  .join('')}</div>`
              : '<div class="small muted">这条消息没有结构化标注，只能做原文检索；在 mock 模式下不会产出字段级答案。</div>'}
          </div>
        </section>

        <section class="card">
          <div class="card__head"><h2>元信息</h2></div>
          <div class="card__body">
            <dl class="kv-list">
              <dt>message_id</dt><dd class="mono">${esc(msg.id)}</dd>
              <dt>batch_id</dt><dd class="mono">${esc(msg.batch)}</dd>
              <dt>event_key</dt><dd class="mono">${esc(meta.event_key || '—')}</dd>
              <dt>事件名称</dt><dd>${esc(meta.title || '—')}</dd>
              <dt>修订类型</dt><dd>${esc(KIND[meta.revision_kind || 'initial'] || '首发')}</dd>
              <dt>更正目标</dt><dd class="mono">${esc((meta.supersedes || []).join(', ') || '—')}</dd>
              <dt>来源已确认</dt><dd>${meta.source_confirmed ? '是（导入者标注）' : '否'}</dd>
            </dl>
          </div>
        </section>
      </div>
    </div>

    ${meta.event_key ? renderEvent(ev) : ''}
  `;

  root.querySelector('[data-act="back"]').onclick = () => history.back();
  root.querySelectorAll('[data-msg]').forEach((b) => {
    b.onclick = () => openDetailReplace(b.dataset.msg);
  });
  root.querySelector('[data-act="copy"]').onclick = () => {
    navigator.clipboard && navigator.clipboard.writeText(msg.id);
    toast('已复制', 'ok');
  };
  root.querySelector('[data-act="edit"]').onclick = () => {
    openEditDialog({
      api: ctx.api,
      message: msg,
      onDone: () => {
        ctx.clearSessions('消息被修订，旧会话已全部失效');
        render(root, ctx, messageId);
      },
    });
  };
  root.querySelector('[data-act="del"]').onclick = async () => {
    await deleteMessageFlow({
      api: ctx.api,
      message: msg,
      onSessionsInvalidated: () => ctx.clearSessions('消息被删除，旧会话已全部失效'),
      onDone: () => ctx.navigate('#/library'),
    });
  };

  bindFieldLocator(root, msg.content);
}

/** 字段 → 原文定位：悬停或聚焦字段，在原文里真实标出它出现的位置。
 *  后端保证 value 逐字出现在 content 中，所以这是真实定位，不是视觉效果。 */
function bindFieldLocator(root, content) {
  const block = root.querySelector('#quote-block');
  if (!block) return;
  let active = null;
  const mark = (value) => {
    if (active === value) return;
    active = value;
    block.innerHTML = value ? highlightAll(content, value) : esc(content);
    const m = block.querySelector('mark');
    if (m && value) m.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  };
  root.querySelectorAll('[data-focus]').forEach((el) => {
    const v = el.dataset.focus;
    el.addEventListener('mouseenter', () => mark(v));
    el.addEventListener('focus', () => mark(v));
    el.addEventListener('mouseleave', () => mark(null));
    el.addEventListener('blur', () => mark(null));
  });
}

function openDetailReplace(id) {
  location.hash = '#/detail/' + encodeURIComponent(id);
}

function renderEvent(ev) {
  if (!ev) {
    return `<section class="card" style="margin-top:14px">
      <div class="card__head"><h2>事件版本与变更</h2></div>
      <div class="card__body small muted">未能读取到该事件的聚合信息（GET /v1/events）。</div>
    </section>`;
  }
  const msgs = ev.messages || [];
  const changes = ev.changes || [];
  return `
    <section class="card" style="margin-top:14px">
      <div class="card__head">
        <h2>事件：${esc(ev.title || ev.event_key)}</h2>
        <span class="badge badge--accent mono">${esc(ev.event_key)}</span>
        ${ev.cancelled ? '<span class="badge badge--warn">已取消</span>' : '<span class="badge badge--ok">有效</span>'}
        ${(ev.conflicts || []).length ? `<span class="badge badge--danger">字段冲突：${esc(ev.conflicts.join('、'))}</span>` : ''}
        <span class="spacer"></span>
        <span class="small muted">${msgs.length} 条相关消息</span>
      </div>
      <div class="table-wrap">
        <table class="table">
          <thead><tr><th style="width:96px">类型</th><th>原文</th><th style="width:150px">发送时间</th><th style="width:120px"></th></tr></thead>
          <tbody>
            ${msgs
              .map((m) => {
                const mm = m.metadata || {};
                return `<tr>
                  <td>${mm.revision_kind === 'correction'
                    ? '<span class="badge badge--warn">更正</span>'
                    : mm.revision_kind === 'cancellation'
                      ? '<span class="badge badge--danger">取消</span>'
                      : '<span class="badge">首发</span>'}</td>
                  <td class="small">${esc(m.content)}</td>
                  <td class="nowrap small">${esc(fmtTime(m.sent_at))}</td>
                  <td class="nowrap"><button class="btn btn--sm btn--ghost" data-msg="${esc(m.id)}">打开</button></td>
                </tr>`;
              })
              .join('')}
          </tbody>
        </table>
      </div>
      <div class="card__body">
        <div class="small muted" style="margin-bottom:6px">字段变更（只有 source_confirmed=true 的更正才会覆盖旧值；未确认的冲突并列保留）</div>
        ${changes.length
          ? `<div class="table-wrap"><table class="table">
              <thead><tr><th>字段</th><th>原值</th><th>新值</th><th>来源</th></tr></thead>
              <tbody>${changes
                .map(
                  (c) => `<tr>
                    <td>${esc(c.name)}</td>
                    <td class="muted">${esc(c.before)}</td>
                    <td><strong>${esc(c.after)}</strong></td>
                    <td class="small">${c.confirmed_by_importer ? '<span class="badge badge--ok">导入者已确认</span>' : '<span class="badge">未确认</span>'}
                      <div class="mono muted">${esc(c.to_message_id)}</div></td>
                  </tr>`,
                )
                .join('')}</tbody>
            </table></div>`
          : '<div class="small muted">没有检测到字段级变更。</div>'}
        <div class="notice notice--muted small" style="margin-top:10px"><span class="notice__icon">i</span>
          <div>${esc(ev.source_note || '')}</div></div>
      </div>
    </section>`;
}
