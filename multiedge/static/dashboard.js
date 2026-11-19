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
  stop_adjustment: '止损上调', stop_adjusted: '止损上调', trailing_enabled: '启用移动止损',
  ma20_rising_support: '20日均线带上行，按区间下沿与缓冲提高保护位',
  ma20_breached_next_session: '日线收盘已跌破均线保护位，等待下一可交易K线按止损或跳空开盘执行',
  higher_low_confirmed: '入场后确认了更高低点，按结构与缓冲提高保护位',
  higher_low_breached_next_session: '已跌破确认的更高低点保护位，等待下一可交易K线执行',
  no_higher_confirmed_support: '没有可上移的已确认支撑，保持原止损',
  intraday_coverage_gap: '盘中行情覆盖中断，暂停该标的处理',
  entry: '模拟买入', exit: '模拟卖出', skipped: '跳过', pending: '等待后续行情', filled: '模拟成交',
  invalid_stop: '缺少有效止损', missing_signal_session: '缺少信号日行情', late_import: '历史信号迟到导入，不补记成交',
  missing_entry_bar: '缺少入场日行情', already_holding: '已有该标的持仓', stop_at_or_above_entry: '止损不低于模拟入场价',
  target_at_or_below_entry: '目标不高于模拟入场价', insufficient_budget: '风险预算或现金不足',
  missing_observed_at: '缺少信号登记时间', invalid_observed_at: '信号登记时间无效',
  signal_observed_after_entry: '信号登记晚于入场时间，不回填交易', signal_observed_before_session: '登记时间早于信号交易日',
  account_started_after_entry: '账户登记晚于入场时间，不回填交易',
  calendar_unavailable: '缺少有效的交易日参考行情，暂停新买入', waiting_next_bar: '等待符合条件的下一根完整 5 分钟 K 线',
  waiting_next_session: '等待下一个实际交易日', stale_signal: '信号早于可核验的交易日范围，不补买',
  signal_expired: '已超过信号的下一交易日，信号作废',
  stop: '触及止损', target: '触及目标', stop_gap: '跳空穿越止损', target_gap: '跳空越过目标',
  trailing_stop: '触及移动止损', trailing_stop_gap: '跳空穿越移动止损',
  trailing_stop_and_target: '同根K线触及移动止损与目标，按止损优先处理',
  stop_and_target: '同一行情区间触及止损与目标，按止损优先处理',
  waiting_retest: '等待回踩支撑区并收复', retest_confirmed: '回踩已确认，等待后续K线开盘复核',
  waiting_entry_open: '等待确认后的可交易开盘', entry_confirmation: '回踩收复确认',
  missing_target: '缺少有效前高目标，不新买', invalid_target: '前高目标无效，不新买',
  confluent_higher_low_confirmed: '入场后更高低点与均线区共同确认，上移同一结构保护位',
  no_new_confluent_higher_low: '尚无入场后新确认的共振更高低点，保持原保护位',
  entry_plan_unavailable: '尚无可执行的结构买入计划', entry_plan_mismatch: '买入计划与本账户版本或价位不一致',
  invalid_daily_plan: '源日行情或结构核验失败，本次不买',
  signal_not_buy: '源报告未给出原始BUY，v2不升级WATCH或NO', missing_pullback: '缺少本次动能后的回调结构',
  missing_ma_confluence: '缺少成熟均线与另一结构的同区共振',
  insufficient_confluence: '缺少至少两类成熟结构共振', support_band_too_wide: '结构价位分散，未形成同区支撑',
  anchor_outside_support: '本次回调低点偏离支撑共振区', source_below_support: '源日收盘已跌破支撑区',
  source_above_entry_limit: '源日价格已超过允许入场范围', entry_below_support: '实际开盘已低于支撑区',
  entry_above_limit: '实际开盘已超过允许入场范围', entry_rr_below_minimum: '实际开盘扣成本后不足3R',
  reference_rr_below_minimum: '登记参考扣成本后不足3R', entry_price_confirmed: '实际开盘的结构与成本后盈亏比已通过',
  entry_plan_ready: '结构与参考盈亏比已通过，继续等待回踩确认',
  retest_coverage_gap: '登记后的回踩行情不连续，本次不买', retest_structure_broken: '登记后支撑结构已跌破，本次不买',
};

let workspace = { runs: [], profiles: [], paper_accounts: [] };
let activeRun = null;
let candidateFilter = 'ALL';
let selectedPaper = null;
let paperPnlAccountId = null;
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
  const replayed = run?.artifact_verification?.csv_json_key_fields_match && run?.config_verified && !partial && rows.length > 0 && rows.every((row) => row.report_available);
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

function paperStrategyVersion(value, account = {}) {
  return value.strategy_version || value.config?.strategy_version || value.risk_plan?.strategy_version || account.strategy_version || account.config?.strategy_version || '';
}

function paperPlan(value, account = {}) {
  const config = value.config || account.config || {};
  const mode = value.exit_mode || account.exit_mode || config.exit_mode || 'structural';
  const risk = value.risk_plan || {};
  const entry = numeric(value.entry_price) ? Number(value.entry_price) : null;
  const reference = entry ?? (numeric(risk.report_close) ? Number(risk.report_close) : null);
  const stopPrice = value.stop ?? risk.stop;
  const initialStopPrice = value.initial_stop ?? risk.stop ?? stopPrice;
  const targetPrice = value.target ?? risk.target;
  const stop = mode === 'structural'
    ? (numeric(reference) && Number(reference) > 0 && numeric(initialStopPrice) ? (Number(reference) - Number(initialStopPrice)) / Number(reference) : value.planned_stop_pct)
    : value.planned_stop_pct ?? value.stop_loss_pct ?? account.stop_loss_pct ?? config.stop_loss_pct;
  const target = mode === 'structural'
    ? (numeric(reference) && Number(reference) > 0 && numeric(targetPrice) ? (Number(targetPrice) - Number(reference)) / Number(reference) : value.planned_target_pct)
    : value.planned_target_pct ?? value.take_profit_pct ?? account.take_profit_pct ?? config.take_profit_pct;
  const version = paperStrategyVersion(value, account);
  const rr = version === 'structural_retest_v2' ? value.entry_checks?.net_rr ?? null : value.planned_rr ?? (numeric(stop) && Number(stop) > 0 && numeric(target) ? Number(target) / Number(stop) : null);
  const label = mode === 'fixed_percent'
    ? `自定义比例 · 止损 ${numeric(stop) ? fmt(Number(stop) * 100) : '—'}% / 止盈 ${numeric(target) ? fmt(Number(target) * 100) : '—'}%`
    : version === 'structural_retest_v2' ? '结构回踩确认 v2' : '旧结构版本 · 结构止损 / 前高目标';
  return { mode, stop, target, rr, label, version, stopPrice, initialStopPrice, targetPrice, referenceLabel: version === 'structural_retest_v2' ? '按实际成交与买卖成本' : entry === null ? '按信号收盘价' : '按初始风险' };
}

