/* =========================================================================
 * integration.js —— 联调检查
 *
 * 解决的问题：任务清单要求"每个关键接口有联调记录"，并且
 * "接口返回 404、403、超时要区分显示"，不能把网络错误显示成"没找到"。
 *
 * 做法：对后端逐个发真实请求，记录 HTTP 状态、错误码、耗时、request_id 和响应片段，
 * 结果可导出成 JSON / Markdown 交给团队。前端不对结果做任何美化或编造。
 * ========================================================================= */
import { esc, fmtTime, downloadJson, toast } from '../util.js';

const LS_LAST = 'qunyi.integration.last';

/** 接口清单：与任务清单里的 P0 / P1 对应 */
const ENDPOINTS = [
  { id: 'health', method: 'GET', path: '/health', name: '健康检查', p: 'P0', public: true, why: '后端活着没有' },
  { id: 'capabilities', method: 'GET', path: '/capabilities', name: '能力与模式', p: 'P0', why: 'mode=mock 还是 live、simulation 标志' },
  { id: 'coverage', method: 'GET', path: '/coverage', name: '覆盖范围', p: 'P0', why: '条数、群标签、索引状态' },
  { id: 'messages', method: 'GET', path: '/v1/messages?limit=5', name: '消息列表', p: 'P0', why: '导入内容能否读回' },
  { id: 'groups', method: 'GET', path: '/v1/groups', name: '可访问群', p: 'P0', why: '群管理页的数据来源' },
  { id: 'subscriptions', method: 'GET', path: '/v1/subscriptions', name: '关注列表', p: 'P0', why: '关注关系存在后端' },
  { id: 'notifications', method: 'GET', path: '/v1/notifications?limit=5', name: '通知增量', p: 'P0', why: 'next_cursor / has_more 契约' },
  { id: 'ack', method: 'POST', path: '/v1/notifications/ack', name: '已读上报', p: 'P0', body: {}, why: '空 body 探测：400 说明接口存在，不会真的写入' },
  { id: 'query', method: 'POST', path: '/v1/query', name: '检索问答', p: 'P0', body: { question: '连通性测试', search_mode: 'balanced' }, expensive: true, why: '会真的走一次检索（live 模式下会调用模型）' },
  { id: 'sync', method: 'GET', path: '/v1/sync/status', name: '同步 / 机器人状态', p: 'P1', why: '机器人离线要能显示出来' },
  { id: 'me', method: 'GET', path: '/me', name: '当前身份', p: 'P1', why: '正式登录后用于显示用户与权限' },
  { id: 'events', method: 'GET', path: '/events', name: '变更事件', p: 'P1', why: '更正 / 取消的来源' },
];

const ui = { running: false, results: null };

/* ------------------------------------------------------------------ 渲染 */
export async function render(root, ctx) {
  const connected = ctx.state.conn === 'backend';
  ui.results = loadLast();

  root.innerHTML = `
    <div class="page-head">
      <div>
        <h2>联调检查</h2>
        <p>对后端逐个发<strong>真实请求</strong>，记录状态码、错误码、耗时和 request_id。
          404（未实现）、403（无权限）、超时（网络）会分开显示，不混成一句"加载失败"。</p>
      </div>
      <span class="spacer"></span>
      <button class="btn btn--sm" data-act="export-json">导出 JSON</button>
      <button class="btn btn--sm" data-act="export-md">导出 Markdown</button>
      <button class="btn btn--sm btn--primary" data-act="run" ${connected ? '' : 'disabled'}>开始检查</button>
    </div>

    ${connected ? '' : `<div class="notice notice--warn" style="margin-bottom:14px"><span class="notice__icon">!</span>
      <div>当前处于<strong>本地试用</strong>，没有后端可测。请先在左下角「登录 / 连接设置」里连上后端。</div></div>`}

    <div id="ig-summary"></div>
    <div id="ig-table"></div>
  `;

  root.querySelector('[data-act="run"]').onclick = () => run(root, ctx);
  root.querySelector('[data-act="export-json"]').onclick = () => {
    if (!ui.results) return toast('还没有检查结果', 'warn');
    downloadJson(`qunyi-联调记录-${stamp()}.json`, ui.results);
  };
  root.querySelector('[data-act="export-md"]').onclick = () => {
    if (!ui.results) return toast('还没有检查结果', 'warn');
    downloadText(`qunyi-联调记录-${stamp()}.md`, toMarkdown(ui.results));
  };

  paint(root, ctx);
}

