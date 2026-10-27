/* 智能查找：会话 + 提问 + 条件更正 + 引用溯源 */
import { esc, fmtTime, toast, highlight, STATUS_TEXT, STATUS_TONE } from '../util.js';
import { toIso } from '../api.js';

const COND_LABEL = {
  query_text: '检索词',
  event_key: '事件',
  group_label: '群标签',
  sent_after: '起始',
  sent_before: '截止',
};
const DATE_KEYS = new Set(['sent_after', 'sent_before']);

const local = {
  pendingUpdates: {},
  pendingClears: [],
  busy: false,
  searchMode: 'expanded',
};

export async function render(root, ctx) {
  const sess = ctx.state.currentSession;
  const coverage = ctx.state.coverage;

  root.innerHTML = `
    <div class="page-head">
      <div>
        <h2>智能查找</h2>
        <p>用口语描述记忆，AI 在<strong>已导入</strong>的消息里找答案，并给出可回到原文的引用。
        后端是 <code>${esc(ctx.state.capabilities?.mode || '—')}</code> 模式：
        ${ctx.state.capabilities?.simulation ? '当前为模拟适配器，只做确定性规则，不理解任意口语更正。' : '当前接真实模型。'}
        </p>
      </div>
      <span class="spacer"></span>
      <button class="btn" data-act="new">+ 新建会话</button>
    </div>

    <div class="search-layout">
      <section class="card chat">
        <div class="card__head">
          <h2>${sess ? '会话 ' + esc(sess.id.slice(0, 10)) + '…' : '新会话'}</h2>
          <span class="badge" data-ver>${sess ? '条件 v' + sess.version : '尚未提问'}</span>
          <span class="spacer"></span>
          <button class="btn btn--sm btn--ghost" data-act="export">导出会话</button>
        </div>
        <div class="chat__stream" id="stream"></div>
        <div class="composer">
          <div class="composer__row">
            <textarea class="textarea" id="ask" rows="2" placeholder="例如：之前那个志愿活动在哪集合？（Enter 发送，Shift+Enter 换行）"></textarea>
            <button class="btn btn--primary" id="send">发送</button>
          </div>
          <div class="composer__hint">
            <label class="checkbox" style="font-size:11.5px">
              <input type="radio" name="mode" value="expanded" ${local.searchMode === 'expanded' ? 'checked' : ''} />扩展检索
            </label>
            <label class="checkbox" style="font-size:11.5px">
              <input type="radio" name="mode" value="keyword" ${local.searchMode === 'keyword' ? 'checked' : ''} />仅关键词
            </label>
            <span class="spacer"></span>
            <span>引用必须能在原文中逐字核对，否则后端会拒绝该答案</span>
          </div>
        </div>
      </section>

      <aside class="stack">
        <section class="card">
          <div class="card__head"><h2>当前条件</h2><span class="spacer"></span>
            <span class="small muted">随下次提问一起提交</span></div>
          <div class="card__body">
            <div class="cond-panel" id="cond-panel"></div>
            <div style="border-top:1px solid var(--line);margin-top:12px;padding-top:12px">
              <div class="cond-row">
                <select class="select" id="cond-key">
                  ${Object.entries(COND_LABEL).map(([k, v]) => `<option value="${k}">${v}</option>`).join('')}
                </select>
                <input class="input" id="cond-value" placeholder="值" />
              </div>
              <div class="row" style="margin-top:8px">
                <button class="btn btn--sm" id="cond-add">加入待应用</button>
                <span class="spacer"></span>
                <span class="small muted">日期用“发送时间”筛选</span>
              </div>
            </div>
          </div>
        </section>

        <section class="card">
          <div class="card__head"><h2>历史会话</h2><span class="spacer"></span>
            <span class="small muted">本机记录</span></div>
          <div class="card__body card__body--tight" id="session-list"></div>
        </section>

        <section class="card">
          <div class="card__head"><h2>覆盖范围</h2></div>
          <div class="card__body small muted">
            ${coverage
              ? `已导入 <strong>${coverage.imported_count}</strong> 条，可检索 <strong>${coverage.indexed_count}</strong> 条；
                 群：${esc((coverage.groups || []).join('、') || '—')}。
                 <div style="margin-top:6px">${esc(coverage.scope || '')}</div>`
              : '尚未读取覆盖范围'}
          </div>
        </section>
      </aside>
    </div>
  `;

  const stream = root.querySelector('#stream');
  const askEl = root.querySelector('#ask');

  /* ---------------- 渲染 ---------------- */
  function paintStream() {
    const s = ctx.state.currentSession;
    if (!s || !s.turns.length) {
      stream.innerHTML = `<div class="empty"><span class="empty__icon">✦</span>
        直接输入问题即可开始。试试「之前那个志愿活动在哪集合」——<br />
        它会先追问你到底指哪个活动，再给出带原文引用的答案。</div>`;
      return;
    }
    stream.innerHTML = s.turns
      .map(
        (t) => `
        <div class="bubble bubble--user">
          <div class="bubble__avatar">我</div>
          <div class="bubble__body"><div class="bubble__text">${esc(t.question)}</div></div>
        </div>
        <div class="bubble bubble--ai">
          <div class="bubble__avatar">忆</div>
          <div class="bubble__body">${renderResult(t.result)}</div>
        </div>`,
      )
      .join('');
    stream.scrollTop = stream.scrollHeight;
  }

  function paintConditions() {
    const s = ctx.state.currentSession;
    const applied = (s && s.conditions) || {};
    const keys = Object.keys(applied).filter((k) => k !== 'query_text');
    const pending = Object.keys(local.pendingUpdates);
    const clearing = local.pendingClears;

    const parts = [];
    if (keys.length) {
      parts.push(
        keys
          .map(
            (k) => `<span class="chip chip--cond">${esc(COND_LABEL[k] || k)}：${esc(
              DATE_KEYS.has(k) ? fmtTime(applied[k]) : applied[k],
            )}<button data-clear="${esc(k)}" title="下次提问时清除">✕</button></span>`,
          )
          .join(' '),
      );
    } else {
      parts.push('<span class="cond-empty">还没有附加条件（只有检索词）</span>');
    }
    if (pending.length || clearing.length) {
      parts.push(
        `<div style="margin-top:8px"><div class="small muted" style="margin-bottom:4px">待应用</div>
        ${pending
          .map(
            (k) => `<span class="chip chip--cond">+ ${esc(COND_LABEL[k] || k)}：${esc(
              DATE_KEYS.has(k) ? fmtTime(local.pendingUpdates[k]) : local.pendingUpdates[k],
            )}<button data-drop="${esc(k)}">✕</button></span>`,
          )
          .join(' ')}
        ${clearing
          .map((k) => `<span class="chip" style="border-color:#f3c3bd;color:#8f2b20">− ${esc(COND_LABEL[k] || k)}<button data-undrop="${esc(k)}">✕</button></span>`)
          .join(' ')}
        </div>`,
      );
    }

    root.querySelector('#cond-panel').innerHTML = parts.join('');
    root.querySelectorAll('[data-clear]').forEach((b) => {
      b.onclick = () => {
        const k = b.dataset.clear;
        if (!local.pendingClears.includes(k)) local.pendingClears.push(k);
        delete local.pendingUpdates[k];
        paintConditions();
      };
    });
    root.querySelectorAll('[data-drop]').forEach((b) => {
      b.onclick = () => { delete local.pendingUpdates[b.dataset.drop]; paintConditions(); };
    });
    root.querySelectorAll('[data-undrop]').forEach((b) => {
      b.onclick = () => {
        local.pendingClears = local.pendingClears.filter((x) => x !== b.dataset.undrop);
        paintConditions();
      };
    });

    // 事件 chip 支持一键提问
    const verEl = root.querySelector('[data-ver]');
    if (verEl) verEl.textContent = s ? '条件 v' + s.version : '尚未提问';
  }

  function paintSessions() {
    const list = ctx.state.sessions || [];
    const host = root.querySelector('#session-list');
    if (!list.length) {
      host.innerHTML = '<div class="small muted" style="padding:6px">还没有历史会话</div>';
      return;
    }
    host.innerHTML = list
      .slice(0, 8)
      .map(
        (s) => `<button class="nav__item" data-sess="${esc(s.id)}" style="font-size:12.5px">
          <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;text-align:left">${esc(s.title || s.id)}</span>
          ${s.invalid ? '<span class="badge badge--danger">失效</span>' : `<span class="badge">${esc(s.status || '')}</span>`}
        </button>`,
      )
      .join('');
    host.querySelectorAll('[data-sess]').forEach((b) => {
      b.onclick = () => loadSession(b.dataset.sess);
    });
  }

  /* ---------------- 动作 ---------------- */
  function pushTurn(question, result) {
    const s = ctx.state.currentSession || { id: null, conditions: {}, version: 0, turns: [] };
    s.turns.push({ question, result, created: new Date().toISOString() });
    if (result.session_id) s.id = result.session_id;
    if (result.conditions) s.conditions = result.conditions;
    if (result.condition_version) s.version = result.condition_version;
    ctx.state.currentSession = s;
    ctx.rememberSession({
      id: s.id,
      title: s.turns[0] ? s.turns[0].question.slice(0, 26) : '会话',
      status: result.status,
      updatedAt: new Date().toISOString(),
    });
    paintStream();
    paintConditions();
    paintSessions();
  }

  async function submit(question, extra = {}) {
    if (local.busy || !question.trim()) return;
    local.busy = true;
    const sendBtn = root.querySelector('#send');
    sendBtn.disabled = true;
    sendBtn.textContent = '检索中…';

    // 先占位，让用户看到自己的提问
    const s0 = ctx.state.currentSession || { id: null, conditions: {}, version: 0, turns: [] };
    if (!ctx.state.currentSession) ctx.state.currentSession = s0;
    stream.insertAdjacentHTML(
      'beforeend',
      `<div class="bubble bubble--user"><div class="bubble__avatar">我</div>
        <div class="bubble__body"><div class="bubble__text">${esc(question)}</div></div></div>
       <div class="bubble bubble--ai" id="pending"><div class="bubble__avatar">忆</div>
        <div class="bubble__body"><div class="bubble__text"><span class="typing"><i></i><i></i><i></i></span> 正在检索并核对原文…</div></div></div>`,
    );
    stream.scrollTop = stream.scrollHeight;

    const payload = { question, search_mode: local.searchMode };
    if (s0.id) payload.session_id = s0.id;
    const updates = { ...local.pendingUpdates, ...(extra.conditionUpdates || {}) };
    if (Object.keys(updates).length) payload.condition_updates = updates;
    const clears = [...local.pendingClears, ...(extra.clearConditions || [])];
    if (clears.length) payload.clear_conditions = clears;

    try {
      const result = await ctx.api.query(payload);
      local.pendingUpdates = {};
      local.pendingClears = [];
      pushTurn(question, result);
    } catch (err) {
      const pending = root.querySelector('#pending');
      if (pending) pending.remove();
      if (err.status === 401) {
        ctx.handleError(err);
      } else if (err.payload && err.payload.status === 'error' && err.payload.session_id) {
        // 502/503/504：后端已保存会话与 trace，仍然当作一轮结果展示
        pushTurn(question, { ...err.payload, status: 'error', error: err.payload.error || { code: err.code, message: err.message } });
      } else if (err.status === 404) {
        toast('该会话已失效（消息被修订或删除），已新建会话', 'warn');
        ctx.state.currentSession = { id: null, conditions: {}, version: 0, turns: [] };
        ctx.clearSessions('会话已失效');
      } else {
        toast(`${err.code}：${err.message}`, 'error');
        stream.insertAdjacentHTML(
          'beforeend',
          `<div class="notice notice--danger"><span class="notice__icon">✕</span>
            <div><strong>${esc(err.code)}</strong>：${esc(err.message)}
            ${err.hint ? `<div class="small" style="margin-top:4px">${esc(err.hint)}</div>` : ''}</div></div>`,
        );
      }
    } finally {
      local.busy = false;
      sendBtn.disabled = false;
      sendBtn.textContent = '发送';
    }
  }

  async function loadSession(id) {
    try {
      const data = await ctx.api.getSession(id);
      ctx.state.currentSession = {
        id: data.id,
        conditions: data.conditions || {},
        version: data.version || 0,
        turns: (data.turns || []).map((t) => ({ question: t.question, result: t.result, created: t.created })),
      };
      local.pendingUpdates = {};
      local.pendingClears = [];
      await render(root, ctx);
      toast('已恢复会话', 'ok');
    } catch (err) {
      if (err.status === 404) {
        toast('该会话已失效，无法恢复', 'warn');
        ctx.markSessionInvalid(id);
        paintSessions();
      } else {
        toast(`${err.code}：${err.message}`, 'error');
      }
    }
  }

  /* ---------------- 事件绑定 ---------------- */
  root.querySelector('#send').onclick = () => {
    const q = askEl.value.trim();
    if (!q) return toast('先输入一个问题', 'warn');
    askEl.value = '';
    submit(q);
  };
  askEl.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      root.querySelector('#send').click();
    }
  });
  root.querySelectorAll('input[name="mode"]').forEach((r) => {
    r.onchange = () => { local.searchMode = r.value; };
  });

  const keyEl = root.querySelector('#cond-key');
  const valEl = root.querySelector('#cond-value');
  keyEl.onchange = () => {
    const isDate = DATE_KEYS.has(keyEl.value);
    valEl.type = isDate ? 'datetime-local' : 'text';
    valEl.placeholder = isDate ? '' : keyEl.value === 'event_key' ? '如 service' : '如 模拟班级群';
    valEl.value = '';
  };
  root.querySelector('#cond-add').onclick = () => {
    const key = keyEl.value;
    const raw = valEl.value.trim();
    if (!raw) return toast('先填一个值', 'warn');
    local.pendingUpdates[key] = DATE_KEYS.has(key) ? toIso(raw) : raw;
    local.pendingClears = local.pendingClears.filter((k) => k !== key);
    valEl.value = '';
    paintConditions();
  };

  root.querySelector('[data-act="new"]').onclick = () => {
    ctx.state.currentSession = { id: null, conditions: {}, version: 0, turns: [] };
    local.pendingUpdates = {};
    local.pendingClears = [];
    render(root, ctx);
  };
  root.querySelector('[data-act="export"]').onclick = () => {
    const s = ctx.state.currentSession;
    if (!s || !s.turns.length) return toast('还没有可导出的会话', 'warn');
    ctx.exportSession(s);
  };

  // 结果卡内的按钮（事件委托）
  stream.onclick = (e) => {
    const opt = e.target.closest('[data-option-key]');
    if (opt) {
      submit(opt.dataset.optionTitle || '选择该事件', { conditionUpdates: { event_key: opt.dataset.optionKey } });
      return;
    }
    const cite = e.target.closest('[data-open-msg]');
    if (cite) {
      ctx.openDetail(cite.dataset.openMsg);
      return;
    }
    const copy = e.target.closest('[data-copy]');
    if (copy) {
      navigator.clipboard && navigator.clipboard.writeText(copy.dataset.copy);
      toast('已复制 request_id', 'ok');
      return;
    }
    const logBtn = e.target.closest('[data-log]');
    if (logBtn) {
      ctx.navigate('#/system?req=' + encodeURIComponent(logBtn.dataset.log));
    }
  };

  // 概览页带过来的事件预选
  if (ctx.state.pendingEventKey) {
    local.pendingUpdates.event_key = ctx.state.pendingEventKey;
    ctx.state.pendingEventKey = null;
    askEl.value = '这个活动具体安排是什么';
    toast('已预选事件，可直接提问', 'info');
  }

  paintStream();
  paintConditions();
  paintSessions();
  askEl.focus();
}

