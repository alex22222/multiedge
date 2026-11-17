'use strict';

const $ = (id) => document.getElementById(id);
const numberFormat = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 2 });
const moneyFormat = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 2 });
const views = {
  overview: ['研究总览', 'OBSERVE · VERIFY · ITERATE', '让每一个信号，都有据可查。', '把任务结果、研究判断与验证记录，放在同一张研究台上。'],
  candidates: ['候选池', 'THE RESEARCH UNIVERSE', '找到值得继续研究的标的。', '从规则信号出发，逐只查看证据、结构和仍待回答的问题。'],
  paper: ['Paper 验证', 'DECISIONS MEET TIME', '把判断交给时间验证。', '每笔模拟决策留有来源，每个估值点标明日期。'],
  experiments: ['指标实验', 'BUILD YOUR OWN EDGE', '建立属于你的指标体系。', '独立保存每次调整，与同一份数据上的原始规则做对照。'],
  validation: ['验证说明', 'KNOW THE EVIDENCE', '知道结果，也知道它的边界。', '明确数据来自哪里、规则如何判定，以及验证实际覆盖了什么。'],
};
const edgeLabels = {
  momentum: '动能', volume_pattern: '量能结构', ma_zone: '均线区', sr_flip: '水平结构',
  trendline: '冻结趋势线', rs: '相对强度 RS', market_structure: '市场结构',
};
const statusLabels = {
  in_review: '任务待复审', partial: '部分明细', imported: '已导入快照', source_snapshot: '来源快照',
  snapshot: '来源快照', complete: '已完成', completed: '已完成', succeeded: '已完成',
  failed: '失败', error: '错误', pending: '等待数据', running: '进行中', unverified: '未核验',
  verified: '有核验证据', available: '有数据', computed: '已计算', missing: '待补证据',
  deferred: '待补证据', needs_research: '待补证据', not_available: '不可用', unavailable: '不可用',
  proxy: '价格代理', price_proxy: '价格代理', not_run: '尚未运行', passed: '已通过',
  waiting: '等待后续行情', replay: '历史回放', forward: '前向 Paper',
  user_recorded: '用户留证', partial_evidence: '部分证据',
};
const paperLabels = {
  entry: '模拟买入', exit: '模拟卖出', skipped: '跳过', pending: '等待后续行情', filled: '模拟成交',
  invalid_stop: '缺少有效止损', missing_signal_session: '缺少信号日行情', late_import: '历史信号迟到导入，不补记成交',
  missing_entry_bar: '缺少入场日行情', already_holding: '已有该标的持仓', stop_at_or_above_entry: '止损不低于模拟入场价',
  target_at_or_below_entry: '目标不高于模拟入场价', insufficient_budget: '风险预算或现金不足',
  missing_observed_at: '缺少信号登记时间', invalid_observed_at: '信号登记时间无效',
  signal_observed_after_entry: '信号登记晚于入场时间，不回填交易', signal_observed_before_session: '登记时间早于信号交易日',
  account_started_after_entry: '账户登记晚于入场时间，不回填交易',
  stop: '触及止损', target: '触及目标', stop_gap: '跳空穿越止损', target_gap: '跳空越过目标',
  stop_and_target: '同日触及止损与目标，按止损优先处理',
};

let workspace = { runs: [], profiles: [], paper_accounts: [] };
let activeRun = null;
let candidateFilter = 'ALL';
let selectedPaper = null;
let loadSequence = 0;
let toastTimer;

function node(tag, className, text) {
  const item = document.createElement(tag);
  if (className) item.className = className;
  if (text !== undefined && text !== null) item.textContent = String(text);
  return item;
}

function numeric(value) {
  return value !== null && value !== undefined && value !== '' && Number.isFinite(Number(value));
}

function fmt(value, digits = 2) {
  if (!numeric(value)) return '—';
  return digits === 2 ? numberFormat.format(Number(value)) : Number(value).toFixed(digits);
}

function money(value) {
  return numeric(value) ? moneyFormat.format(Number(value)) : '—';
}

function dateLabel(value, withTime = false) {
  if (!value) return '未提供';
  if (!withTime && /^\d{4}-\d{2}-\d{2}/.test(String(value))) return String(value).slice(0, 10);
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return String(value);
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
    ...(withTime ? { hour: '2-digit', minute: '2-digit', hour12: false } : {}),
  }).format(date);
}

function humanStatus(value) {
  return statusLabels[String(value || '').toLowerCase()] || String(value || '未提供');
}

function sourceLabel(run) {
  if (!run) return '未提供';
  if (typeof run.source === 'string') return run.source;
  return run.source?.name || run.source?.type || run.issue_id || '研究快照';
}

function verdict(row) {
  return String(row?.verdict || row?.report?.verdict || '未提供').toUpperCase();
}

function badge(value, kind) {
  const style = kind || ({ BUY: 'buy', WATCH: 'watch', NO: 'no' }[value] || 'muted');
  return node('span', `badge ${style}`, ({ MISSING: '待取明细', ERROR: '数据错误', UNAVAILABLE: '数据不足' }[value] || value));
}

function safeURL(value) {
  if (!value) return null;
  try {
    const url = new URL(value, document.baseURI);
    return ['http:', 'https:'].includes(url.protocol) ? url.href : null;
  } catch {
    return null;
  }
}

function externalLink(label, href, className = 'text-link') {
  const link = node('a', className, label);
  const safe = safeURL(href);
  if (safe) {
    link.href = safe;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
  }
  return link;
}

async function api(path, options = {}) {
  const response = await fetch(new URL(path, document.baseURI), {
    credentials: 'same-origin', cache: 'no-store',
    ...options,
    headers: { ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...options.headers },
  });
  if (!(response.headers.get('content-type') || '').includes('application/json')) {
    throw new Error('未能读取研究数据。请确认当前登录会话和服务连接。');
  }
  const data = await response.json();
  if (!response.ok || data.error) throw new Error(data.error || `请求失败（${response.status}）`);
  return data;
}

