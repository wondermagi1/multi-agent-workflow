const phases = [
  ['S0', '想法提出', 'N01–N03'], ['S1', '多体讨论', 'N04–N11'],
  ['S2', '建设方案', 'N12'], ['S3', '多方评价', 'N13'],
  ['S4', '需求规格', 'N14'], ['S5', '需求签收', 'N15'], ['DEV', '实现与交付', 'DEV'],
];

const views = {
  workflow: '工作流', discussion: '群体讨论', trace: '运行记录', delivery: '需求与交付',
};

const statusLabels = {
  ready: '待推进', running: '进行中', waiting_human: '等待人工', blocked: '待处理',
  paused: '已暂停', completed: '已完成', failed: '失败', cancelled: '已取消',
  pending: '未开始', planned: '未开始', current: '进行中', active: '进行中',
  done: '已完成', success: '已完成', passed: '通过', open: '待处理', resolved: '已处理',
  submitted: '已提交', sealed: '已封存', skipped: '已跳过', not_started: '未开始',
};

const nodeTitles = {
  N01: '整理想法', N02: '定题确认', N03: '派单与简报冻结', N04: '独立提案',
  N05: '观点归类', N06: '交叉质询', N07: '反例与风险', N08: '分歧分流',
  N09: '价值取舍', N10: '归纳候选方向', N11: '收敛门禁', N12: '编写建设方案',
  N13: '方案评价', N14: '形成需求规格', N15: '最终需求签收', 'DEV-C': '编码',
  'DEV-T': '测试', 'DEV-R': '评审', 'DEV-A': '交付验收',
};

const roleNames = {
  product: '产品 Agent', user: '用户 Agent', tech: '技术 Agent', technical: '技术 Agent',
  technology: '技术 Agent',
  challenger: '质疑 Agent', recorder: '记录 Agent', organizer: '编排 Agent', host: '主持人',
  coding: '编码 Agent', testing: '测试 Agent', review: '评审 Agent', reviewer: '评审 Agent',
  evidence: '证据 Agent', evaluation: '评价 Agent', requirements: '需求 Agent',
};

const actions = {
  advance: '推进下一步', approve: '确认当前事项', return: '退回修订',
  resolve_objection: '提交异议处置', pause: '暂停运行', resume: '继续运行', retry: '重新尝试', cancel: '结束运行',
  reject: '拒绝通过', request_changes: '请求修订', deliver: '确认交付',
};

const state = {
  info: null, runs: [], run: null, replay: null, selectedEvent: null, eventFilter: 'all',
  view: 'workflow', busy: false, loaded: false, error: '', action: null, actionVersion: null, toastTimer: null,
};

const root = document.querySelector('#main');
const navigation = document.querySelector('#navigation');
const runList = document.querySelector('#run-list');
const newRunDialog = document.querySelector('#new-run');
const actionDialog = document.querySelector('#action-dialog');
const actionForm = document.querySelector('#action-form');
const localTime = new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' });
const number = new Intl.NumberFormat('zh-CN');

const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
})[char]);

function array(value) { return Array.isArray(value) ? value : []; }
function statusText(status) { return statusLabels[status] || String(status || '—'); }
function phaseName(stage) { return phases.find(([id]) => id === stage)?.[1] || stage || '—'; }
function nodeName(node) { return nodeTitles[node] || node || '—'; }
function roleName(role) { return roleNames[role] || role || '未标记角色'; }
const tierNames = { economy: '轻量模型', balanced: '常规模型', strong: '推理模型' };
const difficultyNames = { low: '简单', medium: '中等', high: '复杂' };
function date(value) {
  if (!value) return '—';
  const time = new Date(value);
  return Number.isNaN(time.getTime()) ? String(value) : localTime.format(time);
}

function statusTone(status) {
  if (['done', 'completed', 'success', 'passed', 'resolved', 'submitted'].includes(status)) return 'good';
  if (['waiting_human', 'blocked', 'paused', 'open'].includes(status)) return 'warn';
  if (['failed', 'cancelled'].includes(status)) return 'bad';
  return ['running', 'current', 'active'].includes(status) ? 'info' : '';
}

function badge(content, tone = '') { return `<span class="badge ${tone}">${escape(content)}</span>`; }
function statusBadge(status) { return badge(statusText(status), statusTone(status)); }
function blank(message) { return `<div class="empty"><p>${escape(message)}</p></div>`; }
function panel(title, body, suffix = '') {
  return `<section class="panel"><header class="panel-head"><h2>${escape(title)}</h2>${suffix}</header>${body}</section>`;
}
function details(entries) {
  return `<dl class="detail-grid">${entries.map(([label, value]) => `<dt>${escape(label)}</dt><dd>${escape(value ?? '—')}</dd>`).join('')}</dl>`;
}
function structured(value) { return `<pre>${escape(JSON.stringify(value, null, 2))}</pre>`; }
function showObject(value) {
  if (typeof value === 'string') return `<p class="artifact-content">${escape(value)}</p>`;
  return value === undefined || value === null ? blank('暂无内容。') : structured(value);
}