/* ------------------------------------------------------------------ 结果卡 */
function renderResult(r) {
  const status = r.status || 'uncertain';
  const tone = STATUS_TONE[status] || 'muted';
  const ai = r.ai || {};
  const trace = r.trace || [];
  const conds = r.conditions || {};
  const reqId = r.request_id || '';

  const head = `
    <div class="answer-card__head">
      <span class="badge badge--${tone}">${esc(STATUS_TEXT[status] || status)}</span>
      ${status !== 'error' && r.search_mode ? `<span class="badge">${r.search_mode === 'keyword' ? '关键词' : '扩展'}</span>` : ''}
      ${ai.simulation ? '<span class="badge badge--warn" title="后端 mock 适配器，规则确定性输出">模拟适配器</span>' : ai.mode ? '<span class="badge badge--ok">真实模型</span>' : ''}
      ${status === 'error' ? `<span class="badge badge--danger">${esc((r.error && r.error.code) || '')}</span>` : ''}
      <span class="spacer"></span>
      ${reqId ? `<button class="icon-btn" data-copy="${esc(reqId)}" title="复制 request_id">⧉</button>` : ''}
    </div>`;

  let body = '';

  if (status === 'answer') {
    const fields = r.fields || [];
    body = `
      <div class="answer-summary">${esc(r.answer || '')}</div>
      ${fields.length
        ? `<div class="fields" style="margin-top:10px">${fields
            .map(
              (f) => `<div class="field-item">
                <div class="field-item__name">${esc(f.name)}</div>
                <div>
                  <div class="field-item__value">${esc(f.value)}</div>
                  <div class="field-item__quote">${highlight(truncateQuote(f.quote, f.value), f.value)}</div>
                  <div style="margin-top:4px"><button class="btn btn--sm btn--ghost" data-open-msg="${esc(f.message_id)}">查看原文 →</button></div>
                </div>
              </div>`,
            )
            .join('')}</div>`
        : ''}
      ${renderCitations(r.citations)}
    `;
  } else if (status === 'clarify') {
    body = `
      <div class="answer-summary">${esc(r.question || '需要补充更多条件。')}</div>
      ${(r.options || []).length
        ? `<div class="options">${(r.options || [])
            .map(
              (o) => `<button class="option-btn" data-option-key="${esc(o.event_key)}" data-option-title="${esc(o.title)}">
                ${esc(o.title)} <span class="mono" style="opacity:.6">${esc(o.event_key)}</span></button>`,
            )
            .join('')}</div>
          <div class="small muted" style="margin-top:8px">点击其中一项，会把 <code>condition_updates.event_key</code> 一起提交。</div>`
        : ''}
    `;
  } else if (status === 'not_found') {
    body = `
      <div class="answer-summary">${esc(r.answer || '已导入且可检索内容中未找到相关消息。')}</div>
      <div class="notice notice--muted small" style="margin-top:10px"><span class="notice__icon">i</span>
        <div>“没找到”只代表<strong>当前数据空间已导入</strong>的内容里没有匹配。
        覆盖：已导入 ${esc(String((r.coverage || {}).imported_count ?? '?'))} 条。
        ${esc(((r.coverage || {}).scope) || '')}</div></div>
    `;
  } else if (status === 'uncertain') {
    body = `
      <div class="answer-summary">${esc(r.answer || '证据不足，需要人工核实。')}</div>
      ${renderEventBrief(r.event)}
      ${renderCitations(r.citations)}
    `;
  } else {
    const err = r.error || {};
    body = `
      <div class="answer-summary" style="color:var(--danger)">${esc(err.message || '服务调用失败。')}</div>
      <div class="notice notice--danger small" style="margin-top:10px"><span class="notice__icon">!</span>
        <div><strong>error ≠ 没有相关通知。</strong>这是检索/模型调用出了问题（<code>${esc(err.code || '')}</code>），
        不代表库里没有这条消息。可在系统页用 request_id 查日志。</div></div>
    `;
  }

  const traceHtml = trace.length
    ? trace
        .map((t) =>
          t.tool
            ? `<div class="trace-line">search → 命中 ${esc(String(t.count))} 条 · ${esc(t.search || '')}</div>`
            : `<div class="trace-line">model_turn ${esc(String(t.model_turn || ''))} → ${esc(t.action || '')}</div>`,
        )
        .join('')
    : '<div class="small muted">无工具调用记录（mock 模式下只有一次本地检索）</div>';

  return `
    <div class="answer-card">
      ${head}
      <div class="answer-card__body">
        ${body}
        <details class="details-fold">
          <summary>▾ 执行细节与条件</summary>
          <div class="details-fold__body stack stack--sm">
            ${traceHtml}
            <div class="small muted">条件（v${esc(String(r.condition_version ?? '?'))}）</div>
            <pre class="mono" style="margin:0;background:var(--surface-3);padding:8px;border-radius:8px;overflow:auto">${esc(JSON.stringify(conds, null, 2))}</pre>
            ${ai && Object.keys(ai).length
              ? `<div class="small muted">AI 状态：mode=${esc(String(ai.mode))} · 调用 ${esc(String(ai.calls ?? 0))} 次 · 实际参与=${ai.actual_participation ? '是' : '否'} · 校验通过=${ai.succeeded ? '是' : '否'}${ai.error_code ? ' · ' + esc(ai.error_code) : ''}</div>`
              : ''}
            ${reqId ? `<div class="small muted">request_id：<code>${esc(reqId)}</code> <button class="btn btn--sm btn--ghost" data-log="${esc(reqId)}">查日志</button></div>` : ''}
          </div>
        </details>
      </div>
    </div>`;
}