function paperRiskDetails(record) {
  const risk = record.risk_plan;
  if (risk?.strategy_version === 'structural_retest_v2') {
    const details = node('details', 'paper-risk-details');
    details.append(node('summary', '', '查看回踩与统一结构依据'));
    const list = node('ul', 'evidence-list');
    const checks = record.entry_checks || {};
    for (const text of [
      `源日 ${dateLabel(risk.source_as_of || risk.signal_date)} · 新版本独立登记，不转换旧持仓`,
      Array.isArray(risk.support_band) && risk.support_band.length === 2 ? `支撑共振带 ${risk.support_band.map(money).join(' – ')}` : '支撑共振带未提供',
      `本次回调锚 ${money(risk.anchor_price)} · ${dateLabel(risk.anchor_date)}`,
      `初始结构止损 ${money(risk.stop)} · 前高目标 ${money(risk.target)}`,
      `ATR ${fmt(risk.atr, 4)} · 价格缓冲 ${money(risk.buffer_value)}`,
      `登记参考成本后RR ${numeric(risk.reference_net_rr) ? `${fmt(risk.reference_net_rr)}R` : '不可评估'} · 最低 ${fmt(risk.minimum_net_rr || 3)}R`,
      numeric(checks.net_rr) ? `买入开盘复核成本后RR ${fmt(checks.net_rr)}R` : '实际买入成本后RR尚未确认',
      `来源快照 ${risk.source_run_id || '未提供'} · 版本 ${risk.strategy_version}`,
    ]) list.append(node('li', '', text));
    for (const [key, level] of Object.entries(risk.support_levels || {})) list.append(node('li', '', `${edgeLabels[key] || key} ${money(level)}`));
    if (numeric(risk.maximum_entry_price)) list.append(node('li', '', `允许入场上限 ${money(risk.maximum_entry_price)} · 回踩收复参考 ${money(risk.confirmation_close)}`));
    if (risk.source_fingerprint) list.append(node('li', '', `源日行情指纹 ${risk.source_fingerprint}`));
    if (risk.data_error) list.append(node('li', '', `数据核验：${risk.data_error}`));
    if (checks.expected_bar_start || checks.available_bar_start) list.append(node('li', '', `需连续行情自 ${paperTime(checks.expected_bar_start)}；实际可用自 ${paperTime(checks.available_bar_start)}`));
    if (risk.policy_assumption) list.append(node('li', '', risk.policy_assumption));
    details.append(list);
    return details;
  }
  if (!risk || risk.basis !== 'frozen_engine_structure') return null;
  const details = node('details', 'paper-risk-details');
  details.append(node('summary', '', '查看结构价位依据'));
  const list = node('ul', 'evidence-list');
  for (const text of [
    `信号日 ${dateLabel(risk.signal_date)} · 收盘 ${money(risk.report_close)}`,
    numeric(risk.stop_anchor) ? `E1 关键 K 线低点 ${money(risk.stop_anchor)} · ${risk.anchor_date ? dateLabel(risk.anchor_date) : '日期未提供'}` : 'E1 关键 K 线锚点未取得',
    `均线区参考值 ${money(risk.ma_zone_level)} · 冻结趋势线 ${money(risk.trendline_level)}`,
    Array.isArray(risk.mea_zone) && risk.mea_zone.length === 2 ? `MEA 汇合带 ${risk.mea_zone.map(money).join(' – ')}` : 'MEA 汇合带未提供',
    `初始结构止损 ${money(risk.stop)}${numeric(risk.buffer_atr) ? ` · ${fmt(risk.buffer_atr)}×ATR 缓冲（项目操作化假设）` : ''}`,
    numeric(risk.target) ? `前一波主要高点目标 ${money(risk.target)}` : '未取得有效前高目标；仅跟踪结构止损，R 不可评估',
    `来源快照 ${risk.source_run_id || '未提供'}${risk.source_revision ? ` · 规则 ${risk.source_revision}` : ''}`,
  ]) list.append(node('li', '', text));
  details.append(list);
  return details;
}

function paperTime(value) {
  return dateLabel(value, /[T ]\d{2}:\d{2}/.test(String(value || '')));
}

function paperPriceCell(price, time, note) {
  const cell = node('td', 'paper-price-cell');
  cell.append(node('span', '', money(price)));
  if (time) cell.append(node('small', 'check-detail', paperTime(time)));
  if (note) cell.append(node('small', 'check-detail', note));
  return cell;
}

function paperTickerCell(record) {
  const cell = node('td', 'ticker-cell', record.ticker);
  cell.append(node('small', '', `${fmt(record.quantity)} 股`));
  return cell;
}

function paperPnlCell(value, unavailable = '—') {
  return node('td', numeric(value) ? (Number(value) >= 0 ? 'positive numeric' : 'negative numeric') : 'numeric', numeric(value) ? money(value) : unavailable);
}