function eventDetails(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return showObject(value);
  return `<dl class="detail-grid">${Object.entries(value).map(([key, field]) => {
    if (field && typeof field === 'object') return `<dt>${escape(key)}</dt><dd>${structured(field)}</dd>`;
    return `<dt>${escape(key)}</dt><dd>${escape(field ?? '—')}</dd>`;
  }).join('')}</dl>`;
}

function actionSet(run) {
  return new Set(array(run?.allowed_actions).map(action => typeof action === 'string' ? action : action?.action));
}

function unwrap(payload) { return payload?.run || payload; }
function apiRun(id) { return `/api/runs/${encodeURIComponent(id)}`; }

async function api(path, options = {}) {
  const response = await fetch(path, { cache: 'no-store', ...options, headers: {
    Accept: 'application/json', ...(options.body && { 'Content-Type': 'application/json' }), ...options.headers,
  } });
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const message = payload?.error?.message || payload?.message || `请求未完成（HTTP ${response.status}）`;
    throw Object.assign(new Error(message), { status: response.status, code: payload?.error?.code });
  }
  if (payload === null) throw new Error('服务没有返回可读取的记录。');
  return payload;
}

function toast(message) {
  const box = document.querySelector('#toast');
  box.textContent = message;
  box.hidden = false;
  window.clearTimeout(state.toastTimer);
  state.toastTimer = window.setTimeout(() => { box.hidden = true; }, 3800);
}

function readView() {
  const value = window.location.hash.slice(1);
  return Object.hasOwn(views, value) ? value : 'workflow';
}

function switchView(name) {
  if (!Object.hasOwn(views, name)) return;
  state.view = name;
  if (window.location.hash !== `#${name}`) window.history.replaceState(null, '', `#${name}`);
  render();
  root.focus({ preventScroll: true });
  window.scrollTo({ top: 0, behavior: 'instant' });
}

function syncRun(run) {
  state.run = run;
  const existing = state.runs.findIndex(item => item.id === run.id);
  if (existing >= 0) state.runs.splice(existing, 1, run);
  else state.runs.unshift(run);
  state.runs.sort((a, b) => String(b.updated_at || b.created_at || '').localeCompare(String(a.updated_at || a.created_at || '')));
}

function showProvider() {
  const label = document.querySelector('#provider-label');
  const provider = state.info?.provider;
  const name = typeof provider === 'string' ? provider : provider?.name || provider?.id;
  label.classList.toggle('offline', !name && state.loaded);
  const text = /mock|simulat|demo/i.test(name || '') ? '模拟模式' : name ? name : state.loaded ? '服务未连接' : '连接中';
  label.innerHTML = `<i aria-hidden="true"></i>${escape(text)}`;
}

function renderSide() {
  for (const button of navigation.querySelectorAll('button[data-view]')) {
    if (button.dataset.view === state.view) button.setAttribute('aria-current', 'page');
    else button.removeAttribute('aria-current');
  }
  runList.innerHTML = state.runs.length ? state.runs.slice(0, 40).map(run => {
    const current = run.id === state.run?.id;
    return `<button class="run-entry" data-run="${escape(run.id)}" aria-current="${current}" title="${escape(run.title || run.id)}" ${state.busy ? 'disabled' : ''}>`
      + `<span class="entry-dot ${statusTone(run.status) === 'good' ? 'completed' : statusTone(run.status) === 'warn' ? 'waiting_human' : statusTone(run.status) === 'bad' ? 'failed' : ''}" aria-hidden="true"></span><span class="entry-title">${escape(run.title || run.id)}</span></button>`;
  }).join('') : `<p class="sidebar-empty">${state.loaded ? '暂无运行' : '正在读取运行记录'}</p>`;
  document.querySelector('#breadcrumb').textContent = views[state.view];
  showProvider();
}

function runTitle(run, title = run.title || '工作流总览') {
  return `<header class="title-bar"><div><div class="eyebrow">${state.view === 'workflow' ? '运行概览' : escape(run.title || '当前运行')}</div><h1>${escape(title)}</h1></div>`
    + `<div class="title-meta">${statusBadge(run.status)}${badge(`快照 v${run.version ?? '—'}`)}</div></header>`;
}