/* -------------------------------------------------------------- 执行检查 */
async function run(root, ctx) {
  if (ui.running) return;
  ui.running = true;
  const btn = root.querySelector('[data-act="run"]');
  btn.disabled = true;
  btn.textContent = '检查中…';

  const startedAt = new Date().toISOString();
  const rows = [];
  for (const ep of ENDPOINTS) {
    const row = { ...ep, at: new Date().toISOString() };
    const t0 = performance.now();
    try {
      const data = await ctx.api.call(ep.method, ep.path, ep.body, { timeout: 8000 });
      row.ms = Math.round(performance.now() - t0);
      row.status = 200;
      row.kind = 'ok';
      row.requestId = (data && data.request_id) || '';
      row.sample = sampleOf(data);
    } catch (err) {
      row.ms = Math.round(performance.now() - t0);
      row.status = err.status || 0;
      row.code = err.code || 'ERROR';
      row.message = err.message || '';
      row.requestId = err.requestId || '';
      row.kind = classify(err);
      if (err.payload && typeof err.payload === 'object') row.sample = sampleOf(err.payload);
    }
    rows.push(row);
    paint(root, ctx, rows); // 逐条刷新，长任务也能看到进度
  }

  ui.results = {
    checked_at: startedAt,
    finished_at: new Date().toISOString(),
    base: ctx.api.base,
    mode: (ctx.state.capabilities && ctx.state.capabilities.mode) || '',
    simulation: Boolean(ctx.state.capabilities && ctx.state.capabilities.simulation),
    user_agent: navigator.userAgent,
    rows,
  };
  saveLast(ui.results);
  ui.running = false;
  btn.disabled = false;
  btn.textContent = '重新检查';
  paint(root, ctx, rows);
  toast('检查完成，可导出联调记录', 'ok');
}

/** 把请求结果归类成用户能看懂的几种，而不是一律"失败" */
function classify(err) {
  if (err.status === 404) return 'missing';
  if (err.status === 401) return 'auth';
  if (err.status === 403) return 'forbidden';
  if (err.status === 400 || err.status === 415 || err.status === 422) return 'impl'; // 4xx 参数校验 = 接口存在
  if (err.status === 0 && err.code === 'TIMEOUT') return 'timeout';
  if (err.status === 0) return 'offline';
  if (err.status >= 500) return 'server';
  return 'bad';
}

const KIND_TEXT = {
  ok: '可用',
  impl: '存在（参数校验 4xx）',
  missing: '未实现 404',
  auth: '未登录 401',
  forbidden: '无权限 403',
  timeout: '超时',
  offline: '不可达',
  server: '后端错误 5xx',
  bad: '请求不合规',
  pending: '待检查',
};
const KIND_CLASS = {
  ok: 'badge--ok',
  impl: 'badge--ok',
  missing: 'badge--muted',
  auth: 'badge--warn',
  forbidden: 'badge--danger',
  timeout: 'badge--warn',
  offline: 'badge--danger',
  server: 'badge--danger',
  bad: 'badge--warn',
  pending: '',
};