function renderPaperDailyPnl(data) {
  const report = data.daily_pnl || {};
  const rows = [...(Array.isArray(report.rows) ? report.rows : [])].sort((a, b) => String(b.date).localeCompare(String(a.date)) || String(a.ticker).localeCompare(String(b.ticker)));
  const changedAccount = paperPnlAccountId !== (data.account_id || null);
  paperPnlAccountId = data.account_id || null;
  for (const [id, key, label] of [['paper-pnl-ticker', 'ticker', '全部股票'], ['paper-pnl-date', 'date', '全部交易日']]) {
    const select = $(id);
    const previous = changedAccount ? '' : select.value;
    const values = [...new Set(rows.map((row) => row[key]).filter(Boolean))].sort();
    if (key === 'date') values.reverse();
    const all = node('option', '', label);
    all.value = '';
    select.replaceChildren(all);
    for (const value of values) {
      const option = node('option', '', value);
      option.value = value;
      select.append(option);
    }
    if (values.includes(previous)) select.value = previous;
    select.disabled = !rows.length;
  }
  const ticker = $('paper-pnl-ticker').value;
  const day = $('paper-pnl-date').value;
  const visible = rows.filter((row) => (!ticker || row.ticker === ticker) && (!day || row.date === day));
  const summaries = (Array.isArray(report.summaries) ? report.summaries : []).filter((row) => !ticker || row.ticker === ticker);
  $('paper-pnl-summaries').replaceChildren();
  for (const summary of summaries) {
    const card = node('div', 'paper-pnl-summary');
    card.append(node('span', '', `${summary.ticker} · ${summary.status === 'closed' ? '已平仓' : '持仓中'} · 累计净盈亏`));
    card.append(node('strong', numeric(summary.cumulative_pnl) ? (Number(summary.cumulative_pnl) >= 0 ? 'positive' : 'negative') : '', money(summary.cumulative_pnl)));
    card.append(node('small', '', `最近交易日 ${dateLabel(summary.date)} · 当日 ${numeric(summary.daily_pnl) ? money(summary.daily_pnl) : '不可计算'}`));
    if (summary.quality === 'partial' || summary.quality === 'missing') card.append(node('small', 'warm', summary.quality === 'partial' ? '截至有效行情，未完成日终' : '数据有缺口，详见每日明细'));
    $('paper-pnl-summaries').append(card);
  }
  $('paper-pnl-summaries').hidden = !summaries.length;
  const stockCount = new Set(rows.map((row) => row.ticker)).size;
  $('paper-daily-count').textContent = `${stockCount} 只股票 · ${visible.length} / ${rows.length} 条每日记录`;
  const notes = Array.isArray(report.notes) ? report.notes : [];
  $('paper-daily-note').textContent = notes.join('。');
  $('paper-daily-note').hidden = !notes.length;
  const body = $('paper-daily-body');
  body.replaceChildren();
  const basisLabels = { paper_ledger: '实际 Paper 估值', archive_reconstruction: '依据已归档行情重建', trade_ledger: '成交台账', mixed: '台账与归档行情' };
  for (const row of visible) {
    const tr = node('tr');
    const stock = node('td', 'ticker-cell', row.ticker);
    stock.append(node('small', '', row.status === 'closed' ? '已平仓' : '持仓中'));
    const components = node('td', 'paper-price-cell', money(row.realized_pnl));
    components.append(node('small', 'check-detail', `未实现 ${money(row.unrealized_pnl)}`));
    const quality = node('td', 'cell-note');
    quality.append(badge(row.quality === 'complete' ? (row.status === 'closed' ? '当日已平仓' : '日终已完成') : row.quality === 'partial' ? '未完成日终' : '数据有缺口', row.quality === 'complete' ? 'muted' : 'amber'));
    quality.append(node('small', 'check-detail', basisLabels[row.basis] || row.basis || '依据未提供'));
    if (row.missing_reason) quality.append(node('small', 'check-detail', row.missing_reason));
    tr.append(node('td', 'numeric', dateLabel(row.date)), stock, paperPnlCell(row.daily_pnl, '不可计算'), paperPnlCell(row.cumulative_pnl), components, node('td', 'numeric', money(row.fees)), node('td', 'numeric', `${fmt(row.quantity)} 股`), paperPriceCell(row.mark, row.mark_at), quality);
    body.append(tr);
  }
  $('paper-daily-empty').hidden = visible.length > 0;
  $('paper-daily-empty').textContent = rows.length
    ? '当前筛选下没有每日盈亏记录。'
    : selectedPaper && ((data.positions || []).length || (data.trades || []).length)
      ? '尚未取得可用的逐股每日估值；历史缺口不会显示为零收益。'
      : '模拟成交后，在这里查看每只股票的逐日盈亏；未成交订单不计入盈亏。';
}