function runForm() {
  return `<form class="start-form" data-create-form><label>运行名称 <span>选填</span><input name="title" maxlength="100" placeholder="为本次工作取个名称"></label>`
    + '<label>想法或需求 <span>必填</span><textarea name="idea" rows="4" maxlength="5000" required placeholder="写下目标、使用者以及已知限制。"></textarea></label>'
    + `<p class="form-error" data-form-error role="alert" hidden></p><footer><button class="button" type="submit" ${state.busy ? 'disabled' : ''}>${state.busy ? '正在创建…' : '创建运行 ↗'}</button></footer></form>`;
}

function home() {
  const plan = `<ul class="list-lines">${phases.map(([id, label, nodes]) => `<li><span class="id">${id}</span><strong>${label}</strong><span class="phase-label">${nodes}</span></li>`).join('')}</ul>`;
  return `<section class="hero"><div class="hero-copy"><p class="eyebrow">多智能体软件工程</p><h1>从想法到交付</h1>`
    + '<p>把想法交给工作台。从多角色独立提案、分歧处理到需求签收，沿每一次变更回查依据。</p>'
    + '<button class="button button-accent" data-start-focus type="button">录入需求 <span aria-hidden="true">↘</span></button></div>'
    + '<div class="orbit" aria-hidden="true"><i class="orbit-dot dot-a"></i><i class="orbit-dot dot-b"></i><i class="orbit-dot dot-c"></i><i class="orbit-dot dot-d"></i>'
    + '<span class="orbit-label one">独立提案</span><span class="orbit-label two">分歧收敛</span><span class="orbit-label three">需求签收</span></div></section>'
    + `<div class="entry-grid">${panel('开始一条工作流', runForm())}${panel('流程结构', plan)}</div>`;
}

function allPhaseCards(run) {
  const known = new Map(array(run.phases).map(phase => [phase.id, phase]));
  return `<section class="phase-panel panel" aria-label="阶段轨迹"><header class="panel-head"><h2>阶段轨迹</h2>${badge(`${phaseName(run.stage)} · ${run.node || '—'}`, 'info')}</header><div class="phase-rail">${phases.map(([id, label, nodes]) => {
    const record = known.get(id);
    const current = id === run.stage;
    const done = ['done','completed','passed','complete','success'].includes(record?.status);
    return `<div class="phase-card ${current ? 'current' : done ? 'done' : ''}" ${current ? 'aria-current="step"' : ''}>`
      + `<span class="phase-id">${id} · ${nodes}</span><strong>${escape(record?.label || label)}</strong><small>${escape(current ? statusText(run.status) : done ? '已通过' : record?.status ? statusText(record.status) : '未开始')}</small></div>`;
  }).join('')}</div></section>`;
}

function waitingBox(run) {
  if (run.waiting) return `<div class="status-note warn"><strong>${escape(run.waiting.id || '人工待办')} · ${escape(run.waiting.title || '待人工处理')}</strong>${escape(run.waiting.description || '')}</div>`;
  if (run.status === 'blocked') return '<div class="status-note warn">检查门禁和未处理异议，处置后再推进。</div>';
  if (run.status === 'failed') return '<div class="status-note bad">当前步骤执行失败。可在运行记录中查看错误与尝试详情。</div>';
  if (run.status === 'paused') return '<div class="status-note">工作流已暂停。继续后从当前节点推进。</div>';
  if (run.status === 'completed') return '<div class="status-note">工作流已完成。确认记录与产物可在需求与交付页查看。</div>';
  return '';
}

function actionButtons(run) {
  if (state.replay || run.replay) return '';
  const allowed = [...actionSet(run)].filter(key => Object.hasOwn(actions, key));
  if (!allowed.length) return '';
  return `<div class="action-row">${allowed.map(action => {
    const tone = ['advance','approve','resume','retry'].includes(action) ? '' : ['cancel','reject'].includes(action) ? 'button-danger' : 'button-secondary';
    return `<button class="button ${tone}" data-action="${action}" type="button" ${state.busy ? 'disabled' : ''}>${escape(state.busy && action === state.action ? '处理中…' : actions[action])}</button>`;
  }).join('')}</div>`;
}

function modelTiers() {
  const tiers = state.info?.model_tiers;
  const entries = Array.isArray(tiers) ? tiers.map((item, index) => [item?.tier || item?.id || String(index + 1), item]) : Object.entries(tiers || {});
  if (!entries.length) return '';
  return `<div class="model-list section-gap">${entries.map(([tier, item]) => {
    const model = typeof item === 'string' ? item : item?.model || item?.id || item?.name || '—';
    return `<div class="model-item"><small>${escape(tierNames[tier] || tier)}</small><strong>${escape(model)}</strong></div>`;
  }).join('')}</div>`;
}

