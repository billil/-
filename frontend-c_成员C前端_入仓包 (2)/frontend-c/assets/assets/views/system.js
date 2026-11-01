/* 系统状态：能力、健康检查、请求日志、接口约定 */
import { esc, fmtTime, toast } from '../util.js';

const ERRORS = [
  ['401', 'UNAUTHORIZED', '令牌缺失或无效'],
  ['404', 'NOT_FOUND', '对象不存在，或已因修订/删除失效'],
  ['409', 'IDEMPOTENCY_CONFLICT', '同一幂等键提交了不同内容'],
  ['413', 'BODY_TOO_LARGE', '请求体超过 1 MiB'],
  ['415', 'JSON_REQUIRED', 'Content-Type 不是 application/json'],
  ['502', 'MODEL_* / UNSUPPORTED_EVIDENCE', '模型越界或证据不支持该字段'],
  ['503', 'MODEL_NOT_CONFIGURED', 'live 模式但未配置模型'],
  ['504', 'MODEL_TIMEOUT / QUERY_DEADLINE', '模型超时或超过总时限'],
];

export async function render(root, ctx, params = {}) {
  root.innerHTML = `<div class="empty"><span class="empty__icon">◌</span>正在读取后端能力…</div>`;

  let cap = ctx.state.capabilities;
  let health = null;
  let healthMs = null;
  try {
    const t0 = performance.now();
    health = await ctx.api.health();
    healthMs = Math.round(performance.now() - t0);
    if (!cap) {
      cap = await ctx.api.capabilities();
      ctx.state.capabilities = cap;
    }
  } catch (err) {
    root.innerHTML = `<div class="notice notice--danger"><span class="notice__icon">✕</span>
      <div><strong>${esc(err.code)}</strong>：${esc(err.message)}
      ${err.hint ? `<div class="small" style="margin-top:4px">${esc(err.hint)}</div>` : ''}</div></div>`;
    return;
  }

  const cov = ctx.state.coverage;
  const reqId = params.req || '';

  // 「探测式接入」：A 方案新增的五个接口，后端有就绪就有，没有就 404 降级。
  // 这里是状态页的职责所在——把前端对后端的依赖矩阵如实摆出来。
  const probes = await Promise.all([
    ctx.api.me().catch((e) => ({ available: false, reason: 'ERROR', error: e })),
    ctx.api.groups().catch((e) => ({ available: false, reason: 'ERROR', error: e })),
    ctx.api.subscriptions().catch((e) => ({ available: false, reason: 'ERROR', error: e })),
    ctx.api.notifications({ limit: 1 }).catch((e) => ({ available: false, reason: 'ERROR', error: e })),
    ctx.api.syncStatus().catch((e) => ({ available: false, reason: 'ERROR', error: e })),
  ]);
  const optionalIfaces = [
    ['GET /v1/me', '身份（当前令牌对应的空间）', probes[0]],
    ['GET /v1/groups', '已接入群列表（群管理页）', probes[1]],
    ['GET /v1/subscriptions', '关注关系（群管理页）', probes[2]],
    ['GET /v1/notifications', '服务端通知流（通知中心）', probes[3]],
    ['GET /v1/sync/status', 'QQ 接入同步状态', probes[4]],
  ];

  root.innerHTML = `
    <div class="page-head">
      <div>
        <h2>系统状态</h2>
        <p>这里只展示后端<strong>自己报告</strong>的状态。模拟模式永远显示 <code>simulation=true</code>，
        不会把 mock 结果说成真实模型效果。</p>
      </div>
      <span class="spacer"></span>
      <button class="btn btn--sm" data-act="recheck">重新检测</button>
    </div>

    ${renderAiStatus(cap, readLastQuery())}

    <div class="grid grid--2" style="align-items:start">
      <section class="card">
        <div class="card__head">
          <h2>连接</h2>
          <span class="spacer"></span>
          <span class="badge ${health && health.ok ? 'badge--ok' : 'badge--danger'}">
            ${health && health.ok ? '健康 · ' + healthMs + ' ms' : '异常'}</span>
        </div>
        <div class="card__body">
          <dl class="kv-list">
            <dt>前端代理地址</dt><dd class="mono">${esc(ctx.api.base)}</dd>
            <dt>后端版本</dt><dd class="mono">${esc(health.version || '—')}</dd>
            <dt>健康检查</dt><dd class="mono">${esc(health.request_id || '—')}</dd>
            <dt>令牌状态</dt><dd>${ctx.api.configured ? '已配置（仅存于本机浏览器）' : '<span style="color:var(--danger)">未配置</span>'}</dd>
          </dl>
        </div>
      </section>

      <section class="card">
        <div class="card__head"><h2>能力 GET /v1/capabilities</h2></div>
        <div class="card__body">
          <dl class="kv-list">
            <dt>运行模式 mode</dt><dd><span class="badge ${cap.simulation ? 'badge--warn' : 'badge--ok'}">${esc(cap.mode)}</span>
              ${cap.simulation ? '<span class="small muted">模拟适配器</span>' : '<span class="small muted">真实模型</span>'}</dd>
            <dt>模型已配置</dt><dd>${cap.model_configured ? '是' : '否'}</dd>
            <dt>检索器</dt><dd class="mono">${esc(cap.search || '—')}</dd>
            <dt>真实语义模型</dt><dd>${cap.true_semantic_model ? '是' : '否（本地 n-gram / TF-IDF，不是向量检索）'}</dd>
            <dt>联名版已认证</dt><dd>${cap.workbuddy_certified ? '是' : '否（赛事平台资格仍待核实）'}</dd>
            <dt>单次最多调用</dt><dd>${esc(String(cap.max_calls ?? '—'))} 次</dd>
          </dl>
        </div>
      </section>
    </div>

    <div class="grid grid--2" style="margin-top:14px;align-items:start">
      <section class="card">
        <div class="card__head"><h2>请求日志</h2><span class="sub">GET /v1/requests/{id}</span></div>
        <div class="card__body">
          <div class="row">
            <input class="input mono" id="req-id" placeholder="req_…（可从任意结果卡的 ⧉ 复制）" value="${esc(reqId)}" />
            <button class="btn" id="req-go">查询</button>
          </div>
          <div id="req-out" style="margin-top:12px"></div>
          <div class="small muted" style="margin-top:10px">日志不保存请求正文、令牌或模型响应原文，只记路由、状态、耗时与错误类型。</div>
        </div>
      </section>

      <section class="card">
        <div class="card__head"><h2>覆盖范围快照</h2></div>
        <div class="card__body">
          ${cov
            ? `<dl class="kv-list">
                <dt>已导入</dt><dd>${cov.imported_count} 条（无发送时间 ${cov.unknown_time_count} 条）</dd>
                <dt>已索引</dt><dd>${cov.indexed_count} 条</dd>
                <dt>发送时间</dt><dd>${cov.sent_start ? esc(fmtTime(cov.sent_start)) + ' → ' + esc(fmtTime(cov.sent_end)) : '—'}</dd>
                <dt>群标签</dt><dd>${esc((cov.groups || []).join('、') || '—')}</dd>
              </dl>`
            : '<div class="small muted">尚未读取</div>'}
        </div>
      </section>
    </div>

    <div class="grid grid--2" style="margin-top:14px;align-items:start">
      <section class="card">
        <div class="card__head">
          <h2>可选接口就绪矩阵</h2>
          <span class="sub">探测式接入 · 后端上线即自动启用</span>
        </div>
        <div class="table-wrap">
          <table class="table">
            <thead><tr><th>接口</th><th>用途</th><th style="width:150px">状态</th></tr></thead>
            <tbody>
              ${optionalIfaces
                .map(
                  ([name, use, r]) => `<tr>
                    <td class="mono small">${esc(name)}</td>
                    <td class="small">${esc(use)}</td>
                    <td>${
                      r.available
                        ? '<span class="badge badge--ok">已就绪</span>'
                        : r.reason === 'NOT_IMPLEMENTED'
                          ? '<span class="badge badge--warn">待后端实现（404）</span>'
                          : `<span class="badge badge--danger">请求失败：${esc((r.error && r.error.code) || 'ERROR')}</span>`
                    }</td>
                  </tr>`,
                )
                .join('')}
            </tbody>
          </table>
        </div>
        <div class="card__body small muted">
          这些是 A 成员方案里新增的依赖。前端按「探测 + 降级」处理：就绪就用真实数据；
          未就绪时对应页面显示说明并退回到已有能力（如通知中心退回本地增量轮询），绝不伪造。
        </div>
      </section>

      <section class="card">
        <div class="card__head"><h2>QQ 接入与同步</h2></div>
        <div class="card__body">
          ${
            probes[4].available
              ? '<div id="sync-detail">读取中…</div>'
              : `<div class="notice notice--warn"><span class="notice__icon">!</span>
                  <div>后端尚未提供 <code>GET /v1/sync/status</code>，无法报告 QQ 群接入的实时状态（五态：未配置 / 待授权 / 已授权 / 拉取中 / 异常）。
                  当前消息都来自<strong>手工导入</strong>，这一事实不受影响。</div></div>`
          }
          <dl class="kv-list" style="margin-top:12px">
            <dt>数据来源</dt><dd>${probes[4].available ? '同步状态接口' : '手工导入（当前唯一来源）'}</dd>
            <dt>涉及群标签</dt><dd>${esc((cov && cov.groups || []).join('、') || '—')}</dd>
            <dt>覆盖边界</dt><dd class="small">${esc((cov && cov.scope) || '仅覆盖当前令牌所属数据空间内已导入内容')}</dd>
          </dl>
        </div>
      </section>
    </div>

    <div class="grid grid--2" style="margin-top:14px;align-items:start">
      <section class="card">
        <div class="card__head"><h2>错误码约定</h2></div>
        <div class="table-wrap">
          <table class="table">
            <thead><tr><th style="width:64px">HTTP</th><th>code</th><th>含义</th></tr></thead>
            <tbody>${ERRORS.map(([s, c, m]) => `<tr><td class="mono">${s}</td><td class="mono small">${c}</td><td class="small">${m}</td></tr>`).join('')}</tbody>
          </table>
        </div>
      </section>

      <section class="card">
        <div class="card__head"><h2>前端必须遵守的接口约束</h2></div>
        <div class="card__body stack stack--sm small">
          <div class="notice notice--info"><span class="notice__icon">1</span><div>后端<strong>不带 CORS 头、不处理 OPTIONS</strong>，
            因此前端通过 <code>serve.py</code> 以同源 <code>/gw/*</code> 代理访问（<code>/api/*</code> 兼容保留），浏览器层面不存在跨域。</div></div>
          <div class="notice notice--info"><span class="notice__icon">2</span><div>除 <code>/health</code> 外都需要
            <code>Authorization: Bearer &lt;token&gt;</code>，令牌来自后端 <code>state/credentials.json</code>。</div></div>
          <div class="notice notice--warn"><span class="notice__icon">3</span><div>修订或删除任意一条消息，会让该空间<strong>全部会话、轮次、幂等批次</strong>失效；
            前端收到 404 必须新建会话，而不是重试。</div></div>
          <div class="notice notice--warn"><span class="notice__icon">4</span><div>后端是单进程全局锁，模型慢请求会阻塞其他请求，演示规模如此，前端要给出等待提示。</div></div>
        </div>
      </section>
    </div>
  `;

  const go = async () => {
    const id = root.querySelector('#req-id').value.trim();
    const out = root.querySelector('#req-out');
    if (!id) return toast('先填 request_id', 'warn');
    out.innerHTML = '<div class="skeleton" style="height:14px;width:50%"></div>';
    try {
      const row = await ctx.api.getRequest(id);
      out.innerHTML = `
        <dl class="kv-list">
          <dt>route</dt><dd class="mono">${esc(row.route || '—')}</dd>
          <dt>status</dt><dd class="mono">${esc(String(row.status ?? '—'))}</dd>
          <dt>耗时</dt><dd>${esc(String(row.duration_ms ?? '—'))} ms</dd>
          <dt>时间</dt><dd>${esc(fmtTime(row.created, true))}</dd>
          <dt>metadata</dt><dd><pre class="mono" style="margin:0;background:var(--surface-3);padding:8px;border-radius:8px;overflow:auto">${esc(JSON.stringify(row.metadata || {}, null, 2))}</pre></dd>
        </dl>`;
    } catch (err) {
      out.innerHTML = `<div class="notice notice--danger small"><span class="notice__icon">✕</span>
        <div><strong>${esc(err.code)}</strong>：${esc(err.message)}</div></div>`;
    }
  };
  root.querySelector('#req-go').onclick = go;
  root.querySelector('#req-id').addEventListener('keydown', (e) => { if (e.key === 'Enter') go(); });
  root.querySelector('[data-act="recheck"]').onclick = () => render(root, ctx, params);
  if (reqId) go();

  // 同步状态接口就绪时，展开五态详情
  if (probes[4].available) {
    const box = root.querySelector('#sync-detail');
    try {
      const st = probes[4].data || {};
      const state = String(st.state || st.status || 'unknown');
      const five = ['未配置', '待授权', '已授权', '拉取中', '异常'];
      const label = five.includes(state) ? state : state;
      box.innerHTML = `<dl class="kv-list">
        <dt>接入状态</dt><dd><span class="badge ${state === '异常' ? 'badge--danger' : state === '已授权' || state === '拉取中' ? 'badge--ok' : 'badge--warn'}">${esc(label)}</span></dd>
        <dt>详情</dt><dd class="small mono">${esc(JSON.stringify(st, null, 2))}</dd>
      </dl>`;
    } catch { box.innerHTML = '<div class="small muted">状态解析失败</div>'; }
  }
}