function renderPaperEntryAudits(data) {
  const v2 = paperStrategyVersion(data) === 'structural_retest_v2';
  const audits = Array.isArray(data.entry_audits) ? data.entry_audits : v2 ? (data.orders || []) : [];
  const rows = [...audits].sort((a, b) => String(b.checked_at || b.observed_at || b.signal_date || '').localeCompare(String(a.checked_at || a.observed_at || a.signal_date || '')));
  const filled = rows.filter((row) => row.status === 'filled').length;
  const rejected = rows.filter((row) => row.status === 'skipped' || row.status === 'rejected').length;
  $('paper-entry-count').textContent = `${rows.length} 个候选 · ${filled} 已成交 / ${rows.length - filled - rejected} 等待 / ${rejected} 拒绝`;
  $('paper-entry-policy-note').textContent = !selectedPaper
    ? '选择账户后显示买入前复核规则。'
    : !v2
      ? '此账户保留旧版本的原始买入与出场规则；不会把新版本条件追溯应用于已有持仓或历史成交。'
      : `结构回踩确认 v2 · 每个新候选先核验同区结构与有效前高，完整K线回踩收复后，下一合格开盘重新计算成本后盈亏比，至少 ${fmt(data.entry_policy?.minimum_net_rr || 3)}R 才能模拟买入。初始止损与后续上调沿用同类结构，新旧版本分别记账。${data.entry_policy?.description ? ` ${data.entry_policy.description}` : ''}`;
  const body = $('paper-entry-body');
  body.replaceChildren();
  for (const audit of rows) {
    const risk = audit.risk_plan || {};
    const checks = audit.entry_checks || {};
    const confirmation = checks.confirmation_bar;
    const tr = node('tr');
    const stock = node('td', 'ticker-cell', audit.ticker || '—');
    stock.append(node('small', '', `信号 ${dateLabel(audit.signal_date || risk.source_as_of)}`));
    const decision = node('td', 'cell-note');
    const rejectedRow = ['skipped', 'rejected'].includes(audit.status);
    const status = audit.status === 'filled' ? '已模拟买入' : rejectedRow ? '拒绝本次买入' : confirmation ? '回踩已确认，等待开盘复核' : '等待回踩确认';
    decision.append(badge(status, audit.status === 'filled' ? 'buy' : rejectedRow ? 'amber' : 'muted'));
    const reason = checks.reason || audit.reason || risk.reason;
    if (reason) decision.append(node('small', 'check-detail', paperLabels[reason] || reason));
    const times = audit.checked_at || checks.checked_at || audit.observed_at;
    if (times) decision.append(node('small', 'check-detail', `核验 ${paperTime(times)}`));
    const structure = node('td', 'paper-price-cell');
    structure.append(node('span', '', Array.isArray(risk.support_band) && risk.support_band.length === 2 ? `${risk.support_band.map(money).join(' – ')}` : '支撑区未通过核验'));
    if (numeric(risk.anchor_price)) structure.append(node('small', 'check-detail', `回调锚 ${money(risk.anchor_price)} · ${dateLabel(risk.anchor_date)}`));
    structure.append(node('small', 'check-detail', `止损 ${money(risk.stop)} / 目标 ${money(risk.target)}`));
    const details = paperRiskDetails(audit);
    if (details) structure.append(details);
    const retest = node('td', 'paper-price-cell');
    if (confirmation) {
      retest.append(node('span', '', `${paperTime(confirmation.timestamp)} – ${paperTime(confirmation.end)}`));
      retest.append(node('small', 'check-detail', `最低 ${money(confirmation.low)} · 收盘 ${money(confirmation.close)}`));
      if (checks.confirmation_observed_at) retest.append(node('small', 'check-detail', `实际确认 ${paperTime(checks.confirmation_observed_at)}`));
      retest.append(node('small', 'check-detail', '确认K线内不回填买入'));
    } else retest.append(node('span', '', rejectedRow ? '本次计划未放行' : '尚无合格完整回踩K线'));
    const rr = node('td', 'paper-price-cell');
    rr.append(node('span', '', numeric(checks.net_rr) ? `开盘复核 ${fmt(checks.net_rr)}R` : '开盘复核尚未完成'));
    rr.append(node('small', 'check-detail', `登记参考 ${numeric(risk.reference_net_rr) ? `${fmt(risk.reference_net_rr)}R` : '不可评估'} · 最低 ${fmt(risk.minimum_net_rr || data.entry_policy?.minimum_net_rr || 3)}R`));
    rr.append(node('small', 'check-detail', '均按买卖费用与滑点计算'));
    tr.append(stock, decision, structure, retest, rr, paperPriceCell(audit.entry_price ?? checks.entry_price, audit.entry_timestamp || checks.entry_timestamp, audit.status === 'filled' ? '实际模拟入场' : '尚未模拟成交'));
    body.append(tr);
  }
  $('paper-entry-empty').hidden = rows.length > 0;
  $('paper-entry-empty').textContent = v2
    ? '新版本已独立登记，尚无买入前复核记录。后续BUY候选的等待、拒绝和成交依据都会保留；无合格机会时不制造交易。'
    : '此旧版本没有新规则的买入前审计；原成交与每日盈亏仍可在下方查看。';
}

function paperStopUpdateDetails(update) {
  const evidence = update.evidence || {};
  const v2 = update.policy_version === 'structural_retest_v2' || evidence.strategy_version === 'structural_retest_v2';
  const details = node('details', 'paper-risk-details paper-trailing-details');
  details.append(node('summary', '', '查看本次判断依据'));
  const list = node('ul', 'evidence-list');
  list.append(node('li', '', `行情截止日 ${dateLabel(evidence.data_as_of || update.source_as_of)}${update.source_completed_at ? ` · 日线完成校验截止 ${paperTime(update.source_completed_at)}` : ''}`));
  if (numeric(evidence.ma20) || numeric(evidence.ema20)) list.append(node('li', '', `20MA ${money(evidence.ma20)} · 20EMA ${money(evidence.ema20)} · 区间下沿 ${money(evidence.ma_band_low)}`));
  if (numeric(evidence.atr)) list.append(node('li', '', `ATR ${fmt(evidence.atr, 4)} · 缓冲 ${fmt(evidence.buffer_atr)}×ATR = ${money(evidence.buffer_value)}`));
  if (numeric(evidence.pivot_low)) list.append(node('li', '', `确认低点 ${money(evidence.pivot_low)} · ${dateLabel(evidence.pivot_date)} · 确认于 ${dateLabel(evidence.pivot_confirmed_at)}${numeric(evidence.previous_pivot_low) ? ` · 前一低点 ${money(evidence.previous_pivot_low)}` : ''}`));
  else list.append(node('li', '', '尚无可用的确认更高低点，结构候选不参与本次上调'));
  if (numeric(evidence.ma_candidate) || numeric(evidence.structure_candidate)) list.append(node('li', '', v2
    ? `共振结构候选 ${money(evidence.structure_candidate)} · 仅在更高低点与均线区共同确认后上调`
    : `均线候选 ${money(evidence.ma_candidate)} · 结构候选 ${money(evidence.structure_candidate)} · 采用${evidence.selected_basis === 'higher_low' ? '确认的更高低点' : evidence.selected_basis === 'ma20' ? '20日均线区' : '本次记录的依据'}`));
  list.append(node('li', '', v2
    ? 'v2量化定义：入场后的确认更高低点与均线区共同形成支撑，按较低结构参考减ATR缓冲，只允许上移；孤立均线上行不会切换原保护依据。'
    : '旧版本量化定义：20MA/EMA20区间下沿或左右各3根确认的更高低点，留ATR缓冲后取较高候选；按美分向下取整，只允许上移。'));
  if (['applied', 'pending'].includes(update.status) && update.requested_effective_at && update.requested_effective_at !== update.effective_at) list.append(node('li', '', `计划生效 ${paperTime(update.requested_effective_at)}；实际生效见本次记录`));
  if (update.policy_version) list.append(node('li', '', `规则版本 ${update.policy_version}`));
  if (evidence.data_fingerprint) list.append(node('li', '', `行情快照指纹 ${evidence.data_fingerprint}`));
  if (Array.isArray(evidence.source_snapshots) && evidence.source_snapshots.length) list.append(node('li', '', `依据 ${evidence.source_snapshots.length} 份保存的行情来源快照`));
  if (update.source_signal_id) list.append(node('li', '', `关联入场信号 ${update.source_signal_id}`));
  details.append(list);
  return details;
}