function usagePanel(run) {
  const usage = run.usage || {};
  const tokens = usage.total_tokens ?? (usage.input_tokens == null && usage.output_tokens == null ? null : Number(usage.input_tokens || 0) + Number(usage.output_tokens || 0));
  const estimate = usage.estimated_cost_usd;
  const metric = (label, value) => `<div class="metric"><small>${escape(label)}</small><strong>${escape(value)}</strong></div>`;
  return panel('开销与模型', `<div class="metrics">${metric('模型调用', usage.calls == null ? '—' : number.format(usage.calls))}${metric('总 Token', tokens == null ? '—' : number.format(tokens))}${metric('输入 / 输出', usage.input_tokens == null && usage.output_tokens == null ? '—' : `${number.format(usage.input_tokens || 0)} / ${number.format(usage.output_tokens || 0)}`)}${metric('费用估算', estimate == null ? '—' : `$${Number(estimate).toFixed(4)}`)}</div>`
    + modelTiers());
}

function newestEvents(run, count) { return [...array(run.events)].sort((a,b) => Number(b.seq) - Number(a.seq)).slice(0, count); }

function workPlan(run) {
  const tasks = array(run.work_plan);
  if (!tasks.length) return panel('任务拆解与编排', blank('完成议题整理后显示任务、前置关系和模型分层。'));
  const historyQuery = state.replay ? `?seq=${encodeURIComponent(state.replay.seq)}` : '';
  const exportLink = `<a class="text-button" href="${apiRun(run.id)}/workflow${historyQuery}" download="workflow.json">导出工作流 ↓</a>`;
  const taskCards = tasks.map(task => {
    const dependencies = array(task.depends_on);
    const model = state.info?.model_tiers?.[task.tier];
    return `<li class="task-card"><div class="row-meta">${badge(task.id)}${badge(difficultyNames[task.difficulty] || task.difficulty || '待估难度', task.difficulty === 'high' ? 'warn' : '')}</div>`
      + `<h3>${escape(task.title || task.id)}</h3><p>${escape(roleName(task.role))} · ${escape(tierNames[task.tier] || task.tier || '待路由')}${typeof model === 'string' ? ` · ${escape(model)}` : ''}</p>`
      + `<div class="task-dependencies"><span>前置任务</span>${dependencies.length ? dependencies.map(id => badge(id, 'info')).join('') : '<span>可独立开始</span>'}</div></li>`;
  }).join('');
  return panel('任务拆解与编排', `<ol class="task-grid">${taskCards}</ol>`, exportLink);
}

function runOverview(run) {
  const recent = newestEvents(run, 5);
  const attempts = [...array(run.attempts)].reverse().slice(0, 5);
  return runTitle(run) + allPhaseCards(run)
    + `<div class="run-grid"><div class="stack">${panel('当前执行', details([['阶段', phaseName(run.stage)], ['节点', `${run.node || ''} ${run.node_label || nodeName(run.node)}`], ['运行编号', run.id], ['更新时间', date(run.updated_at)]]) + waitingBox(run) + actionButtons(run))}`
    + workPlan(run)
    + `${panel('输入需求', `<p class="idea">${escape(run.idea || '暂无需求文本。')}</p>`)}`
    + `${panel('最近事件', recent.length ? `<ul class="run-logs">${recent.map(e => `<li><div><strong>${escape(e.title || e.type)}</strong><small>${escape(date(e.timestamp))}</small></div><small>${escape(e.node || e.stage || '运行')}</small></li>`).join('')}</ul>` : blank('暂无执行事件。'), '<button class="text-button" data-go="trace" type="button">打开运行记录 ↗</button>')}</div>`
    + `<aside class="stack">${usagePanel(run)}${panel('执行尝试', attempts.length ? `<ul class="run-logs">${attempts.map(attempt => `<li><div><strong>${escape(roleName(attempt.role))}</strong>${statusBadge(attempt.status)}</div><small>${escape(attempt.node || '')}${attempt.model ? ` · ${escape(attempt.model)}` : ''}</small></li>`).join('')}</ul>` : blank('模型及工具启动后显示尝试。'))}</aside></div>`;
}

function gateList(gates) {
  if (!gates.length) return blank('门禁计算后显示逐项结果与依据。');
  return gates.slice().reverse().map(gate => {
    const title = `${gate.id || '门禁'}${gate.node ? ` · ${gate.node}` : ''}`;
    return `<details class="artifact" ${gate === gates.at(-1) ? 'open' : ''}><summary>${escape(title)} ${gate.passed === true ? badge('通过', 'good') : gate.passed === false ? badge('未通过', 'warn') : badge('待检查')}</summary>`
      + (gate.seat_count ? details([['有效赞同', `${gate.support_count} / ${gate.seat_count} 席`], ['目标比例', `${Math.round(gate.threshold * 100)}%`]]) : '')
      + `<ul class="check-list">${array(gate.checks).map(check => `<li><span>${escape(check.label || check.name || '检查项')}</span>${badge(check.passed === true ? '通过' : check.passed === false ? '未通过' : '待核验', check.passed === true ? 'good' : check.passed === false ? 'warn' : '')}</li>`).join('')}</ul></details>`;
  }).join('');
}