function setFormStatus(id, message, isError = false) {
  $(id).textContent = message;
  $(id).classList.toggle('is-error', isError);
}

function toast(message) {
  clearTimeout(toastTimer);
  $('toast').textContent = message;
  $('toast').hidden = false;
  toastTimer = setTimeout(() => { $('toast').hidden = true; }, 4000);
}

function showView() {
  const requested = location.hash.slice(1);
  if (requested === 'main') {
    $('main').focus();
    return;
  }
  const view = Object.hasOwn(views, requested) ? requested : 'overview';
  document.querySelectorAll('.view').forEach((section) => { section.hidden = section.id !== `view-${view}`; });
  document.querySelectorAll('[data-view]').forEach((link) => {
    if (link.dataset.view === view) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  });
  const [label, eyebrow, title, subtitle] = views[view];
  $('breadcrumb-current').textContent = label;
  $('page-eyebrow').textContent = eyebrow;
  $('page-title').textContent = title;
  $('page-subtitle').textContent = subtitle;
  document.title = `${label} · Multi-Edge 策略研究台`;
}

function runOption(run) {
  return `${run.as_of || '日期未提供'} · ${run.label || run.name || sourceLabel(run)}`;
}

function renderRunSelector() {
  $('run-select').replaceChildren();
  for (const run of workspace.runs || []) {
    const option = node('option', '', runOption(run));
    option.value = run.run_id;
    $('run-select').append(option);
  }
  if (!$('run-select').options.length) $('run-select').append(node('option', '', '尚无研究快照'));
  if (activeRun) $('run-select').value = activeRun.run_id;
}

function addFact(list, label, value) {
  const row = node('div');
  row.append(node('dt', '', label), node('dd', '', value));
  list.append(row);
}

function renderOverview() {
  const run = activeRun;
  const rows = run?.results || [];
  const loaded = run?.result_count ?? rows.length;
  const expected = run?.expected_count;
  const partial = numeric(expected) && Number(loaded) < Number(expected);
  const counts = run?.claimed_counts || run?.counts || {};
  $('metric-loaded').textContent = run ? fmt(loaded, 0) : '—';
  $('metric-loaded-note').textContent = numeric(expected) ? `任务股票池 ${fmt(expected, 0)} 只 · 已载入 ${fmt(loaded, 0)} 只` : '按实际载入明细统计';
  for (const kind of ['BUY', 'WATCH', 'NO']) {
    $(`metric-${kind.toLowerCase()}`).textContent = fmt(counts[kind] ?? counts[kind.toLowerCase()], 0);
    $(`metric-${kind.toLowerCase()}-label`).textContent = `${kind} · ${run?.claimed_counts ? '任务汇总' : '实际明细'}`;
  }
  $('nav-candidate-count').textContent = run ? fmt(rows.length, 0) : '—';
  $('snapshot-title').textContent = run ? `${sourceLabel(run)} · ${dateLabel(run.as_of)} 收盘快照` : '尚无研究快照';
  $('snapshot-description').textContent = partial
    ? `任务汇总覆盖 ${fmt(expected, 0)} 只，目前载入 ${fmt(loaded, 0)} 只明细。全池明细与独立复跑仍需补齐。`
    : (run?.description || '任务来源、独立复跑与 Paper 结果分别留证；完成状态以验证记录为准。');
  $('snapshot-status').textContent = partial ? '部分明细' : humanStatus(run?.status || 'pending');
  $('snapshot-status').className = `badge ${partial || run?.status === 'in_review' ? 'amber' : 'muted'}`;
  const coverage = numeric(expected) && Number(expected) > 0 ? Math.min(100, Number(loaded) / Number(expected) * 100) : null;
  $('coverage-number').textContent = coverage === null ? '—' : `${fmt(coverage, 1)}%`;
  $('coverage-progress').setAttribute('aria-valuenow', coverage === null ? '0' : String(coverage));
  $('coverage-progress').firstElementChild.style.width = `${coverage || 0}%`;
  $('coverage-note').textContent = coverage === null ? '尚未提供完整股票池规模。' : `${fmt(loaded, 0)} / ${fmt(expected, 0)} 只明细已载入。这里表示明细覆盖率，不表示策略通过率。`;
  $('source-facts').replaceChildren();
  addFact($('source-facts'), '分析截止日', dateLabel(run?.as_of));
  addFact($('source-facts'), '来源', sourceLabel(run));
  addFact($('source-facts'), '行情来源', run?.data_snapshot?.provider || '待核验');
  addFact($('source-facts'), '价格口径', run?.data_snapshot?.price_basis || '待核验');
  addFact($('source-facts'), '快照状态', humanStatus(run?.status));
  addFact($('source-facts'), '载入时间 · 北京', dateLabel(run?.imported_at, true));
  const issue = safeURL(run?.issue_url);
  $('issue-link').href = issue || '#validation';
  $('issue-link').textContent = issue ? '打开 Multica 来源任务 ↗' : '查看来源与验证说明 ↗';
  if (issue) {
    $('issue-link').target = '_blank';
    $('issue-link').rel = 'noopener noreferrer';
  } else {
    $('issue-link').removeAttribute('target');
    $('issue-link').removeAttribute('rel');
  }
  const replayed = run?.config_verified && !partial && rows.length > 0 && rows.every((row) => row.report_available);
  $('overview-source-note').textContent = replayed
    ? '完整明细与行情已取得；全部关键判定字段经独立复跑一致。原任务的复审状态单独保留。'
    : (run?.claimed_counts ? '卡片仅展示已取得明细；上方 BUY / WATCH / NO 数量来自任务汇总，独立复跑范围见验证说明。' : '卡片展示实际载入的股票明细。');
  const featured = rows.filter((row) => ['BUY', 'WATCH', 'NO'].includes(verdict(row))).sort((a, b) => ({ BUY: 0, WATCH: 1, NO: 2 }[verdict(a)] ?? 3) - ({ BUY: 0, WATCH: 1, NO: 2 }[verdict(b)] ?? 3)).slice(0, 4);
  $('overview-candidates').replaceChildren();
  for (const row of featured) {
    const card = node('button', 'candidate-card');
    card.type = 'button';
    card.setAttribute('aria-label', `查看 ${row.ticker} 三层研究`);
    card.append(node('span', 'ticker-symbol', row.ticker));
    const main = node('span', 'candidate-card-main');
    const name = node('span', 'candidate-card-name', row.ticker);
    name.append(badge(verdict(row)));
    const report = row.report || {};
    let note = row.error ? '数据不足，查看缺口' : (numeric(report.stop) ? `止损参考 ${fmt(report.stop)}` : '止损参考待核验');
    note += numeric(report.target) ? ` · 目标 ${fmt(report.target)}` : ' · 目标尚不可用';
    main.append(name, node('span', 'candidate-card-note', note));
    const price = node('span', 'candidate-card-price', money(row.close ?? report.close));
    price.append(node('small', '', '快照收盘价'));
    card.append(main, price, node('span', 'candidate-card-arrow', '↗'));
    card.addEventListener('click', () => openStock(row.ticker));
    $('overview-candidates').append(card);
  }
  if (!featured.length) $('overview-candidates').append(node('div', 'empty-state', '尚无股票明细。请先导入真实筛选产物。'));
}