function renderPaperStopAdjustments(data) {
  const policy = data.trailing_policy;
  const v2 = paperStrategyVersion(data) === 'structural_retest_v2';
  const adjustments = Array.isArray(data.stop_adjustments) ? data.stop_adjustments : [];
  const applied = new Map(adjustments.map((update) => [update.update_id, { ...update, status: 'applied' }]));
  const proposals = (Array.isArray(data.stop_updates) ? data.stop_updates : []).filter((update) => !applied.has(update.update_id));
  const rows = [...applied.values(), ...proposals].sort((a, b) => String(b.applied_at || b.recorded_at || b.observed_at || b.effective_at || '').localeCompare(String(a.applied_at || a.recorded_at || a.observed_at || a.effective_at || '')));
  const pending = proposals.filter((update) => update.status === 'pending').length;
  $('paper-stop-adjustment-count').textContent = `${adjustments.length} 次已生效上调${pending ? ` · ${pending} 项待生效` : ''}`;
  $('paper-trailing-policy-note').textContent = !selectedPaper
    ? '选择账户后显示移动止损规则与生效时间。'
    : data.automation_disabled
      ? '该历史账户已停用；这里只展示原样保留的评估与调整记录。'
      : policy
        ? `${v2 ? '新版本自账户登记起冻结结构跟踪规则，不中途转换旧持仓。' : '旧版本移动止损已启用。'}${policy.enabled_at ? ` ${paperTime(policy.enabled_at)} 北京时间登记。` : ''}${v2 ? ` ${policy.description || '仅按入场后的确认更高低点与均线共振评估上调，沿用初始结构依据。'}` : '按已完成日线评估20日均线区和确认的更高低点。'}后续合格交易时段生效，盘中每5分钟检查；只提高、不放宽，初始风险与R基准保持不变。${policy.version || policy.policy_version ? `规则 ${policy.version || policy.policy_version}。` : ''}`
        : '此账户未启用移动止损，继续按已登记的初始出场价位执行。';
  const body = $('paper-stop-adjustments-body');
  body.replaceChildren();
  const labels = { applied: '已生效上调', pending: '待生效计划', no_change: '无需上调', position_closed: '持仓已关闭' };
  for (const update of rows) {
    const row = node('tr');
    const ticker = node('td', 'ticker-cell', update.ticker || '—');
    ticker.append(node('small', '', labels[update.status] || humanStatus(update.status)));
    const prices = node('td', 'paper-price-cell');
    prices.append(node('span', '', update.status === 'applied' ? `${money(update.old_stop)} → ${money(update.new_stop)}` : `候选 ${money(update.new_stop)}`));
    if (update.status !== 'applied') prices.append(node('small', 'check-detail', '候选尚未改变实际止损'));
    const times = node('td', 'paper-price-cell');
    if (update.observed_at) times.append(node('span', '', `登记 ${paperTime(update.observed_at)}`));
    const effective = update.status === 'applied' ? update.effective_at : update.status === 'pending' ? update.requested_effective_at || update.effective_at : null;
    if (effective) times.append(node('small', 'check-detail', `${update.status === 'applied' ? '生效' : '计划'} ${paperTime(effective)}`));
    if (update.applied_at || update.recorded_at) times.append(node('small', 'check-detail', `核验 ${paperTime(update.applied_at || update.recorded_at)}`));
    const reason = node('td', 'cell-note', paperLabels[update.reason] || update.reason || (update.evidence?.selected_basis === 'higher_low' ? '确认的更高低点提高保护位' : '均线区提高保护位'));
    reason.append(paperStopUpdateDetails(update));
    row.append(ticker, prices, times, reason);
    body.append(row);
  }
  $('paper-stop-adjustments-empty').hidden = rows.length > 0;
  $('paper-stop-adjustments-empty').textContent = policy
    ? '尚无移动止损评估或已生效上调；只使用判断时已可用的完整行情，实际启用后才执行，不补记此前调整。'
    : '尚无移动止损调整记录。';
}

function renderPaperFormPlan() {
  const form = $('paper-form');
  const fixed = form.elements.exit_mode.value === 'fixed_percent';
  const versioned = !fixed && form.elements.mode.value === 'forward';
  form.elements.strategy_version.disabled = !versioned;
  const v2 = versioned && form.elements.strategy_version.value === 'structural_retest_v2';
  const stop = Number(form.elements.stop_loss_percent.value);
  const target = Number(form.elements.take_profit_percent.value);
  $('paper-percent-fields').hidden = !fixed;
  for (const name of ['stop_loss_percent', 'take_profit_percent']) form.elements[name].disabled = !fixed;
  $('paper-plan-preview').textContent = v2
    ? '结构回踩确认 v2：BUY先成为候选，完整K线回踩收复后，下一合格开盘复核成本后RR至少3R；无有效目标不新买。初始止损与后续更高低点跟踪沿用同类结构，独立记账，不转换旧持仓。'
    : !fixed
      ? '旧结构口径：读取信号日结构价位，在原规则下验证；无有效前高时仅跟踪结构止损。不会给旧持仓追加v2规则。'
    : stop > 0 && target > 0
      ? `自定义实验：按模拟买入价设置止损 ${fmt(stop)}%、止盈 ${fmt(target)}%，计划约 ${fmt(target / stop)}R，未扣成本。这组比例不是视频的通用规则。`
      : '填写有效的止损距离和止盈目标后，显示计划盈亏比。';
}

function renderPaperAccounts() {
  const priority = (account) => account.automation_disabled ? 3 : paperStrategyVersion(account) === 'structural_retest_v2' ? 0 : (paperPlan(account).mode === 'structural' && (account.execution_model || account.config?.execution_model) === 'intraday_5m' ? 1 : 2);
  const accounts = [...(workspace.paper_accounts || [])].sort((a, b) => priority(a) - priority(b));
  const selected = selectedPaper?.account_id || $('paper-account-select').value;
  $('paper-account-select').replaceChildren();
  for (const account of accounts) {
    const data = paperLedger(account);
    const profile = (workspace.profiles || []).find((item) => (item.id || item.profile_id) === data.profile_id);
    const strategy = data.profile_id ? (configOf(profile).name || '独立指标实验') : '原始策略';
    const option = node('option', '', `${data.automation_disabled ? '已停用 · 旧示例记录' : humanStatus(data.mode)} · ${strategy} · ${paperPlan(data).label}${data.trailing_policy && !data.automation_disabled ? ' · 移动止损' : ''} · ${data.as_of || data.created_at?.slice(0, 10) || data.account_id}`);
    option.value = account.account_id;
    $('paper-account-select').append(option);
  }
  if (!accounts.length) $('paper-account-select').append(node('option', '', '尚无验证账户'));
  if (accounts.some((account) => account.account_id === selected)) $('paper-account-select').value = selected;
  const current = accounts.find((account) => account.account_id === $('paper-account-select').value);
  if (current) selectedPaper = current;
  else if (!accounts.length) selectedPaper = null;
  renderPaperHistory();
  renderPaper();
}