function discussionMap(run) {
  const proposals = array(run.proposals);
  const attempts = array(run.attempts).filter(attempt => attempt.node === 'N04');
  const seats = ['product', 'technology', 'evidence', 'challenger'];
  const count = array(run.claims).length;
  return `<div class="discussion-map" aria-label="议题发散与观点归类"><div class="map-root">同一议题</div>`
    + `<div class="map-seats">${seats.map(role => {
      const proposal = proposals.find(item => item.role === role);
      const attempt = attempts.findLast(item => item.role === role);
      const status = proposal ? '已公开' : attempt ? statusText(attempt.status) : '待派发';
      return `<div class="map-seat ${proposal ? 'shared' : ''}"><strong>${escape(roleName(role))}</strong><small>${escape(status)}</small></div>`;
    }).join('')}</div>`
    + `<div class="map-root ${count ? 'shared' : ''}">${count ? `归纳为 ${number.format(count)} 条观点` : '汇总观点'}</div></div>`;
}

function runDiscussion(run) {
  const rounds = [['发散','独立提案','N04'], ['归类','观点归类','N05'], ['论证','质询与分流','N06–N09'], ['收敛','收敛与门禁','N10–N11']];
  const activeRound = run.stage === 'S1' ? ({ N04: 0, N05: 1, N06: 2, N07: 2, N08: 2, N09: 2, N10: 3, N11: 3 })[run.node] : -1;
  const proposals = array(run.proposals);
  const claims = array(run.claims);
  const stances = array(run.stances);
  const objections = array(run.objections);
  const gates = array(run.gates).filter(gate => gate.id === 'G1' || gate.node === 'N11');
  const canResolve = actionSet(run).has('resolve_objection') && !state.replay;
  const proposalBody = proposals.length ? `<div class="proposal-grid">${proposals.map(proposal => `<article class="proposal-card">${badge(roleName(proposal.role), 'info')}`
    + `<h3>${escape(proposal.title || proposal.id || '提案')}</h3><p>${escape(proposal.summary || '')}</p>${proposal.rationale ? `<p><strong>依据：</strong>${escape(proposal.rationale)}</p>` : ''}${proposal.risk ? `<p><strong>风险：</strong>${escape(proposal.risk)}</p>` : ''}</article>`).join('')}</div>` : blank('必需席位提交齐备后统一显示提案。');
  return runTitle(run, '群体讨论') + `<div class="rounds">${rounds.map(([id, label, nodes], i) => `<div class="round ${activeRound === i ? 'current' : ''}" ${activeRound === i ? 'aria-current="step"' : ''}><span class="phase-id">${id}</span><strong>${label}</strong><small>${nodes}</small></div>`).join('')}</div>`
    + `<div class="run-grid"><div class="stack">${panel('独立提案', discussionMap(run) + proposalBody, badge(`${number.format(proposals.length)} 份已公开`))}`
    + `${panel('观点与证据', claims.length ? `<ul class="body-list">${claims.map(claim => `<li><div class="row-meta">${badge(claim.id)}</div><strong>${escape(claim.text || claim.summary || '观点')}</strong>${array(claim.source_ids).length ? `<p class="muted-text">来源 ${escape(claim.source_ids.join('、'))}</p>` : ''}</li>`).join('')}</ul>` : blank('记录 Agent 归类后显示观点及其提案出处。'))}`
    + `${panel('异议与分流', objections.length ? `<ul class="body-list">${objections.map(item => `<li><div class="row-meta">${badge(item.id)}${badge(({ fact:'事实', value:'价值', tradeoff:'权衡' })[item.type] || item.type || '异议')}${statusBadge(item.status)}${item.blocking && item.status !== 'resolved' ? badge('阻塞', 'warn') : ''}</div>`
      + `<strong>${escape(item.summary || '待处理异议')}</strong>${item.claim_id ? `<p class="muted-text">关联观点 ${escape(item.claim_id)}</p>` : ''}${item.resolution ? `<p class="artifact-content">${escape(item.resolution)}</p>` : ''}${canResolve && resolvable(item) ? `<button class="text-button" data-action="resolve_objection" data-objection="${escape(item.id)}" type="button" ${state.busy ? 'disabled' : ''}>处理这条异议 ↗</button>` : ''}</li>`).join('')}</ul>` : blank('暂无已登记的异议。'))}</div>`
    + `<aside class="stack">${panel('G1 收敛门禁', gateList(gates) + (run.stage === 'S1' ? waitingBox(run) + actionButtons(run) : ''), run.round && run.max_rounds ? badge(`讨论轮次 ${run.round} / ${run.max_rounds}`) : '')}`
    + `${panel('立场记录', stances.length ? `<ul class="body-list">${stances.slice().reverse().map(stance => `<li><div class="row-meta"><strong>${escape(roleName(stance.role))}</strong>${badge(({ support:'赞同', conditional:'有条件赞同', oppose:'反对' })[stance.stance] || stance.stance || '待表态', stance.stance === 'support' ? 'good' : 'warn')}${stance.round ? badge(`第 ${stance.round} 轮`) : ''}</div><p class="muted-text">${escape(stance.rationale || '')}</p></li>`).join('')}</ul>` : blank('结构化质询完成后显示各席位立场。'))}</aside></div>`;
}