/* --------------------------------------------------------- AI 实际调用状态
 * 任务清单 P1-8：要区分「配置为真实模式」和「本次请求确实调用成功」。
 * 这里把两件事分开摆：
 *   1) 后端报告的模式（capabilities.mode / simulation）
 *   2) 最近一次检索响应里的 ai 字段（calls / actual_participation / succeeded / error_code）
 * 后端没返回 ai 字段时，就明说"无法判断"，不猜。
 */
function readLastQuery() {
  try {
    const raw = localStorage.getItem('qunyi.lastQuery');
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function renderAiStatus(cap, last) {
  const configuredMock = Boolean(cap && (cap.mode === 'mock' || cap.simulation));
  const ai = last && last.ai;
  let tone = 'info';
  let verdict = '';
  let detail = '';

  if (!last) {
    tone = 'muted';
    verdict = '还没有发起过检索';
    detail = '去「智能查找」问一个问题，这里会显示那一次请求真实的模型参与情况。';
  } else if (configuredMock) {
    tone = 'warn';
    verdict = '本次结果是模拟出来的，没有调用真实大模型';
    detail = `后端当前 mode=${cap.mode || 'mock'}${cap.simulation ? '（simulation=true）' : ''}，检索靠词面匹配 + 规则抽取。`;
  } else if (ai && Number(ai.calls) > 0 && ai.actual_participation && ai.succeeded) {
    tone = 'ok';
    verdict = '本次真实调用了模型，并通过证据校验';
    detail = `调用 ${ai.calls} 次 · mode=${ai.mode || cap.mode}`;
  } else if (ai && ai.error_code) {
    tone = 'warn';
    verdict = '配置为真实模式，但本次调用失败';
    detail = `错误码 ${ai.error_code}${ai.calls ? ' · 调用 ' + ai.calls + ' 次' : ''}。这属于调用失败，不是"没有相关通知"。`;
  } else if (!ai) {
    tone = 'warn';
    verdict = '后端没有返回 ai 字段，无法判断本次是否真的调用了模型';
    detail = '不要据此宣称"已接入真实模型"；需要成员 D 在响应里补 ai 字段。';
  } else {
    tone = 'warn';
    verdict = '本次未实际参与生成（可能证据不足直接返回）';
    detail = `calls=${ai.calls ?? 0} · actual_participation=${ai.actual_participation ? 'true' : 'false'}`;
  }

  return `
    <section class="card" style="margin-bottom:14px">
      <div class="card__head">
        <h2>AI 实际调用状态</h2>
        <span class="spacer"></span>
        <span class="badge badge--${tone}">${esc(configuredMock ? '模拟' : cap && cap.mode ? 'live' : '未知')}</span>
      </div>
      <div class="card__body">
        <div class="notice notice--${tone}" style="margin-bottom:10px"><span class="notice__icon">${tone === 'ok' ? '✓' : 'i'}</span>
          <div><strong>${esc(verdict)}</strong><div class="small" style="margin-top:4px">${esc(detail)}</div></div></div>
        <dl class="kv-list">
          <dt>后端配置</dt><dd class="mono">mode=${esc((cap && cap.mode) || '?')} · simulation=${cap && cap.simulation ? 'true' : 'false'}</dd>
          <dt>最近一次提问</dt><dd>${last ? esc(last.question || '—') : '—'}</dd>
          <dt>请求时间</dt><dd>${last ? esc(fmtTime(last.at, true)) : '—'}</dd>
          <dt>返回状态</dt><dd class="mono">${last ? esc(last.status || '—') : '—'}</dd>
          <dt>request_id</dt><dd class="mono">${last && last.request_id ? esc(last.request_id) : '—'}</dd>
          <dt>ai 字段</dt><dd class="mono small">${ai ? esc(JSON.stringify(ai)) : '后端未返回'}</dd>
        </dl>
        <div class="small muted" style="margin-top:8px">「配置为 live」只说明后端填了模型配置；
          是否真的调用成功，以每次响应的 <code>ai</code> 字段为准。</div>
      </div>
    </section>`;
}