function renderPaperHistory() {
  const mode = $('paper-history-mode').value;
  const accounts = (workspace.paper_accounts || []).map(paperLedger).filter((account) => account.mode === mode);
  const rows = accounts.flatMap((account) => (account.trades || []).map((trade) => ({ account, trade })));
  rows.sort((a, b) => String(b.trade.exit_timestamp || b.trade.exit_date).localeCompare(String(a.trade.exit_timestamp || a.trade.exit_date)) || a.trade.ticker.localeCompare(b.trade.ticker));
  $('paper-history-count').textContent = `${accounts.length} 个账户 · ${rows.length} 笔已平仓交易`;
  const body = $('paper-history-body');
  body.replaceChildren();
  for (const { account, trade } of rows) {
    const tr = node('tr');
    const source = node('td', 'cell-note', paperPlan(account).label);
    source.append(node('small', 'check-detail', account.automation_disabled ? '已停用账户 · 原记录保留' : humanStatus(account.mode)));
    const pnl = paperPnlCell(trade.net_pnl);
    if (numeric(trade.realized_r)) pnl.append(node('small', 'check-detail', `按初始风险 ${fmt(trade.realized_r)}R`));
    const action = node('td');
    const button = node('button', 'paper-history-open', '查看原账户');
    button.type = 'button';
    button.setAttribute('aria-label', `查看 ${trade.ticker} 的原账户与每日盈亏`);
    button.addEventListener('click', () => {
      $('paper-account-select').value = account.account_id;
      $('paper-account-select').dispatchEvent(new Event('change'));
      $('paper-account-select').scrollIntoView({ behavior: 'smooth', block: 'center' });
    });
    action.append(button);
    tr.append(paperTickerCell(trade), source,
      paperPriceCell(trade.entry_price, trade.entry_timestamp || trade.entry_date),
      paperPriceCell(trade.exit_price, trade.exit_timestamp || trade.exit_date),
      node('td', 'cell-note', paperLabels[trade.exit_reason] || trade.exit_reason || '未提供'), pnl, action);
    body.append(tr);
  }
  $('paper-history-empty').hidden = rows.length > 0;
  $('paper-history-empty').textContent = mode === 'forward'
    ? '所有前向账户尚无已平仓交易；未成交候选与仍在持有的股票不会记作平仓。'
    : '尚无历史回放的平仓记录。';
}