function eventKind(event) {
  const type = String(event.type || '').toLowerCase();
  if (/gate/.test(type)) return 'gate';
  if (/human|decision|confirm|objection|approve/.test(type)) return 'human';
  if (event.stage === 'DEV' || /^DEV/.test(event.node || '')) return 'implementation';
  if (/agent|model|attempt|claim|stance|proposal/.test(type)) return 'agent';
  return 'flow';
}

function runTrace(run) {
  const labels = { all: '全部', flow: '流程', agent: 'Agent', gate: '门禁', human: '人工与分歧', implementation: '实现' };
  const events = newestEvents(run, Infinity);
  const visible = state.eventFilter === 'all' ? events : events.filter(event => eventKind(event) === state.eventFilter);
  const selected = visible.find(event => String(event.seq) === state.selectedEvent) || visible[0];
  const filters = `<div class="filters" aria-label="事件筛选">${Object.entries(labels).map(([key,label]) => `<button class="filter" data-filter="${key}" type="button" aria-pressed="${state.eventFilter === key}">${label}</button>`).join('')}</div>`;
  const list = visible.length ? `<ul class="events">${visible.map(event => `<li><button type="button" class="event-entry" data-event="${escape(event.seq)}" aria-pressed="${event === selected}"><span class="sequence">${escape(event.seq)}</span>`
    + `<span><strong>${escape(event.title || event.type)}</strong><span class="event-node">${escape(event.node || event.stage || labels[eventKind(event)])}</span></span><small>${escape(date(event.timestamp))}</small></button></li>`).join('')}</ul>` : blank('当前筛选下没有事件。');
  const info = selected ? details([['序号', selected.seq], ['类型', selected.type], ['阶段', phaseName(selected.stage)], ['节点', selected.node], ['时间', date(selected.timestamp)]])
    + `<div class="section-gap">${eventDetails(selected.details)}</div>`
    + (!state.replay ? `<div class="action-row"><button type="button" class="button button-secondary" data-replay="${escape(selected.seq)}" ${state.busy ? 'disabled' : ''}>回放至此</button></div>` : '') : blank('选择一条执行事件。');
  return runTitle(run, '运行记录与回放') + `<div class="run-grid">${panel('事件时间线', filters + list, badge(`${number.format(visible.length)} 条`))}<aside class="stack">${panel('事件详情', info)}${usagePanel(run)}</aside></div>`;
}

function artifactList(artifacts, missing) {
  return artifacts.length ? artifacts.map(item => `<details class="artifact"><summary>${escape(item.title || item.kind || item.id || '过程产物')}<small>${escape([item.node, item.version == null ? '' : `v${item.version}`].filter(Boolean).join(' · '))}</small></summary>${showObject(item.content)}</details>`).join('') : blank(missing);
}

function runDelivery(run) {
  const artifacts = [...array(run.artifacts)].reverse();
  const isImplementation = item => /^DEV-/.test(item.node || '');
  const decisions = array(run.decisions).filter(item => ['N15','DEV-A'].includes(item.node));
  const demands = artifacts.filter(item => ['N14','N15'].includes(item.node));
  const others = artifacts.filter(item => !demands.includes(item) && !isImplementation(item));
  return runTitle(run, '需求与交付')
    + `<div class="run-grid"><div class="stack">${panel('需求规格与验收条件', artifactList(demands,'需求 Agent 生成规格后显示原始版本及条目。'))}`
    + `${panel('编码、测试、评审', artifactList(artifacts.filter(isImplementation),'需求签收后，执行产物会随实现步骤登记。'))}`
    + `${panel('前序过程产物', artifactList(others,'暂无可查看的前序产物。'))}</div><aside class="stack">`
    + `${panel('需求签收与交付验收', (decisions.length ? `<ul class="body-list">${decisions.slice().reverse().map(item => `<li><div class="row-meta">${badge(item.node === 'N15' ? '需求签收' : '交付验收')}${badge(actions[item.action] || item.action || '人工决定')}</div><p class="artifact-content">${escape(item.note || '已登记')}</p><small>${escape(date(item.timestamp))}</small></li>`).join('')}</ul>` : blank('当前版本暂无需求签收或交付验收记录。')) + (['S5','DEV'].includes(run.stage) ? waitingBox(run) + actionButtons(run) : ''))}`
    + `${panel('判定明细', gateList(array(run.gates)))}</aside></div>`;
}