function layerStatus(layer) {
  const status = String(layer?.status || 'missing').toLowerCase();
  if (['verified', 'computed', 'available', 'complete'].includes(status)) return 'verified';
  if (['partial', 'proxy', 'price_proxy', 'source_snapshot', 'snapshot', 'user_recorded', 'partial_evidence'].includes(status)) return 'partial';
  return 'missing';
}

function layerDots(layers) {
  const result = node('div', 'layer-dots');
  let evidenced = 0;
  for (const [key, label] of [['cognitive', '认知'], ['intuitive', '直觉'], ['technical', '技术']]) {
    const status = layerStatus(layers?.[key]);
    if (status !== 'missing') evidenced += 1;
    const dot = node('span', `layer-dot ${status}`);
    dot.title = `${label}层：${humanStatus(layers?.[key]?.status || 'missing')}`;
    result.append(dot);
  }
  result.setAttribute('aria-label', `三层中 ${evidenced} 层有数据或代理证据`);
  result.append(node('small', '', `${evidenced}/3 有证据`));
  return result;
}

function renderCandidates() {
  const all = activeRun?.results || [];
  const search = $('candidate-search').value.trim().toUpperCase();
  const rows = all.filter((row) => (candidateFilter === 'ALL' || verdict(row) === candidateFilter) && String(row.ticker || '').toUpperCase().includes(search));
  $('candidate-count-note').textContent = `本页 ${rows.length} 只 / 股票池 ${all.length} 只 · 已取明细 ${activeRun?.result_count ?? all.filter((row) => row.verdict !== 'MISSING').length} 只`;
  $('candidate-snapshot-badge').textContent = dateLabel(activeRun?.as_of);
  $('candidate-table-body').replaceChildren();
  for (const row of rows) {
    const tr = node('tr');
    const tickerCell = node('td', 'ticker-cell', row.ticker);
    tickerCell.append(node('small', '', row.company || row.name || row.sector || '单股研究'));
    const verdictCell = node('td');
    verdictCell.append(badge(verdict(row)));
    const layersCell = node('td');
    layersCell.append(layerDots(row.layers));
    const actionCell = node('td');
    const action = node('button', 'table-action', '查看三层 ↗');
    action.type = 'button';
    action.setAttribute('aria-label', `查看 ${row.ticker} 详情`);
    action.addEventListener('click', () => openStock(row.ticker));
    actionCell.append(action);
    tr.append(tickerCell, verdictCell, node('td', '', money(row.close ?? row.report?.close)), node('td', '', numeric(row.report?.mature_count) ? `${row.report.mature_count} 项` : '未提供'), layersCell, actionCell);
    $('candidate-table-body').append(tr);
  }
  $('candidate-empty').hidden = rows.length > 0;
}

function addEvidence(list, evidence) {
  for (const entry of Array.isArray(evidence) ? evidence : []) {
    const li = node('li');
    if (typeof entry === 'string') li.textContent = entry;
    else if (entry && typeof entry === 'object') {
      const text = entry.text || entry.summary || entry.title || entry.description || entry.source || '来源证据';
      const url = safeURL(entry.url || entry.link || entry.source_url);
      li.append(url ? externalLink(text, url, '') : node('span', '', text));
      if (entry.as_of || entry.date || entry.event_date) li.append(node('span', '', ` · ${dateLabel(entry.as_of || entry.date || entry.event_date)}`));
      if (entry.observed_at) li.append(node('span', 'muted-text', ` · 登记 ${dateLabel(entry.observed_at, true)}`));
    }
    list.append(li);
  }
}