/* ------------------------------------------------------------------ 绘制 */
function paint(root, ctx, rows) {
  const list = rows || (ui.results && ui.results.rows) || [];
  const summary = root.querySelector('#ig-summary');
  const table = root.querySelector('#ig-table');
  if (!summary || !table) return;

  if (!list.length) {
    summary.innerHTML = '';
    table.innerHTML = `<div class="empty"><span class="empty__icon">⇄</span>
      <div style="margin:6px 0">还没有检查结果</div>
      <div class="small">点右上「开始检查」，前端会真的去请求上面这些接口。</div></div>`;
    return;
  }

  const total = list.length;
  const usable = (r) => r.kind === 'ok' || r.kind === 'impl';
  const ok = list.filter(usable).length;
  const missing = list.filter((r) => r.kind === 'missing').length;
  const p0 = list.filter((r) => r.p === 'P0');
  const p0ok = p0.filter(usable).length;
  const checkedAt = (ui.results && ui.results.checked_at) || list[0].at;

  summary.innerHTML = `
    <div class="notice ${p0ok === p0.length ? 'notice--ok' : 'notice--info'}" style="margin-bottom:14px">
      <span class="notice__icon">${p0ok === p0.length ? '✓' : 'i'}</span>
      <div>
        <div><strong>P0 主流程接口 ${p0ok}/${p0.length} 可用</strong>；全部接口 ${ok}/${total} 可用，${missing} 个尚未实现。</div>
        <div class="small muted" style="margin-top:4px">检查时间 ${esc(fmtTime(checkedAt, true))} ·
          地址 <code>${esc((ui.results && ui.results.base) || ctx.api.base)}</code> ·
          模式 <code>mode=${esc((ui.results && ui.results.mode) || '?')}</code>
          ${ui.results && ui.results.simulation ? '（模拟适配器）' : ''}</div>
        <div class="small muted" style="margin-top:6px">这份记录可以直接导出交给成员 D / B；
          它只说明<strong>接口通不通</strong>，不等于业务验收通过。</div>
      </div>
    </div>`;

  table.innerHTML = `
    <section class="card">
      <div class="card__head"><h2>接口明细</h2><span class="badge">${list.length} 项</span></div>
      <div class="table-wrap">
        <table class="table">
          <thead><tr>
            <th style="width:56px">级别</th><th style="width:64px">方法</th><th>接口</th>
            <th style="width:150px">结果</th><th style="width:80px">耗时</th><th>说明</th>
          </tr></thead>
          <tbody>
            ${list
              .map(
                (r) => `<tr>
                  <td><span class="badge ${r.p === 'P0' ? 'badge--info' : ''}">${esc(r.p)}</span></td>
                  <td class="mono small">${esc(r.method)}</td>
                  <td>
                    <div class="mono small">${esc(r.path)}</div>
                    <div class="small muted">${esc(r.name)}</div>
                  </td>
                  <td>
                    <span class="badge ${KIND_CLASS[r.kind] || ''}">${esc(KIND_TEXT[r.kind] || r.kind)}</span>
                    ${r.status ? `<div class="small muted">HTTP ${esc(String(r.status))}${r.code ? ' · ' + esc(r.code) : ''}</div>` : ''}
                  </td>
                  <td class="small">${r.ms != null ? esc(String(r.ms)) + ' ms' : '—'}</td>
                  <td>
                    <div class="small">${esc(r.why || '')}</div>
                    ${r.message ? `<div class="small" style="color:var(--danger)">${esc(r.message)}</div>` : ''}
                    ${r.requestId ? `<div class="small mono muted">request_id: ${esc(r.requestId)}</div>` : ''}
                    ${r.sample ? `<details><summary class="small muted">响应片段</summary>
                      <pre class="code-block">${esc(r.sample)}</pre></details>` : ''}
                  </td>
                </tr>`,
              )
              .join('')}
          </tbody>
        </table>
      </div>
    </section>`;
}

/* ---------------------------------------------------------------- 工具 */
function sampleOf(data) {
  try {
    const s = JSON.stringify(data);
    return s.length > 600 ? s.slice(0, 600) + '…' : s;
  } catch {
    return '';
  }
}

function stamp() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}`;
}

function saveLast(data) {
  try {
    localStorage.setItem(LS_LAST, JSON.stringify(data));
  } catch {
    /* 存不下就算了，页面里还能看 */
  }
}
function loadLast() {
  try {
    const raw = localStorage.getItem(LS_LAST);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function toMarkdown(res) {
  const lines = [
    '# 群忆 · 接口联调记录',
    '',
    `- 检查时间：${res.checked_at}`,
    `- 后端地址：\`${res.base}\``,
    `- 后端模式：\`mode=${res.mode}\`${res.simulation ? '（模拟适配器，非真实模型）' : ''}`,
    `- 浏览器：${res.user_agent}`,
    '',
    '| 级别 | 方法 | 接口 | 结果 | HTTP | 耗时 | request_id |',
    '| --- | --- | --- | --- | --- | --- | --- |',
    ...res.rows.map(
      (r) =>
        `| ${r.p} | ${r.method} | \`${r.path}\` | ${KIND_TEXT[r.kind] || r.kind} | ${r.status || '-'} | ${r.ms != null ? r.ms + ' ms' : '-'} | ${r.requestId || '-'} |`,
    ),
    '',
    '> 说明：本记录只证明接口可达与返回状态，不代表业务验收通过；' +
      '"未实现 404"表示后端尚未提供该接口，前端已做降级。',
  ];
  return lines.join('\n');
}

function downloadText(filename, text) {
  const blob = new Blob([text], { type: 'text/markdown;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