function renderPaper() {
  const data = paperLedger(selectedPaper);
  const positions = Array.isArray(data.positions) ? data.positions : [];
  const trades = Array.isArray(data.trades) ? data.trades : [];
  const plan = paperPlan(data);
  const v2 = plan.version === 'structural_retest_v2';
  $('paper-account-plan-title').textContent = selectedPaper ? `${plan.label}${data.trailing_policy && !data.automation_disabled ? ' + 移动止损' : ''}` : '等待选择账户';
  $('paper-account-plan-detail').textContent = !selectedPaper
    ? '成交后显示冻结的止损、止盈与实际执行记录。'
    : plan.mode === 'fixed_percent'
      ? `计划盈亏比约 ${fmt(plan.rr)}R，未扣费用与滑点。BUY 登记后等待符合条件的真实行情；以模拟买入价冻结止损与止盈，按后续股价验证出场。`
      : v2
        ? '本版本从真实登记时间开始独立运行。买入前确认回踩、有效前高与至少3R成本后盈亏比；初始保护和后续上调都基于同类共振结构，旧两笔亏损保留在旧账户。'
        : `旧版本保留初始结构与风险计划${data.trailing_policy ? '及已登记的移动止损规则，初始R基准不变' : ''}。无有效前高时跟踪结构止损；不会将新v2规则应用于此账户的已有持仓。`;
  const execution = data.execution_model || data.config?.execution_model;
  const monitor = data.monitor || {};
  const quoteSources = Object.entries(monitor.quote_sources || {});
  const monitoring = [];
  if (selectedPaper) monitoring.push(data.automation_disabled ? '旧示例已停用 · 仅查看保留的账本' : execution === 'intraday_5m' ? '盘中每 5 分钟检查（真实 5 分钟 K 线模拟）' : '日线 OHLC 模拟');
  if (monitor.last_checked_at || data.last_checked_at) monitoring.push(`最近检查 ${paperTime(monitor.last_checked_at || data.last_checked_at)}（北京时间）`);
  if (data.data_as_of) monitoring.push(`行情截至 ${paperTime(data.data_as_of)}`);
  if (quoteSources.length) {
    monitoring.push(`${quoteSources.length} 个行情来源 · ${quoteSources.filter(([, source]) => source.stale).length} 个过期`);
    if (quoteSources.some(([, source]) => !numeric(source.exchange_delay_seconds))) monitoring.push('交易所行情延迟未核实');
  }
  $('paper-monitor-note').textContent = monitoring.join(' · ');
  $('paper-equity').textContent = money(data.equity);
  $('paper-cash').textContent = money(data.cash);
  $('paper-counts').textContent = selectedPaper ? `${positions.length} / ${trades.length}` : '—';
  $('paper-mode-note').textContent = selectedPaper ? (data.automation_disabled ? '已停用 · 历史快照' : humanStatus(data.mode)) : '等待验证账户';
  $('paper-as-of').textContent = data.data_as_of ? `${data.automation_disabled ? '保留快照截至' : '行情截至'} ${paperTime(data.data_as_of)}` : '尚未取得估值行情';
  const gaps = [];
  if (data.automation_disabled) gaps.push(data.legacy_reason || '此旧示例已停用，历史价格、规则与成交保留原样；不会新增信号或自动推进');
  if (data.data_missing?.length) gaps.push(`行情缺失：${data.data_missing.join('、')}`);
  if (data.data_stale) gaps.push(`行情未通过时效检查${data.data_as_of ? `（最新 ${paperTime(data.data_as_of)}）` : ''}`);
  for (const [ticker, error] of Object.entries(monitor.errors || {})) gaps.push(`${ticker} 行情检查失败：${error}`);
  for (const [ticker, error] of Object.entries(monitor.trailing_errors || {})) gaps.push(`${ticker} 移动止损计算暂停：${error}`);
  if (selectedPaper && !positions.length && !trades.length) gaps.push('尚无模拟成交记录，当前资金不代表策略验证收益');
  $('paper-data-note').hidden = !gaps.length;
  $('paper-data-note').textContent = gaps.join('。');
  renderPaperEntryAudits(data);
  renderPaperDailyPnl(data);
  $('paper-position-rr-heading').textContent = v2 ? '初始成本后计划盈亏比' : '初始计划盈亏比';
  $('paper-position-note').textContent = `${positions.length} 只持仓${data.automation_disabled ? ' · 停用时快照' : ''}`;
  $('paper-positions-body').replaceChildren();
  for (const position of positions) {
    const tr = node('tr');
    const positionPlan = paperPlan(position, data);
    const status = node('td');
    status.append(badge(data.automation_disabled ? '历史快照' : position.stale ? '估值过期' : (position.mark_timestamp || position.mark_time || position.mark_at || position.mark_date ? '有估值' : '等待估值'), position.stale || data.automation_disabled ? 'amber' : 'muted'));
    const relativeStop = numeric(position.stop) && numeric(position.entry_price) && Number(position.entry_price) > 0 ? (Number(position.stop) / Number(position.entry_price) - 1) * 100 : null;
    const stopNote = numeric(relativeStop) ? `${relativeStop >= 0 ? '高于' : '低于'}买入价 ${fmt(Math.abs(relativeStop))}%` : '结构止损';
    const targetNote = numeric(positionPlan.target) ? `距买入价 ${fmt(Number(positionPlan.target) * 100)}%` : '未取得有效前高 · 仅跟踪止损';
    const stopCell = paperPriceCell(position.stop, null, stopNote);
    stopCell.append(node('small', 'check-detail', `初始止损 ${money(positionPlan.initialStopPrice)}${numeric(positionPlan.stop) ? ` · 初始风险 ${fmt(Number(positionPlan.stop) * 100)}%` : ''}`));
    const riskDetails = paperRiskDetails(position);
    if (riskDetails) stopCell.append(riskDetails);
    const floating = ['quantity', 'mark', 'entry_price', 'entry_fee'].every((key) => numeric(position[key])) ? Number(position.quantity) * (Number(position.mark) - Number(position.entry_price)) - Number(position.entry_fee) : null;
    const floatingCell = paperPnlCell(floating);
    floatingCell.append(node('small', 'check-detail', '已扣买入费用；未扣预计卖出费用'));
    tr.append(paperTickerCell(position), paperPriceCell(position.entry_price, position.entry_timestamp || position.entry_time || position.entry_at || position.entry_date), paperPriceCell(position.mark, position.mark_timestamp || position.mark_time || position.mark_at || position.mark_date), floatingCell, stopCell, paperPriceCell(position.target, null, targetNote), node('td', '', numeric(positionPlan.rr) ? `${fmt(positionPlan.rr)}R` : '不可评估'), status);
    $('paper-positions-body').append(tr);
  }
  $('paper-positions-empty').hidden = positions.length > 0;
  $('paper-trade-note').textContent = `当前账户 ${trades.length} 笔完成交易 · 净损益已扣买卖费用`;
  $('paper-trades-body').replaceChildren();
  for (const trade of [...trades].reverse()) {
    const tr = node('tr');
    const levels = node('td', 'paper-price-cell', `${money(trade.stop)} / ${money(trade.target)}`);
    const tradePlan = paperPlan(trade, data);
    levels.append(node('small', 'check-detail', `${tradePlan.label}${numeric(tradePlan.rr) ? ` · ${fmt(tradePlan.rr)}R` : ''}`));
    if (!numeric(trade.target)) levels.append(node('small', 'check-detail', '未取得有效前高 · 仅跟踪结构止损'));
    const riskDetails = paperRiskDetails(trade);
    if (riskDetails) levels.append(riskDetails);
    const reason = trade.exit_reason || '未提供';
    const pnl = node('td', numeric(trade.net_pnl) ? (Number(trade.net_pnl) >= 0 ? 'positive' : 'negative') : '', money(trade.net_pnl));
    if (numeric(trade.realized_r)) pnl.append(node('small', 'check-detail', `按初始风险 ${fmt(trade.realized_r)}R`));
    if (numeric(trade.initial_stop)) levels.append(node('small', 'check-detail', `初始止损 ${money(trade.initial_stop)}`));
    tr.append(paperTickerCell(trade), paperPriceCell(trade.entry_price, trade.entry_timestamp || trade.entry_time || trade.entry_at || trade.entry_date), paperPriceCell(trade.exit_price, trade.exit_timestamp || trade.exit_time || trade.exit_at || trade.exit_date), levels, node('td', 'cell-note', paperLabels[reason] || reason), node('td', '', `${money(trade.entry_fee)} / ${money(trade.exit_fee)}`), pnl);
    $('paper-trades-body').append(tr);
  }
  $('paper-trades-empty').hidden = trades.length > 0;
  renderPaperStopAdjustments(data);
  const pending = (data.orders || []).filter((order) => order.status === 'pending');
  const events = [...(Array.isArray(data.events) && data.events.length ? data.events : [...(data.orders || []).filter((order) => order.status !== 'pending'), ...trades]), ...pending];
  $('paper-events-body').replaceChildren();
  for (const event of events.slice(-100).reverse()) {
    const tr = node('tr');
    const sourceOrder = (data.orders || []).find((order) => order.signal_id === (event.signal_id || event.source_signal_id));
    const record = { ...sourceOrder, ...event, risk_plan: event.risk_plan || sourceOrder?.risk_plan };
    const detail = event.reason || event.exit_reason || event.message || event.description || event.note || (event.status === 'pending' ? '等待符合成交条件的后续行情' : '');
    const kind = event.status || event.action || event.kind || event.type || event.side || '记录';
    const eventPlan = paperPlan(record, data);
    const adjustedStop = numeric(event.old_stop) && numeric(event.new_stop);
    const detailText = adjustedStop ? `${money(event.old_stop)} → ${money(event.new_stop)}。${paperLabels[event.reason] || event.reason || '已确认的结构或均线提高保护位'}；完整依据见移动止损记录。` : event.status === 'pending'
      ? `${paperLabels[detail] || detail}。${eventPlan.label}${eventPlan.mode === 'structural' ? `：止损 ${money(eventPlan.stopPrice)} / 目标 ${numeric(eventPlan.targetPrice) ? money(eventPlan.targetPrice) : '不可得，仅跟踪止损'}` : ''}${numeric(eventPlan.rr) ? ` · ${eventPlan.referenceLabel}约 ${fmt(eventPlan.rr)}R，未扣成本` : ' · R 不可评估'}。${event.signal_date ? `信号日 ${dateLabel(event.signal_date)}。` : ''}`
      : paperLabels[detail] || detail;
    const detailCell = node('td', 'cell-note', detailText);
    const riskDetails = adjustedStop ? null : paperRiskDetails(record);
    if (riskDetails) detailCell.append(riskDetails);
    tr.append(node('td', '', paperTime(event.timestamp || event.exit_timestamp || event.entry_timestamp || event.exit_time || event.entry_time || event.date || event.exit_at || event.exit_date || event.as_of || event.observed_at || event.signal_date)), node('td', 'ticker-cell', event.ticker || '—'), node('td', '', paperLabels[kind] || kind), node('td', '', event.status === 'pending' ? '等待模拟成交' : `${fmt(event.quantity)} / ${money(event.price ?? event.fill_price ?? event.exit_price)}`), detailCell);
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
    assumptions.append(node('p', '', `手续费 ${fmt(data.config?.fee_bps)} bps · 滑点 ${fmt(data.config?.slippage_bps)} bps · 账户每笔风险预算 ${numeric(data.config?.risk_fraction) ? `${fmt(data.config.risk_fraction * 100)}%` : '未提供'}`));
    assumptions.append(node('p', '', `出场方案：${plan.label}${numeric(plan.rr) ? ` · 计划盈亏比 ${fmt(plan.rr)}R，未扣费用与滑点` : ''}`));
    if (plan.mode === 'structural') assumptions.append(node('p', '', v2
      ? 'v2入场条件与持仓跟踪在开户时冻结，旧持仓不迁移。实际买入前以已结束K线确认回踩，后续开盘重新核验费用、滑点和至少3R的盈亏比；后续只在入场后确认更高低点与均线同区共振时上移同类保护。3R、确认周期及ATR缓冲为本项目待验证的机械规则。'
      : data.trailing_policy
      ? '初始结构、仓位与R基准保留原样；持仓后用已完成日线的20MA/EMA20区间及确认的更高低点评估，只上移止损。调整记录区分登记、计划生效、实际生效和核验时间；目标缺失时不补固定百分比。确认周期、ATR缓冲、美分取整和生效规则是项目量化定义。'
      : '逐股识别初始结构价位，保存信号时的依据。此账户按初始止损与目标执行，未启用持仓后的移动止损；目标缺失时不补固定百分比。'));
    if (data.automation_disabled) assumptions.append(node('p', '', `旧示例已停用：${data.legacy_reason || '仅保留原账本，不新增或推进自动交易。'}`));
    if (execution === 'intraday_5m') assumptions.append(node('p', '', '前向记录使用已结束的真实 5 分钟 K 线。BUY 登记后，从符合条件的后续行情开始模拟；行情可能延迟，检查时间与行情时间分别保留。'));
    const basis = { unadjusted: execution === 'intraday_5m' ? '未复权 5 分钟 K 线' : '未复权日线', adjusted: execution === 'intraday_5m' ? '复权 5 分钟 K 线' : '复权日线' }[data.price_basis] || data.price_basis || '口径未提供';
    const provider = { marketwatch: 'MarketWatch', yahoo: 'Yahoo Finance' }[data.provider_id] || data.provider_id || '来源未提供';
    assumptions.append(node('p', '', `行情来源：${provider} · ${basis}`));
    if (quoteSources.length) {
      const details = node('details');
      details.append(node('summary', '', `查看 ${quoteSources.length} 个行情来源的时效核验`));
      const sources = node('ul', 'evidence-list');
      for (const [ticker, source] of quoteSources) {
        const freshness = source.stale === true ? '已过期' : source.stale === false ? '通过时效检查' : '时效未核验';
        const delay = numeric(source.exchange_delay_seconds) ? `交易所延迟 ${fmt(source.exchange_delay_seconds)} 秒` : '交易所延迟未知';
        sources.append(node('li', '', `${ticker} · ${source.provider || '来源未提供'} · 完整 K 线截至 ${paperTime(source.latest_completed_at)} · ${freshness} · ${delay}${source.received_at ? ` · 收到于 ${paperTime(source.received_at)}` : ''}`));
      }
      details.append(sources);
      assumptions.append(details);
    }
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
    const config = {
      run_id: activeRun.run_id, mode: form.elements.mode.value,
      initial_cash: Number(form.elements.initial_cash.value), risk_fraction: Number(form.elements.risk_percent.value) / 100,
      fee_bps: Number(form.elements.fee_bps.value), slippage_bps: Number(form.elements.slippage_bps.value),
      profile_id: form.elements.profile_id.value || null, exit_mode: form.elements.exit_mode.value,
    };
    if (config.exit_mode === 'fixed_percent') {
      config.stop_loss_pct = Number(form.elements.stop_loss_percent.value) / 100;
      config.take_profit_pct = Number(form.elements.take_profit_percent.value) / 100;
    }
    if (config.mode === 'forward' && config.exit_mode === 'structural' && form.elements.strategy_version.value === 'structural_retest_v2') config.strategy_version = 'structural_retest_v2';
    const account = await api('api/paper', {
      method: 'POST', body: JSON.stringify(config),
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
$('paper-exit-mode').addEventListener('change', renderPaperFormPlan);
for (const name of ['mode', 'strategy_version']) $('paper-form').elements[name].addEventListener('change', renderPaperFormPlan);
$('paper-history-mode').addEventListener('change', renderPaperHistory);
for (const id of ['paper-pnl-ticker', 'paper-pnl-date']) $(id).addEventListener('change', () => renderPaperDailyPnl(paperLedger(selectedPaper)));
for (const name of ['stop_loss_percent', 'take_profit_percent']) $('paper-form').elements[name].addEventListener('input', renderPaperFormPlan);
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
renderPaperFormPlan();
showView();
loadWorkspace();