function evidenceForm(row) {
  const section = node('details', 'evidence-editor');
  section.append(node('summary', '', '补充认知 / 直觉层证据'));
  section.append(node('p', 'form-help', '记录你找到的原始来源与判断。保存后标记为“用户留证”，独立核验状态另行确认。'));
  const form = node('form');
  const fields = node('div', 'form-grid');
  const layerLabel = node('label', '', '研究层次');
  const layer = node('select');
  layer.name = 'layer';
  for (const [value, label] of [['cognitive', '认知层 · 催化剂与业务驱动'], ['intuitive', '直觉层 · 主题与板块联动']]) {
    const option = node('option', '', label);
    option.value = value;
    layer.append(option);
  }
  layerLabel.append(layer);
  const date = node('input');
  date.type = 'date';
  date.name = 'event_date';
  const dateField = node('label', '', '事件日期（可选）');
  dateField.append(date);
  fields.append(layerLabel, dateField);
  const source = node('input');
  source.type = 'url';
  source.name = 'source_url';
  source.required = true;
  source.maxLength = 2000;
  source.placeholder = 'https://… 原始公告、财报或研究来源';
  const sourceField = node('label', '', '原始来源链接');
  sourceField.append(source);
  const summary = node('textarea');
  summary.name = 'summary';
  summary.rows = 3;
  summary.maxLength = 4000;
  summary.required = true;
  summary.placeholder = '记录已知事实、你的判断，以及仍待核验的问题。';
  const summaryField = node('label', '', '证据与判断');
  summaryField.append(summary);
  const action = node('button', 'secondary-button', '保存研究证据');
  action.type = 'submit';
  const status = node('p', 'form-status');
  status.setAttribute('role', 'status');
  form.append(fields, sourceField, summaryField, action, status);
  const runId = activeRun.run_id;
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    action.disabled = true;
    status.textContent = '正在保存研究证据…';
    status.classList.remove('is-error');
    try {
      await api('api/evidence', { method: 'POST', body: JSON.stringify({ run_id: runId, ticker: row.ticker, layer: layer.value, summary: summary.value.trim(), source_url: source.value.trim(), event_date: date.value || null }) });
      const fresh = await api(`api/stock?${new URLSearchParams({ run_id: runId, ticker: row.ticker })}`);
      if (activeRun?.run_id === runId) {
        const saved = fresh.result || fresh;
        const cached = activeRun.results?.find((item) => item.ticker === row.ticker);
        if (cached) Object.assign(cached, saved);
        renderCandidates();
        if ($('stock-dialog').open && $('stock-title').textContent.startsWith(row.ticker)) renderStock(saved);
      }
      toast('研究证据已保存，标记为用户留证');
    } catch (error) {
      status.textContent = error.message;
      status.classList.add('is-error');
    } finally {
      action.disabled = false;
    }
  });
  section.append(form);
  return section;
}

function renderStock(row) {
  const report = row.report || {};
  $('stock-title').textContent = `${row.ticker || '标的'} · 三层研究`;
  $('stock-subtitle').textContent = `${dateLabel(activeRun?.as_of)} · ${sourceLabel(activeRun)} · ${humanStatus(activeRun?.status)}`;
  const content = $('stock-content');
  content.replaceChildren();
  const summary = node('div', 'stock-summary');
  summary.append(node('strong', 'stock-price', money(row.close ?? report.close)), badge(verdict(row)), badge(row.report_available ? '有行情数据' : row.report ? '来源快照' : '待取明细', 'muted'));
  content.append(summary);
  if (row.error) content.append(node('div', 'notice error-notice', `明细缺口：${row.error}`));
  const facts = node('dl', 'stock-facts');
  for (const [label, value] of [
    ['止损参考', numeric(report.stop) ? fmt(report.stop) : '不可用'],
    ['目标参考', numeric(report.target) ? fmt(report.target) : '不可用'],
    ['盈亏比', numeric(report.rr) ? `${fmt(report.rr)}R` : '不可评估'],
    ['MEA 汇合区', Array.isArray(report.mea_zone) && report.mea_zone.length === 2 ? report.mea_zone.map((v) => fmt(v)).join(' – ') : '未提供'],
  ]) {
    const fact = node('div', 'stock-fact');
    fact.append(node('dt', '', label), node('dd', '', value));
    facts.append(fact);
  }
  content.append(facts);
  const layers = node('div', 'stock-layers');
  const layerDefinitions = [
    ['cognitive', '认知层', '为什么值得关注？', '尚无经核验的公司驱动或基本面证据。'],
    ['intuitive', '直觉层', '谁与它一起走强？', '尚无板块联动、主题或资金流证据。'],
    ['technical', '技术层', '在哪里验证判断？', '技术明细尚未载入。'],
  ];
  for (const [key, label, question, fallback] of layerDefinitions) {
    const layer = row.layers?.[key] || {};
    const card = node('article', 'layer-card');
    const heading = node('div', 'layer-card-head');
    heading.append(node('span', `layer-label ${key}`, label), badge(humanStatus(layer.status || 'missing'), layerStatus(layer) === 'verified' ? 'mint' : 'muted'));
    card.append(heading, node('h3', '', question), node('p', '', layer.summary || fallback));
    const evidence = node('ul', 'evidence-list');
    addEvidence(evidence, layer.evidence);
    if (evidence.childElementCount) card.append(evidence);
    layers.append(card);
  }
  content.append(layers);
  content.append(evidenceForm(row));
  if (report.edges && Object.keys(report.edges).length) {
    content.append(node('h3', 'stock-section-title', '各项边缘 · 引擎输出'));
    const edges = node('div', 'edge-grid');
    for (const [key, edge] of Object.entries(report.edges)) {
      const item = node('article', 'edge-item');
      const title = node('div', 'edge-title');
      title.append(node('span', '', edgeLabels[key] || key), node('span', '', `${fmt(edge.score, 1)} · ${edge.state || '未提供'}`));
      const bar = node('div', 'edge-bar');
      const fill = node('span');
      fill.style.width = `${numeric(edge.score) ? Math.max(0, Math.min(100, Number(edge.score))) : 0}%`;
      bar.append(fill);
      item.append(title, bar);
      const evidence = node('ul', 'evidence-list edge-evidence');
      addEvidence(evidence, edge.evidence);
      if (evidence.childElementCount) item.append(evidence);
      edges.append(item);
    }
    content.append(edges);
  }
  if (Array.isArray(report.checklist) && report.checklist.length) {
    content.append(node('h3', 'stock-section-title', '买入条件 · 逐项核对'));
    const list = node('ul', 'check-list');
    for (const check of report.checklist) {
      const li = node('li');
      const label = node('span', '', check.label || check.key);
      label.append(node('span', 'check-detail', [check.actual, check.threshold, check.viewpoint].filter(Boolean).join(' · ')));
      li.append(node('span', `check-indicator${check.ok ? '' : ' fail'}`, check.ok ? '✓' : '○'), label);
      list.append(li);
    }
    content.append(list);
  }
  if (Array.isArray(report.risks) && report.risks.length) {
    content.append(node('h3', 'stock-section-title', '风险与缺口'));
    const list = node('ul', 'risk-list');
    for (const risk of report.risks) list.append(node('li', '', risk));
    content.append(list);
  }
  const actions = node('div', 'stock-report-actions');
  if (row.report_available || row.snapshot_available || row.report) {
    const url = new URL('report', document.baseURI);
    url.search = new URLSearchParams({ ticker: row.ticker, as_of: activeRun.as_of, run_id: activeRun.run_id });
    actions.append(externalLink(row.report_available ? '打开策略 HTML 报告 ↗' : '打开来源快照说明 ↗', url.href, 'secondary-button'));
    actions.append(node('p', '', row.report_available ? '报告中的评分与判断来自策略引擎。' : '尚无完整行情图；此页保留已导入的引擎快照与来源说明。'));
  } else {
    actions.append(node('p', '', '尚无可用的策略报告。需要补齐原始明细或行情后再核验。'));
  }
  content.append(actions);
}