function render() {
  renderSide();
  const prefix = state.error ? `<div class="error-banner" role="alert"><span>${escape(state.error)}</span><button type="button" class="button button-secondary button-small" data-refresh>重新读取</button></div>` : '';
  if (!state.loaded) return;
  const run = state.replay?.run || state.run;
  if (!run) {
    root.innerHTML = prefix + (state.view === 'workflow' ? home() : `<section class="loading"><h1>${views[state.view]}</h1><p>创建或选择一条工作流后查看对应记录。</p><button class="button" data-new type="button">新建运行</button></section>`);
    return;
  }
  const historic = state.replay ? `<div class="historic-banner"><span>只读回放 · 事件 #${escape(state.replay.seq)} · 快照 v${escape(run.version)}</span><button class="button button-secondary button-small" data-exit-replay type="button">回到当前运行</button></div>` : '';
  const page = ({ workflow: runOverview, discussion: runDiscussion, trace: runTrace, delivery: runDelivery })[state.view];
  root.innerHTML = prefix + historic + page(run);
}

async function load() {
  state.error = '';
  const responses = await Promise.allSettled([api('/api/info'), api('/api/runs')]);
  if (responses[0].status === 'fulfilled') state.info = responses[0].value;
  if (responses[1].status === 'fulfilled') {
    state.runs = array(responses[1].value.runs);
    const selectedId = state.run?.id || state.runs[0]?.id;
    if (selectedId) {
      try { syncRun(unwrap(await api(apiRun(selectedId)))); }
      catch (error) { state.error = error.message; }
    }
  } else state.error = responses[1].reason.message;
  state.loaded = true;
  render();
}

async function selectRun(id) {
  if (state.busy) return;
  state.error = '';
  try {
    const run = unwrap(await api(apiRun(id)));
    syncRun(run);
    state.replay = null;
    state.selectedEvent = null;
    state.eventFilter = 'all';
    switchView('workflow');
  } catch (error) {
    state.error = error.message;
    render();
  }
}

function formError(form, message) {
  const output = form.querySelector('[data-form-error]');
  if (!output) return;
  output.hidden = !message;
  output.textContent = message || '';
}

async function createRun(form) {
  if (state.busy) return;
  const values = new FormData(form);
  const title = String(values.get('title') || '').trim();
  const idea = String(values.get('idea') || '').trim();
  if (!idea) { formError(form, '请填写想法或需求。'); return; }
  formError(form, '');
  const submit = form.querySelector('[type=submit]');
  submit.disabled = true;
  submit.textContent = '正在创建…';
  state.busy = true;
  try {
    syncRun(unwrap(await api('/api/runs', { method: 'POST', body: JSON.stringify({ title, idea }) })));
    state.replay = null;
    state.selectedEvent = null;
    state.eventFilter = 'all';
    state.error = '';
    if (form.closest('dialog')?.open) form.closest('dialog').close();
    form.reset();
    toast('运行已创建');
    state.busy = false;
    switchView('workflow');
  } catch (error) {
    formError(form, error.message);
  } finally {
    state.busy = false;
    submit.disabled = false;
    submit.textContent = '创建运行 ↗';
  }
}

function showNewRun() {
  if (state.busy) return;
  formError(document.querySelector('#new-run-form'), '');
  newRunDialog.showModal();
}

const describedActions = new Set(['approve', 'return', 'resolve_objection', 'cancel', 'reject', 'request_changes', 'deliver']);
const needsNote = new Set(['return', 'resolve_objection', 'reject', 'request_changes']);
const resolvable = item => item.status === 'open' && ['fact', 'tradeoff'].includes(item.type);

function showAction(key, seedObjection) {
  const run = state.run;
  if (!run || state.replay || state.busy || !actionSet(run).has(key)) return;
  if (!describedActions.has(key)) { applyAction(key); return; }
  state.action = key;
  state.actionVersion = run.version;
  const title = document.querySelector('#action-title');
  title.textContent = actions[key];
  document.querySelector('#action-context').textContent = `${run.waiting?.title || nodeName(run.node)} · ${run.node || ''} · 快照 v${run.version}`;
  const picker = document.querySelector('#action-objection');
  const pickerLabel = document.querySelector('#action-objection-label');
  pickerLabel.hidden = key !== 'resolve_objection';
  picker.required = key === 'resolve_objection';
  picker.innerHTML = array(run.objections).filter(resolvable).map(item => `<option value="${escape(item.id)}">${escape(item.id)} · ${escape(item.summary || item.type || '')}</option>`).join('');
  if (seedObjection && Array.from(picker.options).some(option => option.value === seedObjection)) picker.value = seedObjection;
  const note = document.querySelector('#action-note');
  note.value = '';
  note.required = needsNote.has(key) || (key === 'approve' && run.waiting?.id === 'HITL-2');
  document.querySelector('#action-note-hint').textContent = note.required ? '必填' : '选填';
  document.querySelector('#action-submit').textContent = actions[key];
  formError(actionForm, '');
  actionDialog.showModal();
}