function truncateQuote(quote, value) {
  if (!quote) return '';
  // 只展示包含字段值的那一段，过长时截断
  const idx = value ? quote.indexOf(value) : -1;
  if (idx < 0) return quote.length > 90 ? quote.slice(0, 90) + '…' : quote;
  const start = Math.max(0, idx - 30);
  const end = Math.min(quote.length, idx + value.length + 40);
  return (start > 0 ? '…' : '') + quote.slice(start, end) + (end < quote.length ? '…' : '');
}

function renderCitations(citations) {
  if (!citations || !citations.length) return '';
  const seen = new Set();
  const unique = citations.filter((c) => {
    const k = c.message_id + '|' + c.quote;
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  });
  return `
    <div style="margin-top:12px">
      <div class="small muted" style="margin-bottom:6px">原文引用（${unique.length} 条）</div>
      ${unique
        .map(
          (c) => `<div class="cite">
            <div class="cite__body">
              <div class="cite__meta">
                <span class="chip">${esc(c.group_label || '未知来源')}</span>
                <span>${esc(fmtTime(c.sent_at))}</span>
              </div>
              <div class="cite__quote">${esc(c.quote)}</div>
            </div>
            <button class="btn btn--sm btn--ghost" data-open-msg="${esc(c.message_id)}">原文 →</button>
          </div>`,
        )
        .join('')}
    </div>`;
}

function renderEventBrief(ev) {
  if (!ev || !ev.fields) return '';
  const entries = Object.entries(ev.fields);
  if (!entries.length) return '';
  return `
    <div style="margin-top:10px">
      <div class="small muted" style="margin-bottom:6px">事件 ${esc(ev.title || ev.event_key)} 的当前字段
        ${(ev.conflicts || []).length ? `<span class="badge badge--danger">冲突：${esc(ev.conflicts.join('、'))}</span>` : ''}
        ${ev.cancelled ? '<span class="badge badge--warn">已取消</span>' : ''}
      </div>
      <div class="fields">
        ${entries
          .map(
            ([name, list]) => `<div class="field-item">
              <div class="field-item__name">${esc(name)}</div>
              <div>${list
                .map(
                  (x) => `<div class="field-item__value">${esc(x.value)}</div>
                    <div class="field-item__quote">${highlight(truncateQuote(x.quote, x.value), x.value)}</div>`,
                )
                .join('')}</div>
            </div>`,
          )
          .join('')}
      </div>
    </div>`;
}