async function openStock(ticker) {
  const runId = activeRun?.run_id;
  if (!runId) return;
  const cached = activeRun.results?.find((row) => row.ticker === ticker);
  if (cached) renderStock(cached);
  else $('stock-content').replaceChildren(node('div', 'empty-state', '正在读取标的研究…'));
  if (!$('stock-dialog').open) $('stock-dialog').showModal();
  try {
    const row = await api(`api/stock?${new URLSearchParams({ run_id: runId, ticker })}`);
    if (activeRun?.run_id === runId && $('stock-dialog').open && $('stock-title').textContent.startsWith(ticker)) renderStock(row.result || row);
  } catch (error) {
    if (!cached) $('stock-content').replaceChildren(node('div', 'notice error-notice', error.message));
    else toast(`来源明细刷新失败：${error.message}`);
  }
}

function paperLedger(account) {
  return account ? { ...(account.state || {}), ...account } : {};
}

function renderPaperAccounts() {
  const accounts = workspace.paper_accounts || [];
  const selected = selectedPaper?.account_id || $('paper-account-select').value;
  $('paper-account-select').replaceChildren();
  for (const account of accounts) {
    const data = paperLedger(account);
    const profile = (workspace.profiles || []).find((item) => (item.id || item.profile_id) === data.profile_id);
    const strategy = data.profile_id ? (configOf(profile).name || '独立指标实验') : '原始策略';
    const option = node('option', '', `${humanStatus(data.mode)} · ${strategy} · ${data.as_of || data.created_at?.slice(0, 10) || data.account_id}`);
    option.value = account.account_id;
    $('paper-account-select').append(option);
  }
  if (!accounts.length) $('paper-account-select').append(node('option', '', '尚无验证账户'));
  if (accounts.some((account) => account.account_id === selected)) $('paper-account-select').value = selected;
  const current = accounts.find((account) => account.account_id === $('paper-account-select').value);
  if (current) selectedPaper = current;
  else if (!accounts.length) selectedPaper = null;
  renderPaper();
}

function renderPaper() {
  const data = paperLedger(selectedPaper);
  const positions = Array.isArray(data.positions) ? data.positions : [];
  const trades = Array.isArray(data.trades) ? data.trades : [];
  $('paper-equity').textContent = money(data.equity);
  $('paper-cash').textContent = money(data.cash);
  $('paper-counts').textContent = selectedPaper ? `${positions.length} / ${trades.length}` : '—';
  $('paper-mode-note').textContent = selectedPaper ? humanStatus(data.mode) : '等待验证账户';
  $('paper-as-of').textContent = data.data_as_of ? `行情截至 ${dateLabel(data.data_as_of)}` : '尚未取得估值行情';
  const gaps = [];
  if (data.data_missing?.length) gaps.push(`行情缺失：${data.data_missing.join('、')}`);
  if (data.data_stale) gaps.push(`行情尚未覆盖估值截止日${data.data_as_of ? `（最新 ${dateLabel(data.data_as_of)}）` : ''}`);
  if (selectedPaper && !positions.length && !trades.length) gaps.push('尚无模拟成交记录，当前资金不代表策略验证收益');
  $('paper-data-note').hidden = !gaps.length;
  $('paper-data-note').textContent = gaps.join('。');
  $('paper-position-note').textContent = `${positions.length} 只持仓`;
  $('paper-positions-body').replaceChildren();
  for (const position of positions) {
    const tr = node('tr');
    const status = node('td');
    status.append(badge(position.stale ? '估值过期' : (position.mark_date ? '有估值' : '等待估值'), position.stale ? 'amber' : 'muted'));
    status.append(node('small', 'check-detail', dateLabel(position.mark_date)));
    tr.append(node('td', 'ticker-cell', position.ticker), node('td', '', fmt(position.quantity)), node('td', '', money(position.entry_price)), node('td', '', money(position.mark)), node('td', '', `${fmt(position.stop)} / ${numeric(position.target) ? fmt(position.target) : '不可用'}`), status);
    $('paper-positions-body').append(tr);
  }
  $('paper-positions-empty').hidden = positions.length > 0;
  const pending = (data.orders || []).filter((order) => order.status === 'pending');
  const events = [...(Array.isArray(data.events) && data.events.length ? data.events : [...(data.orders || []).filter((order) => order.status !== 'pending'), ...trades]), ...pending];
  $('paper-events-body').replaceChildren();
  for (const event of events.slice(-100).reverse()) {
    const tr = node('tr');
    const detail = event.reason || event.exit_reason || event.message || event.description || event.note || (event.status === 'pending' ? '尚无可处理的下一完整交易日' : '');
    const kind = event.status || event.action || event.kind || event.type || event.side || '记录';
    tr.append(node('td', '', dateLabel(event.date || event.exit_date || event.as_of || event.timestamp || event.signal_date)), node('td', 'ticker-cell', event.ticker || '—'), node('td', '', paperLabels[kind] || kind), node('td', '', `${fmt(event.quantity)} / ${fmt(event.price ?? event.fill_price ?? event.exit_price)}`), node('td', 'cell-note', paperLabels[detail] || detail));
    $('paper-events-body').append(tr);
  }
  $('paper-events-empty').hidden = events.length > 0;
  renderEquity(data.equity_curve || []);
  $('paper-stat-summary').replaceChildren();
  if (selectedPaper) {
    const metrics = data.metrics || {};
    const observed = Array.isArray(data.equity_curve) && data.equity_curve.length > 0;
    for (const [label, value] of [
      ['账面收益率', observed && numeric(metrics.total_return) ? `${fmt(Number(metrics.total_return) * 100)}%` : '尚无估值'],
      ['最大回撤', observed && numeric(metrics.max_drawdown) ? `${fmt(Number(metrics.max_drawdown) * 100)}%` : '尚无估值'],
      ['已实现损益', money(metrics.realized_pnl)],
      ['完成交易胜率', numeric(metrics.win_rate) ? `${fmt(Number(metrics.win_rate) * 100)}%` : '样本不足'],
    ]) addFact($('paper-stat-summary'), label, value);
  }
  const assumptions = $('paper-assumptions');
  assumptions.replaceChildren();
  if (selectedPaper) {
    assumptions.append(node('p', '', `手续费 ${fmt(data.config?.fee_bps)} bps · 滑点 ${fmt(data.config?.slippage_bps)} bps · 单笔风险 ${numeric(data.config?.risk_fraction) ? `${fmt(data.config.risk_fraction * 100)}%` : '未提供'}`));
    const basis = { unadjusted: '未复权日线', adjusted: '复权日线' }[data.price_basis] || data.price_basis || '口径未提供';
    const provider = { marketwatch: 'MarketWatch', yahoo: 'Yahoo Finance' }[data.provider_id] || data.provider_id || '来源未提供';
    assumptions.append(node('p', '', `行情来源：${provider} · ${basis}`));
    const list = node('ul', 'evidence-list');
    addEvidence(list, data.assumptions || []);
    assumptions.append(list);
  } else assumptions.textContent = '选择账户后显示对应的费用、滑点和成交规则。';
}