async function applyAction(key, form = null) {
  const run = state.run;
  if (!run || state.replay || state.busy || !actionSet(run).has(key)) return;
  const data = form ? new FormData(form) : null;
  const note = String(data?.get('note') || '').trim();
  const objectionId = key === 'resolve_objection' ? data?.get('objection_id') : undefined;
  const payload = { action: key, expected_version: form ? state.actionVersion : run.version, command_id: window.crypto.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}` };
  if (note) payload.note = note;
  if (objectionId) payload.objection_id = objectionId;
  state.error = '';
  state.busy = true;
  state.action = key;
  render();
  const submit = form?.querySelector('[type=submit]');
  if (submit) { submit.disabled = true; submit.textContent = '正在处理…'; }
  let observing = true;
  const observer = window.setInterval(async () => {
    try {
      const snapshot = unwrap(await api(apiRun(run.id)));
      if (observing && snapshot.version > state.run.version) { syncRun(snapshot); render(); }
    } catch { /* A transient observation error does not interrupt an action. */ }
  }, 1200);
  try {
    syncRun(unwrap(await api(`${apiRun(run.id)}/actions`, { method: 'POST', body: JSON.stringify(payload) })));
    if (form && actionDialog.open) actionDialog.close();
    toast(`已登记「${actions[key]}」`);
  } catch (error) {
    if (error.status === 409) {
      try { syncRun(unwrap(await api(apiRun(run.id)))); } catch { /* The read error is recoverable on refresh. */ }
      if (form && actionDialog.open) actionDialog.close();
      state.error = `${error.message} 请核对最新运行状态后再操作。`;
    } else if (form) formError(form, error.message);
    else state.error = error.message;
  } finally {
    observing = false;
    window.clearInterval(observer);
    state.busy = false;
    if (!actionDialog.open) state.action = null;
    if (submit) { submit.disabled = false; submit.textContent = actions[key]; }
    render();
  }
}

async function replayTo(sequence) {
  if (!state.run || state.busy) return;
  try {
    const run = unwrap(await api(`${apiRun(state.run.id)}/history?seq=${encodeURIComponent(sequence)}`));
    state.replay = { run, seq: sequence };
    state.selectedEvent = sequence;
    state.error = '';
  } catch (error) { state.error = error.message; }
  render();
}

state.view = readView();
navigation.addEventListener('click', event => {
  const button = event.target.closest('[data-view]');
  if (button) switchView(button.dataset.view);
});
window.addEventListener('hashchange', () => switchView(readView()));
runList.addEventListener('click', event => {
  const button = event.target.closest('[data-run]');
  if (button) selectRun(button.dataset.run);
});
document.querySelector('#top-create').addEventListener('click', showNewRun);
document.querySelector('#sidebar-create').addEventListener('click', showNewRun);
for (const button of document.querySelectorAll('[data-dialog-close]')) {
  button.addEventListener('click', () => { if (!state.busy) button.closest('dialog').close(); });
}
for (const dialog of [newRunDialog, actionDialog]) {
  dialog.addEventListener('cancel', event => { if (state.busy) event.preventDefault(); });
}
document.querySelector('#new-run-form').addEventListener('submit', event => { event.preventDefault(); createRun(event.currentTarget); });
actionForm.addEventListener('submit', event => { event.preventDefault(); applyAction(state.action, actionForm); });
root.addEventListener('submit', event => {
  if (event.target.matches('[data-create-form]')) { event.preventDefault(); createRun(event.target); }
});
root.addEventListener('click', event => {
  const button = event.target.closest('button');
  if (!button) return;
  if (button.hasAttribute('data-start-focus')) root.querySelector('[name=idea]')?.focus();
  if (button.hasAttribute('data-new')) showNewRun();
  if (button.hasAttribute('data-refresh')) load();
  if (button.dataset.go) switchView(button.dataset.go);
  if (button.dataset.action) showAction(button.dataset.action, button.dataset.objection);
  if (button.hasAttribute('data-exit-replay')) { state.replay = null; render(); }
  if (button.dataset.replay) replayTo(button.dataset.replay);
  if (button.dataset.filter) { state.eventFilter = button.dataset.filter; state.selectedEvent = null; render(); }
  if (button.dataset.event) { state.selectedEvent = button.dataset.event; render(); }
});

load();
