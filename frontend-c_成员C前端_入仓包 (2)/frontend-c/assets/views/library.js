/* 消息库：分页列表 + 本地筛选 + 行内操作 */
import { esc, fmtTime, truncate, toast, debounce } from '../util.js';
import { deleteMessageFlow, openMessageDrawer } from '../message-actions.js';

const ui = { limit: 20, offset: 0, q: '', group: '' };

export async function render(root, ctx) {
  root.innerHTML = `<div class="empty"><span class="empty__icon">◌</span>正在读取消息…</div>`;

  let data;
  try {
    data = await ctx.api.listMessages({ limit: ui.limit, offset: ui.offset });
  } catch (err) {
    root.innerHTML = `<div class="notice notice--danger"><span class="notice__icon">✕</span>
      <div><strong>${esc(err.code)}</strong>：${esc(err.message)}</div></div>`;
    ctx.handleError(err);
    return;
  }

  // 越界保护：删到当前页空了自动回退一页
  if (!data.items.length && ui.offset > 0) {
    ui.offset = Math.max(0, ui.offset - ui.limit);
    return render(root, ctx);
  }

  const total = data.total;
  ctx.state.messagesTotal = total;
  ctx.refreshShell();

  const groups = (ctx.state.coverage && ctx.state.coverage.groups) || [];
  const rows = data.items.filter((m) => {
    if (ui.group && m.group_label !== ui.group) return false;
    if (ui.q) {
      const hay = (m.content + ' ' + m.group_label + ' ' + ((m.metadata && m.metadata.title) || '')).toLowerCase();
      if (!hay.includes(ui.q.toLowerCase())) return false;
    }
    return true;
  });

  const from = total ? data.offset + 1 : 0;
  const to = data.offset + data.items.length;

  root.innerHTML = `
    <div class="page-head">
      <div>
        <h2>消息库</h2>
        <p>后端只提供 <code>limit/offset</code> 分页，没有搜索接口。下面的关键词与群筛选<strong>只作用于当前这一页</strong>，
        翻页后需要重新筛选。</p>
      </div>
      <span class="spacer"></span>
      <button class="btn btn--primary" data-act="import">+ 导入消息</button>
    </div>

    <section class="card">
      <div class="card__head" style="gap:8px;flex-wrap:wrap">
        <input class="input" style="max-width:240px" placeholder="在当前页内搜索内容 / 群 / 标题" data-q value="${esc(ui.q)}" />
        <select class="select" style="max-width:190px" data-group>
          <option value="">全部群标签</option>
          ${groups.map((g) => `<option value="${esc(g)}" ${ui.group === g ? 'selected' : ''}>${esc(g)}</option>`).join('')}
        </select>
        <select class="select" style="max-width:120px" data-limit>
          ${[10, 20, 50, 100].map((n) => `<option value="${n}" ${ui.limit === n ? 'selected' : ''}>每页 ${n}</option>`).join('')}
        </select>
        <span class="spacer"></span>
        <span class="small muted">${rows.length} / ${data.items.length} 条通过筛选</span>
        <button class="btn btn--sm" data-act="reload">刷新</button>
        <button class="btn btn--sm" data-act="rebuild" title="POST /v1/index/rebuild">重建索引</button>
      </div>

      ${
        data.items.length
          ? `<div class="table-wrap">
              <table class="table">
                <thead>
                  <tr>
                    <th style="width:130px">群标签</th>
                    <th>内容</th>
                    <th style="width:150px">发送时间</th>
                    <th style="width:150px">事件</th>
                    <th style="width:96px"></th>
                  </tr>
                </thead>
                <tbody>
                  ${rows
                    .map((m) => {
                      const meta = m.metadata || {};
                      return `<tr data-id="${esc(m.id)}" style="cursor:pointer">
                        <td><span class="chip">${esc(m.group_label || '未知来源')}</span></td>
                        <td class="msg-content">
                          <div class="msg-snippet">${esc(truncate(m.content, 110))}</div>
                          <div class="msg-meta">
                            <span class="mono">${esc(m.id.slice(0, 12))}…</span>
                            <span>修订 v${esc(m.revision)}</span>
                            ${meta.fields && Object.keys(meta.fields).length ? `<span>${Object.keys(meta.fields).length} 个字段</span>` : '<span>未标注字段</span>'}
                          </div>
                        </td>
                        <td class="nowrap">${m.sent_at ? esc(fmtTime(m.sent_at)) : '<span class="muted">未提供</span>'}</td>
                        <td>${
                          meta.event_key
                            ? `<span class="badge badge--accent">${esc(meta.event_key)}</span>
                               ${meta.revision_kind === 'correction' ? '<span class="badge badge--warn">更正</span>' : ''}
                               ${meta.revision_kind === 'cancellation' ? '<span class="badge badge--danger">取消</span>' : ''}`
                            : '<span class="muted small">—</span>'
                        }</td>
                        <td class="nowrap">
                          <button class="btn btn--sm btn--ghost" data-open="${esc(m.id)}">打开</button>
                          <button class="btn btn--sm btn--ghost" data-del="${esc(m.id)}" title="删除">删</button>
                        </td>
                      </tr>`;
                    })
                    .join('')}
                </tbody>
              </table>
            </div>
            ${rows.length ? '' : '<div class="empty">当前页没有符合筛选条件的消息</div>'}
            <div class="pager">
              <span class="pager__info">第 ${from}–${to} 条，共 ${total} 条</span>
              <span class="spacer"></span>
              <button class="btn btn--sm" data-page="prev" ${data.offset === 0 ? 'disabled' : ''}>上一页</button>
              <button class="btn btn--sm" data-page="next" ${to >= total ? 'disabled' : ''}>下一页</button>
            </div>`
          : `<div class="empty">
              <span class="empty__icon">◌</span>
              还没有消息。可以点右上角「导入消息」，或在概览页一键导入 3 条示例数据。
            </div>`
      }
    </section>
  `;

  // --- 交互 ---
  const qEl = root.querySelector('[data-q]');
  qEl.oninput = debounce(() => {
    ui.q = qEl.value.trim();
    const pos = qEl.selectionStart;
    render(root, ctx).then(() => {
      const el = root.querySelector('[data-q]');
      if (el) { el.focus(); el.setSelectionRange(pos, pos); }
    });
  }, 250);

  root.querySelector('[data-group]').onchange = (e) => {
    ui.group = e.target.value;
    render(root, ctx);
  };
  root.querySelector('[data-limit]').onchange = (e) => {
    ui.limit = Number(e.target.value);
    ui.offset = 0;
    render(root, ctx);
  };
  root.querySelector('[data-act="reload"]').onclick = () => render(root, ctx);
  root.querySelector('[data-act="rebuild"]').onclick = async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    try {
      const res = await ctx.api.rebuildIndex();
      toast(`已重建 ${res.indexed} 条索引`, 'ok');
    } catch (err) {
      toast(`${err.code}：${err.message}`, 'error');
    } finally {
      btn.disabled = false;
    }
  };
  root.querySelector('[data-act="import"]').onclick = () => ctx.openImport(() => render(root, ctx));

  root.querySelectorAll('[data-page]').forEach((btn) => {
    btn.onclick = () => {
      ui.offset = btn.dataset.page === 'next' ? ui.offset + ui.limit : Math.max(0, ui.offset - ui.limit);
      render(root, ctx);
    };
  });

  root.querySelectorAll('tbody tr[data-id]').forEach((tr) => {
    tr.querySelector('[data-open]').onclick = (e) => {
      e.stopPropagation();
      ctx.openDetail(tr.dataset.id);
    };
    tr.querySelector('[data-del]').onclick = async (e) => {
      e.stopPropagation();
      try {
        const msg = await ctx.api.getMessage(tr.dataset.id);
        await deleteMessageFlow({
          api: ctx.api,
          message: msg,
          onSessionsInvalidated: () => ctx.clearSessions('消息被删除，旧会话已全部失效'),
          onDone: () => render(root, ctx),
        });
      } catch (err) {
        toast(`${err.code}：${err.message}`, 'error');
      }
    };
    tr.onclick = () =>
      openMessageDrawer({
        api: ctx.api,
        messageId: tr.dataset.id,
        onOpenFull: (id) => ctx.openDetail(id),
        onChanged: () => render(root, ctx),
        onSessionsInvalidated: () => ctx.clearSessions('消息被修订，旧会话已全部失效'),
      });
  });
}