function svgNode(tag, attributes = {}, text) {
  const item = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const [key, value] of Object.entries(attributes)) item.setAttribute(key, String(value));
  if (text !== undefined) item.textContent = text;
  return item;
}

function renderEquity(curve) {
  const values = (Array.isArray(curve) ? curve : []).filter((point) => numeric(point.equity)).map((point) => ({ value: Number(point.equity), date: point.date || point.as_of || point.timestamp || '' }));
  $('equity-sample-count').textContent = `${values.length} 个估值点`;
  const container = $('equity-chart');
  container.replaceChildren();
  if (!values.length) {
    container.append(node('div', 'empty-state', '尚无权益观察。首个估值点产生后会在这里显示。'));
    return;
  }
  const width = 700, height = 220, left = 68, right = 12, top = 14, bottom = 34;
  const min = Math.min(...values.map((point) => point.value));
  const max = Math.max(...values.map((point) => point.value));
  const padding = Math.max((max - min) * 0.12, max * 0.001, 1);
  const low = min - padding, high = max + padding;
  const x = (i) => left + (values.length > 1 ? i / (values.length - 1) : 0.5) * (width - left - right);
  const y = (value) => top + (high - value) / (high - low) * (height - top - bottom);
  const svg = svgNode('svg', { viewBox: `0 0 ${width} ${height}`, role: 'img', 'aria-label': `${humanStatus(paperLedger(selectedPaper).mode)}权益曲线，${values.length} 个真实台账估值点` });
  svg.append(svgNode('title', {}, '来自 Paper 台账的权益观察'));
  for (let index = 0; index < 3; index += 1) {
    const value = low + (high - low) * index / 2;
    svg.append(svgNode('line', { x1: left, y1: y(value), x2: width - right, y2: y(value), stroke: '#243541', 'stroke-width': 1 }));
    svg.append(svgNode('text', { x: left - 10, y: y(value) + 4, fill: '#78909d', 'font-size': 10, 'text-anchor': 'end' }, fmt(value)));
  }
  const points = values.map((point, index) => `${x(index).toFixed(1)},${y(point.value).toFixed(1)}`).join(' ');
  if (values.length > 1) svg.append(svgNode('polyline', { points, fill: 'none', stroke: '#94d5b4', 'stroke-width': 2, 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }));
  const last = values[values.length - 1];
  svg.append(svgNode('circle', { cx: x(values.length - 1), cy: y(last.value), r: 4, fill: '#94d5b4', stroke: '#183e2a', 'stroke-width': 4 }));
  svg.append(svgNode('text', { x: left, y: height - 9, fill: '#78909d', 'font-size': 10 }, values[0].date ? dateLabel(values[0].date) : '首个估值点'));
  svg.append(svgNode('text', { x: width - right, y: height - 9, fill: '#78909d', 'font-size': 10, 'text-anchor': 'end' }, last.date ? dateLabel(last.date) : '最新估值点'));
  container.append(svg);
}

function configOf(profile) {
  return profile?.config || profile || {};
}

function renderProfiles(preferredId) {
  const old = preferredId || $('profile-select').value;
  const paperProfile = $('paper-profile-select').value;
  $('profile-select').replaceChildren();
  $('paper-profile-select').replaceChildren(node('option', '', '原始策略 · 冻结基线'));
  $('paper-profile-select').firstElementChild.value = '';
  for (const profile of workspace.profiles || []) {
    const config = configOf(profile);
    const option = node('option', '', `${config.name || profile.id} · ${config.mode === 'custom' ? '自定义' : '基准过滤'}`);
    option.value = profile.id || profile.profile_id;
    $('profile-select').append(option);
    const paperOption = option.cloneNode(true);
    $('paper-profile-select').append(paperOption);
  }
  if (!$('profile-select').options.length) $('profile-select').append(node('option', '', '尚无实验配置'));
  if ([...$('profile-select').options].some((option) => option.value === old)) $('profile-select').value = old;
  if ([...$('paper-profile-select').options].some((option) => option.value === paperProfile)) $('paper-profile-select').value = paperProfile;
  $('run-experiment').disabled = !activeRun || !(workspace.profiles || []).length;
}

function selectProfile() {
  const selected = (workspace.profiles || []).find((profile) => (profile.id || profile.profile_id) === $('profile-select').value);
  if (!selected) return;
  const config = configOf(selected);
  for (const name of ['name', 'mode', 'rsi_period', 'rsi_min', 'rsi_max', 'macd_fast', 'macd_slow', 'macd_signal']) {
    if (config[name] !== undefined) $('profile-form').elements[name].value = config[name];
  }
  for (const name of ['rsi_enabled', 'macd_enabled']) $('profile-form').elements[name].checked = Boolean(config[name]);
  renderExperimentMode();
}

function renderExperimentMode() {
  $('experiment-mode-help').textContent = $('profile-form').elements.mode.value === 'custom'
    ? '独立按启用的指标判断，作为自定义实验呈现；不会改写原始策略。'
    : '在原策略判定上叠加启用的指标，观察哪些信号被保留。';
}

function checkSummary(checks) {
  if (!checks) return '';
  if (Array.isArray(checks)) return checks.map((check) => typeof check === 'string' ? check : `${check.label || check.name || check.key || '指标'} ${check.ok === true ? '通过' : check.ok === false ? '未过' : ''}${check.threshold ? ` · ${check.threshold}` : ''}${check.reason ? ` · ${check.reason}` : ''}`).join('；');
  return Object.entries(checks).map(([key, value]) => `${key}：${typeof value === 'boolean' ? (value ? '通过' : '未过') : typeof value === 'object' ? (value.reason || value.message || (value.ok ? '通过' : '未过')) : value}`).join('；');
}

function renderExperiment(output) {
  const results = output.results || [];
  $('experiment-table-body').replaceChildren();
  $('experiment-result-count').textContent = `${results.length} 只明细 · ${output.config?.name || '指标对照'}`;
  for (const result of results) {
    const tr = node('tr');
    const original = node('td');
    original.append(badge(result.baseline_verdict || result.baseline || '未提供'));
    const experimental = node('td');
    experimental.append(badge(result.verdict || result.experimental_verdict || '未提供'));
    const values = result.values || {};
    tr.append(node('td', 'ticker-cell', result.ticker), original, experimental, node('td', '', fmt(values.rsi, 1)), node('td', '', fmt(values.macd_histogram, 3)), node('td', 'cell-note', result.error || result.reason || checkSummary(result.checks) || '未提供额外说明'));
    $('experiment-table-body').append(tr);
  }
  $('experiment-empty').hidden = results.length > 0;
}

function renderValidation() {
  const baseline = workspace.baseline?.reference || workspace.baseline?.expected || workspace.baseline || {};
  $('baseline-facts').replaceChildren();
  for (const [label, value] of [
    ['基准判定', baseline.verdict || 'BUY'],
    ['冻结趋势线', fmt(baseline.trendline ?? baseline.trendline_level ?? 185.33)],
    ['水平结构', fmt(baseline.sr_flip ?? baseline.sr_level ?? 184.33)],
    ['止损 / 目标', `${fmt(baseline.stop ?? 184.91)} / ${fmt(baseline.target ?? 207.05)}`],
    ['参考盈亏比', `${fmt(baseline.rr ?? 4.25)}R`],
  ]) addFact($('baseline-facts'), label, value);
  $('baseline-verification').textContent = workspace.baseline?.summary || '这里展示冻结参考值；独立复跑结果见本轮验证回执。';
  const validation = workspace.validation || {};
  const receipt = $('validation-receipt');
  receipt.replaceChildren();
  if (validation.summary) receipt.append(node('p', 'muted-text', validation.summary));
  const checks = Array.isArray(validation.checks) ? validation.checks : [];
  if (checks.length) {
    const list = node('ul', 'receipt-list');
    for (const check of checks) {
      const item = node('li');
      const description = node('div');
      description.append(node('strong', '', check.label || check.name || check.title || '验证记录'));
      description.append(node('p', '', check.detail || check.summary || check.message || humanStatus(check.status)));
      item.append(node('span', 'receipt-mark', check.ok === true || check.status === 'passed' ? '✓' : '○'), description);
      list.append(item);
    }
    receipt.append(list);
  } else {
    const list = node('dl', 'source-facts');
    if (validation.status) addFact(list, '验证状态', humanStatus(validation.status));
    if (validation.as_of || validation.checked_at) addFact(list, '验证时间', dateLabel(validation.as_of || validation.checked_at, true));
    if (numeric(validation.passed)) addFact(list, '通过测试', fmt(validation.passed, 0));
    if (numeric(validation.failed)) addFact(list, '失败测试', fmt(validation.failed, 0));
    if (list.childElementCount) receipt.append(list);
    if (!validation.summary && !list.childElementCount) receipt.append(node('div', 'empty-state', '验证回执尚未载入。界面不会据此宣称测试或策略验证通过。'));
  }
  if (Array.isArray(validation.notes)) {
    const notes = node('ul', 'evidence-list');
    addEvidence(notes, validation.notes);
    receipt.append(notes);
  }
}

function renderWorkspace() {
  renderRunSelector();
  renderOverview();
  renderCandidates();
  renderProfiles();
  renderPaperAccounts();
  renderValidation();
  $('last-updated').textContent = `页面刷新 · ${dateLabel(new Date().toISOString(), true)} 北京时间`;
}

async function loadWorkspace() {
  const sequence = ++loadSequence;
  $('loading-state').hidden = false;
  $('global-error').hidden = true;
  $('refresh-button').disabled = true;
  const previousRun = activeRun?.run_id;
  try {
    const data = await api('api/state');
    let run = data.active_run;
    if (previousRun && (data.runs || []).some((item) => item.run_id === previousRun) && run?.run_id !== previousRun) {
      run = await api(`api/run?${new URLSearchParams({ id: previousRun })}`);
    }
    if (sequence !== loadSequence) return;
    workspace = data;
    activeRun = run?.run || run || null;
    renderWorkspace();
  } catch (error) {
    $('global-error').textContent = error.message;
    $('global-error').hidden = false;
  } finally {
    if (sequence === loadSequence) {
      $('loading-state').hidden = true;
      $('refresh-button').disabled = false;
    }
  }
}

$('refresh-button').addEventListener('click', loadWorkspace);
$('run-select').addEventListener('change', async () => {
  const runId = $('run-select').value;
  if (!runId) return;
  const sequence = ++loadSequence;
  $('loading-state').hidden = false;
  $('global-error').hidden = true;
  try {
    const run = await api(`api/run?${new URLSearchParams({ id: runId })}`);
    if (sequence !== loadSequence) return;
    activeRun = run.run || run;
    if ($('stock-dialog').open) $('stock-dialog').close();
    renderWorkspace();
    $('experiment-table-body').replaceChildren();
    $('experiment-empty').hidden = false;
    $('experiment-result-count').textContent = '快照已切换，请重新运行';
  } catch (error) {
    $('global-error').textContent = error.message;
    $('global-error').hidden = false;
    renderRunSelector();
  } finally {
    if (sequence === loadSequence) $('loading-state').hidden = true;
  }
});
document.querySelectorAll('[data-filter]').forEach((button) => {
  button.addEventListener('click', () => {
    candidateFilter = button.dataset.filter;
    document.querySelectorAll('[data-filter]').forEach((item) => {
      item.classList.toggle('active', item === button);
      item.setAttribute('aria-pressed', String(item === button));
    });
    renderCandidates();
  });
});
$('candidate-search').addEventListener('input', renderCandidates);
$('close-stock').addEventListener('click', () => $('stock-dialog').close());
$('stock-dialog').addEventListener('click', (event) => {
  if (event.target === $('stock-dialog')) {
    const box = $('stock-dialog').getBoundingClientRect();
    if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) $('stock-dialog').close();
  }
});
$('profile-form').elements.mode.addEventListener('change', renderExperimentMode);
$('profile-select').addEventListener('change', selectProfile);
$('profile-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = form.querySelector('button[type=submit]');
  const config = { name: form.elements.name.value.trim(), mode: form.elements.mode.value };
  for (const field of ['rsi_enabled', 'macd_enabled']) config[field] = form.elements[field].checked;
  for (const field of ['rsi_period', 'rsi_min', 'rsi_max', 'macd_fast', 'macd_slow', 'macd_signal']) config[field] = Number(form.elements[field].value);
  if (config.rsi_enabled && config.rsi_min > config.rsi_max) {
    setFormStatus('profile-form-status', 'RSI 下限需要小于或等于上限。', true);
    return;
  }
  if (config.macd_enabled && config.macd_fast >= config.macd_slow) {
    setFormStatus('profile-form-status', 'MACD 快线周期需要小于慢线周期。', true);
    return;
  }
  button.disabled = true;
  setFormStatus('profile-form-status', '正在保存独立配置…');
  try {
    const profile = await api('api/profile', { method: 'POST', body: JSON.stringify(config) });
    await loadWorkspace();
    renderProfiles(profile.id || profile.profile_id || profile.profile?.id);
    setFormStatus('profile-form-status', '配置已保存。可运行与当前快照的对照。');
    toast('独立指标配置已保存');
  } catch (error) {
    setFormStatus('profile-form-status', error.message, true);
  } finally {
    button.disabled = false;
  }
});
$('run-experiment').addEventListener('click', async () => {
  if (!activeRun || !$('profile-select').value) return;
  const button = $('run-experiment');
  button.disabled = true;
  setFormStatus('experiment-status', '正在用同一份数据计算对照…');
  try {
    const output = await api('api/experiment', { method: 'POST', body: JSON.stringify({ run_id: activeRun.run_id, profile_id: $('profile-select').value }) });
    renderExperiment(output);
    setFormStatus('experiment-status', '对照已完成。指标值与缺失原因列在下方。');
  } catch (error) {
    setFormStatus('experiment-status', error.message, true);
  } finally {
    button.disabled = false;
  }
});
$('paper-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  if (!activeRun) {
    setFormStatus('paper-form-status', '请先载入研究快照。', true);
    return;
  }
  const form = event.currentTarget;
  const button = form.querySelector('button[type=submit]');
  button.disabled = true;
  setFormStatus('paper-form-status', '正在建立验证并读取可用行情…');
  try {
    const account = await api('api/paper', {
      method: 'POST', body: JSON.stringify({
        run_id: activeRun.run_id, mode: form.elements.mode.value,
        initial_cash: Number(form.elements.initial_cash.value), risk_fraction: Number(form.elements.risk_percent.value) / 100,
        fee_bps: Number(form.elements.fee_bps.value), slippage_bps: Number(form.elements.slippage_bps.value),
        profile_id: form.elements.profile_id.value || null,
      }),
    });
    selectedPaper = account.account || account;
    await loadWorkspace();
    renderPaperAccounts();
    setFormStatus('paper-form-status', '验证账户已记录。成交、等待或数据缺口请查看右侧与下方台账。');
    toast('Paper 验证已记录');
  } catch (error) {
    setFormStatus('paper-form-status', error.message, true);
  } finally {
    button.disabled = false;
  }
});
$('paper-account-select').addEventListener('change', async () => {
  const id = $('paper-account-select').value;
  if (!id) return;
  try {
    const account = await api(`api/paper?${new URLSearchParams({ id })}`);
    selectedPaper = account.account || account;
    renderPaper();
  } catch (error) {
    setFormStatus('paper-form-status', error.message, true);
  }
});
window.addEventListener('hashchange', showView);
showView();
loadWorkspace();
