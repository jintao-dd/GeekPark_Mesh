/* GeekPark Mesh · 周报后台主控台
 *
 * 与设计稿 v0.8 的差别只有一处，且是刻意的：
 * 管线进度不是本地计时器，而是轮询后端真实执行状态。
 * 后端做到哪一步，界面才走到哪一步——UI 上出现的每一条，后端必须真的做。
 */
const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const A = window.MESHADMIN;

const STEPS = [['放入素材', 0], ['挖掘 & 脱敏', 0], ['审校', 0], ['确认上线', 1]];
const SCREEN = [1, 3, 4, 5];
const S = { step: A.startStep || 0, running: false, autoPreviewAfterMining: false };
const AUTO_PREVIEW_KEY = 'meshAutoPreview:' + (A.slug || '');
function setAutoPreview(on) {
  S.autoPreviewAfterMining = !!on;
  try {
    if (on) sessionStorage.setItem(AUTO_PREVIEW_KEY, '1');
    else sessionStorage.removeItem(AUTO_PREVIEW_KEY);
  } catch (_) {}
}

const JSON_HDR = { Accept: 'application/json' };

function submitPreparePreview() {
  startPreparePreview();
}

async function startPreparePreview(opts) {
  const force = !!(opts && opts.force);
  const t2 = $('#to2');
  if (t2) t2.disabled = true;
  showBusy('正在生成要点卡与草稿…', '按团队出卡后生成草稿，可刷新后续跑');
  try {
    const r = await fetch(`/admin/issue/${A.slug}/preview/start?force=${force ? 1 : 0}`, {
      method: 'POST',
      headers: JSON_HDR,
    });
    const st = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(st.error || st.detail || ('HTTP ' + r.status));
    if (st.error && !st.running) throw new Error(st.error);
  } catch (e) {
    hideBusy();
    if (t2) t2.disabled = false;
    toast('无法启动生成：' + (e.message || e));
    return;
  }
  pollPreview();
}

async function pollPreview() {
  try {
    const r = await fetch(`/admin/issue/${A.slug}/preview/status`, { headers: JSON_HDR });
    const st = await r.json();
    if (st.error && !st.running) {
      hideBusy();
      toast(st.error);
      const t2 = $('#to2'); if (t2) t2.disabled = false;
      return;
    }
    if (st.done && st.preview_url) {
      showBusy(st.message || '完成，正在进入预览…', '请稍候');
      location.href = st.preview_url;
      return;
    }
    const hint = (st.total > 0 && st.cur > 0)
      ? `进度 ${st.cur}/${st.total}`
      : '大文件可能需要几分钟，请勿关闭页面';
    showBusy(st.message || '正在生成要点卡与草稿…', hint);
    if (st.running) setTimeout(pollPreview, 900);
    else {
      hideBusy();
      const t2 = $('#to2'); if (t2) t2.disabled = false;
    }
  } catch (e) {
    hideBusy();
    toast('生成状态查询失败，请刷新后重试');
    const t2 = $('#to2'); if (t2) t2.disabled = false;
  }
}

function canEnter(n) {
  if (n <= 0) return true;
  if (n === 1) return document.querySelectorAll('#frows .frow').length > 0;
  if (n === 2) return !!A.hasItems;
  if (n === 3) {
    return !!A.hasItems
      && A.cardCount > 0
      && A.hasDraft
      && !A.draftStale
      && A.nNoOwner === 0;
  }
  return false;
}
function guardStep(n) {
  if (canEnter(n)) return true;
  if (n === 1) toast('请先放入素材');
  else if (n === 2) toast('请先完成挖掘与脱敏');
  else if (n === 3) {
    if (A.draftStale || !A.hasDraft) toast('请先生成周报草稿');
    else if (A.nNoOwner > 0) toast('还有条目待指定归属');
    else toast('请先完成审校并生成草稿');
  }
  return false;
}
function renderDots() {
  // 顶栏四步已去掉；主路径是「生成预览 → 预览编辑」
  const el = $('#dots');
  if (el) el.innerHTML = '';
}
function syncState() {
  const el = $('#state');
  if (!el) return;
  if (A.issueStatus === 'published') {
    el.innerHTML = `${A.periodLabel} · <b>已上线</b>`;
    return;
  }
  const labels = ['收集素材', '挖掘中', '可预览编辑', '可重新上线'];
  el.textContent = labels[S.step] || '收集素材';
}

function toast(t) {
  const e = $('#toast'); e.textContent = t; e.classList.add('on');
  clearTimeout(e._t); e._t = setTimeout(() => e.classList.remove('on'), 2600);
}
function renderReviewSummary() {
  const verdict = $('#verdict');
  if (verdict) {
    const hold = A.nNoOwner > 0 || !A.hasDraft || A.draftStale;
    verdict.className = 'verdict' + (hold ? ' hold' : '');
    verdict.innerHTML = `<div class="big"><svg viewBox="0 0 24 24">${hold ? '<path d="M12 8v5"/><path d="M12 16h.01"/><path d="M10.29 3.86l-7.5 13A1 1 0 0 0 3.66 18h16.68a1 1 0 0 0 .87-1.5l-7.5-13a1 1 0 0 0-1.74 0z"/>' : '<path d="M20 6L9 17l-5-5"/>'}</svg></div><div class="tx"><h2>${hold ? '还有项目需要人工确认' : '核对状态基本就绪'}</h2><p>${hold ? `当前还有 <b>${A.nNoOwner}</b> 条待判定归属，${A.hasDraft && !A.draftStale ? '草稿已生成' : '草稿尚未生成或已过期'}。` : `已生成 <b>${A.cardCount}</b> 张团队卡，条目 <b>${A.nItems}</b> 条，已合并 <b>${A.nMerged}</b> 组，草稿可进入上线前检查。`}</p></div>`;
  }
  const attn = $('#attn');
  if (attn) {
    const items = [];
    if (A.nNoOwner > 0) items.push(`还有 ${A.nNoOwner} 条条目待指定归属`);
    if (!A.hasDraft || A.draftStale) items.push(A.draftStale ? '挖掘或卡片已变更，请重新生成周报草稿' : '周报草稿尚未生成');
    if (A.nBlocked > 0) items.push(`有 ${A.nBlocked} 条 ⑤区 / L3 内容被硬拦`);
    attn.className = 'attn' + (items.length ? '' : ' empty-attn');
    attn.innerHTML = items.length
      ? `<h4><span class="dotr"></span>这一步建议你先看这些</h4><p class="sub">这里只列当前最需要人工处理的事项。</p>${items.map(x => `<div class="acard"><div class="q">${x}</div></div>`).join('')}`
      : '';
  }
  const to4 = $('#to4');
  if (to4) to4.disabled = !canEnter(3);
  syncPublishGate();
}
function syncPublishGate() {
  const pub = $('#publish');
  if (!pub) return;
  const ready = canEnter(3);
  pub.disabled = !ready;
  pub.title = ready ? '' : '上线前检查未全部通过';
}
function show(id, opts = {}) {
  $$('.screen').forEach(x => x.classList.remove('on'));
  const el = $('#' + id);
  if (el) {
    // 重新触发进入动画，避免像硬切页
    void el.offsetWidth;
    el.classList.add('on');
  }
  if (opts.scroll) window.scrollTo({ top: 0, behavior: 'smooth' });
}
function go(n, opts = {}) {
  S.step = n;
  show('s' + SCREEN[n], opts);
  renderDots();
  syncState();
  renderReviewSummary();
}
function restoreScroll() {
  const y = sessionStorage.getItem('meshRestoreScroll');
  if (y == null) return;
  sessionStorage.removeItem('meshRestoreScroll');
  requestAnimationFrame(() => window.scrollTo(0, +y));
}
$$('[data-go]').forEach(b => b.onclick = () => {
  const n = +b.dataset.go;
  if (n < S.step || guardStep(n)) go(n, { scroll: true });
});
$$('form.ops').forEach(form => {
  form.addEventListener('submit', () => {
    sessionStorage.setItem('meshRestoreScroll', String(window.scrollY));
  });
});
addEventListener('scroll', () => $('#chrome').classList.toggle('solid', scrollY > 10));

/* ---------- 第一步：拖入即上传（AJAX，不整页刷新） ---------- */
const drop = $('#drop'), fi = $('#fi'), upform = $('#upform');
const DROP_IDLE = '拖到这里，或点击选择';

function escHtml(t) {
  return String(t || '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function teamOptions(selected) {
  return (A.sourcePicks || A.teams || []).map(t =>
    `<option value="${escHtml(t)}"${t === selected ? ' selected' : ''}>${escHtml(t)}</option>`
  ).join('');
}

function splitHint(meta) {
  if (!meta) return '';
  let m = meta;
  if (typeof meta === 'string') {
    try { m = JSON.parse(meta); } catch (_) { return ''; }
  }
  const sp = (m && m.split) || {};
  const n = sp.segments || 0;
  const mode = sp.mode || '';
  if (mode === 'multi' && n > 1) {
    const types = sp.types || {};
    const brief = Object.keys(types).sort().map(k => `${k}×${types[k]}`).join('、');
    return brief ? ` · 已拆 ${n} 段（${brief}）` : ` · 已拆 ${n} 段`;
  }
  if (mode === 'single') return ' · 整包 1 段';
  if (mode === 'fallback') return ' · 降级整段抽取';
  if (n === 1) return ' · 整包 1 段';
  if (n > 1) return ` · 已拆 ${n} 段`;
  return '';
}

function sourceRowHtml(s) {
  const viewLabel = A.canRawView ? '查看原文' : '查看条目';
  const viewLink = A.canWrite
    ? `<a class="src-link" href="/admin/source/${+s.id}">${viewLabel}</a>`
    : '';
  const del = A.canWrite
    ? `<form method="post" action="/admin/source/${+s.id}/delete" class="js-confirm js-ajax" data-title="移除来源" data-body="删除该来源及其条目？" data-ok="移除"><button class="x" type="submit">移除</button></form>`
    : '';
  return `<div class="frow is-new" data-source-id="${+s.id}">
    <div class="nm">
      <b>${escHtml(s.title || '')}</b>
      <div class="meta-edits">
        <label class="meta-lbl">团队</label>
        <select class="meta-sel" data-field="team" title="选择提交团队或编辑部材料类型">${teamOptions(s.team)}</select>
      </div>
    </div>
    <div class="dept"><div class="hint">${+(s.n || 0)} 字 · ${s.extracted ? '已抽取' : '未抽取'}${splitHint(s.meta)}</div></div>
    <div class="frow-acts">${viewLink}${del}</div>
  </div>`;
}

function bindMetaSels(root) {
  if (!A.canWrite) return;
  (root || document).querySelectorAll('#frows .meta-sel[data-field="team"]').forEach(sel => {
    if (sel.dataset.bound) return;
    sel.dataset.bound = '1';
    sel.addEventListener('change', async () => {
      const row = sel.closest('[data-source-id]');
      if (!row) return;
      const id = +row.dataset.sourceId;
      const team = sel.value;
      const payload = { id, team };
      sel.disabled = true;
      try {
        const r = await fetch('/api/source_meta', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
          body: JSON.stringify(payload),
        });
        const j = await r.json().catch(() => ({}));
        if (!r.ok) {
          throw new Error(j.detail || j.error || ('HTTP ' + r.status));
        }
        toast(j.need_reextract ? '已保存；该混合来源需重新抽取' : '已保存');
      } catch (e) {
        toast('保存失败：' + (e.message || e));
      } finally {
        sel.disabled = false;
      }
    });
  });
}

function refreshSourceCount() {
  const n = document.querySelectorAll('#frows .frow').length;
  const fc = $('#fcount'); if (fc) fc.textContent = String(n);
  const to2 = $('#to2'); if (to2) to2.disabled = n === 0;
}

function prependSources(list) {
  const box = $('#frows');
  if (!box || !list || !list.length) return;
  const empty = box.querySelector('.note');
  if (empty) empty.remove();
  // 本批按上传顺序返回；列表按 id 倒序展示，新文件在上
  box.insertAdjacentHTML('afterbegin', [...list].reverse().map(sourceRowHtml).join(''));
  bindMetaSels(box);
  bindMeshConfirms(box);
  refreshSourceCount();
  requestAnimationFrame(() => {
    box.querySelectorAll('.frow.is-new').forEach(el => {
      el.addEventListener('animationend', () => el.classList.remove('is-new'), { once: true });
    });
  });
}

async function submitUpload() {
  if (!A.canWrite) return;
  if (!upform || !fi || !fi.files || !fi.files.length) return;
  const h = $('#drop h3');
  const nFiles = fi.files.length;
  if (h) h.textContent = `正在放入 ${nFiles} 个文件…`;
  drop && drop.classList.add('busy', 'uploading');
  const fd = new FormData(upform);
  try {
    const r = await fetch(upform.action, {
      method: 'POST',
      body: fd,
      headers: { Accept: 'application/json', 'X-Mesh-Ajax': '1' },
      credentials: 'same-origin',
    });
    let j = null;
    try { j = await r.json(); } catch (_) {}
    if (!r.ok || !j || !j.ok) {
      const err = (j && j.error === 'empty') ? '没有读到有效文件' : ((j && j.error) || ('上传失败 HTTP ' + r.status));
      toast(err);
      return;
    }
    prependSources(j.sources || []);
    A.sourcesDirty = true; // 新增素材后需重挖，避免「生成预览」误跳过挖掘
    toast(`已放入 ${j.saved} 个来源，类型与团队已自动识别`);
  } catch (e) {
    toast('上传失败：' + (e.message || e));
  } finally {
    if (h) h.textContent = DROP_IDLE;
    drop && drop.classList.remove('busy', 'uploading');
    try { fi.value = ''; } catch (_) {}
  }
}

if (drop && fi) {
  drop.onclick = e => { if (e.target.closest('.rej') || e.target === fi) return; fi.click(); };
  drop.addEventListener('dragover', e => { e.preventDefault(); drop.classList.add('over'); });
  drop.addEventListener('dragleave', () => drop.classList.remove('over'));
  drop.addEventListener('drop', e => {
    e.preventDefault(); drop.classList.remove('over');
    if (!e.dataTransfer.files.length) return;
    try {
      const dt = new DataTransfer();
      [...e.dataTransfer.files].forEach(f => dt.items.add(f));
      fi.files = dt.files;
    } catch (_) {
      fi.files = e.dataTransfer.files;
    }
    submitUpload();
  });
  fi.addEventListener('change', () => { if (fi.files.length) submitUpload(); });
}
const params = new URLSearchParams(location.search);
if (params.get('uploaded')) toast(`已放入 ${params.get('uploaded')} 个来源，类型与团队已自动识别`);
if (params.get('upload_error') === 'empty') toast('没有读到有效文件');

bindMetaSels();

const safebar = $('#safebar');
if (safebar) {
  const pop = $('#safepop');
  safebar.onclick = () => {
    const on = pop.classList.toggle('on');
    safebar.setAttribute('aria-expanded', on ? 'true' : 'false');
  };
  const sx = $('#safex'); if (sx) sx.onclick = () => { pop.classList.remove('on'); safebar.setAttribute('aria-expanded', 'false'); };
}

/* ---------- 第二步：真实管线 ---------- */
function ruleLi(x, i) {
  const by = x.by === 'code' ? '代码执行' : '模型内执行';
  return `<li class="rule ${x.tier}" data-i="${i}"><span class="mk"></span><span><span class="k">${x.k}<span class="sk">${x.sk}</span><span class="sk" style="opacity:.6">${by}</span></span><span class="h">${x.h}</span></span><span class="r"></span></li>`;
}
function renderRules() {
  const proc = A.steps.filter(x => x.tier === 'proc'), design = A.steps.filter(x => x.tier === 'design');
  $('#rules').innerHTML =
    `<details class="proc" id="procWrap" open><summary><span class="pk">文件处理</span><span class="ph">转写 · 清洗 · 去重，共 ${proc.length} 项</span><span class="pr" id="procR"></span></summary><ul class="plist">${proc.map((x, i) => ruleLi(x, i)).join('')}</ul></details>`
    + `<ul class="dlist">${design.map((x, i) => ruleLi(x, proc.length + i)).join('')}</ul>`;
}
function paint(state) {
  const lis = $$('#rules .rule');
  const total = A.steps.length;
  const cur = state.cur;
  lis.forEach((li, i) => {
    li.classList.remove('run');
    if (i < cur) li.classList.add('done');
    else if (i === cur) { li.classList.add(state.done ? 'done' : 'run'); }
    const sk = A.steps[i].sk;
    const r = state.results[sk];
    if (r) li.querySelector('.r').textContent = r;
    if (i === cur && !state.done) li.scrollIntoView({ block: 'center', behavior: 'smooth' });
  });
  const pct = Math.min(100, Math.round(((cur + 1) / total) * 100));
  $('#pf').style.width = pct + '%';
  $('#ppct').textContent = pct + '%';
  $('#pnow').textContent = state.error ? ('出错：' + state.error)
    : (state.done ? '全部完成' : (cur >= 0 ? A.steps[cur].k : '准备就绪'));
}
function showBusy(msg, hint) {
  const el = $('#busy');
  if (!el) return;
  const title = $('#busyTitle');
  const h = $('#busyHint');
  if (title) title.textContent = msg || '处理中…';
  if (h && hint) h.textContent = hint;
  el.classList.add('on');
  document.body.classList.add('ai-busy');
}
function hideBusy() {
  const el = $('#busy');
  if (el) el.classList.remove('on');
  document.body.classList.remove('ai-busy');
}

async function poll() {
  try {
    const r = await fetch(`/admin/issue/${A.slug}/pipeline/status`, { headers: JSON_HDR });
    const st = await r.json();
    paint(st);
    if (S.autoPreviewAfterMining && st.cur >= 0) {
      const stepName = (A.steps[st.cur] && A.steps[st.cur].k) || '处理中';
      const detail = (st.results && A.steps[st.cur] && st.results[A.steps[st.cur].sk]) || '';
      showBusy('正在挖掘与脱敏…', detail ? `${stepName} · ${detail}` : stepName);
    }
    if (st.error) {
      S.running = false;
      setAutoPreview(false);
      hideBusy();
      toast('挖掘失败：' + st.error);
      $('#gen').style.display = '';
      const t2 = $('#to2'); if (t2) t2.disabled = false;
      return;
    }
    if (st.done) {
      S.running = false;
      if (S.autoPreviewAfterMining) {
        setAutoPreview(false);
        A.hasItems = true;
        A.sourcesDirty = false;
        showBusy('正在生成要点卡与草稿…', '完成后自动进入预览编辑页，请勿关闭页面');
        submitPreparePreview();
        return;
      }
      hideBusy();
      $('#gen').style.display = ''; $('#to3').style.display = '';
      $('#result').classList.add('on');
      const g = $('#resGrid');
      g.innerHTML = A.steps.filter(x => st.results[x.sk]).map(x =>
        `<div><b>${st.results[x.sk]}</b><span>${x.k}</span></div>`).join('');
      toast('挖掘与脱敏完成');
      A.hasItems = true;
      A.hasDraft = false;
      A.draftStale = true;
      renderDots();
      renderReviewSummary();
      return;
    }
    setTimeout(poll, 900);
  } catch (e) {
    S.running = false;
    setAutoPreview(false);
    hideBusy();
    toast('状态查询失败');
    const t2 = $('#to2'); if (t2) t2.disabled = false;
    const g = $('#gen'); if (g) g.style.display = '';
  }
}
async function runMining(opts = {}) {
  if (S.running) return;
  const force = !!opts.force;
  if (A.hasItems && !force) {
    const ok = await MeshDialog.confirm({
      title: '重新挖掘',
      body: '当前已经有挖掘结果，再次执行会重跑所有来源，可能很慢。确定继续吗？',
      okText: '重新挖掘',
      cancelText: '取消',
    });
    if (!ok) {
      setAutoPreview(false);
      hideBusy();
      const t2 = $('#to2'); if (t2) t2.disabled = false;
      return;
    }
  }
  S.running = true;
  $('#gen').style.display = 'none'; $('#to3').style.display = 'none';
  $('#result').classList.remove('on');
  $('#pnow').textContent = '启动中…';
  if (S.autoPreviewAfterMining) showBusy('正在挖掘与脱敏…', '大文件可能需要几分钟，请勿关闭页面');
  try {
    const r = await fetch(`/admin/issue/${A.slug}/pipeline/start?force=${force ? 1 : 0}`, {
      method: 'POST',
      headers: JSON_HDR,
    });
    if (!r.ok) {
      const j = await r.json().catch(() => ({}));
      throw new Error(j.detail || j.error || ('HTTP ' + r.status));
    }
  } catch (e) {
    S.running = false;
    setAutoPreview(false);
    hideBusy();
    toast('无法启动挖掘，请重试');
    $('#gen').style.display = '';
    const t2 = $('#to2'); if (t2) t2.disabled = false;
    return;
  }
  poll();
}
const to2 = $('#to2');
if (to2) to2.onclick = () => {
  if (!A.canWrite) { toast('当前身份不能生成预览'); return; }
  if (A.issueStatus === 'published') {
    toast('已上线期不能直接生成预览。请先「创建修订草稿」。');
    return;
  }
  if (!document.querySelectorAll('#frows .frow').length) {
    toast('请先放入素材');
    return;
  }
  // 忙态下再点：强制从头重跑（挖掘 → 预览）
  if (document.body.classList.contains('ai-busy')) {
    to2.disabled = true;
    setAutoPreview(true);
    showBusy('正在挖掘与脱敏…', '大文件可能需要几分钟，请勿关闭页面');
    runMining({ force: true });
    return;
  }
  // 每次「生成预览」都先重新挖掘，再自动出卡+草稿（避免跳过挖掘导致 provenance 等字段陈旧）
  to2.disabled = true;
  setAutoPreview(true);
  showBusy('正在挖掘与脱敏…', '完成后将自动生成要点卡与草稿');
  runMining({ force: true });
};
const gen = $('#gen'); if (gen) gen.onclick = () => runMining({ force: document.body.classList.contains('ai-busy') || S.running });
const to3 = $('#to3'); if (to3) to3.onclick = () => { if (guardStep(2)) go(2, { scroll: true }); };
const to4 = $('#to4'); if (to4) to4.onclick = () => { if (guardStep(3)) go(3, { scroll: true }); };

/* ---------- AI 长任务：要点卡 / 草稿 loading ---------- */
$$('form.ai-job').forEach(form => {
  form.addEventListener('submit', async e => {
    if (form.dataset.submitting === '1') {
      e.preventDefault();
      return;
    }
    if (form.dataset.needItems === '1' && !A.hasItems) {
      e.preventDefault();
      toast('请先完成挖掘与脱敏');
      return;
    }
    const conf = form.dataset.confirm;
    if (conf) {
      e.preventDefault();
      if (form.dataset.confirming === '1') return;
      form.dataset.confirming = '1';
      try {
        const ok = await MeshDialog.confirm({
          title: '请确认',
          body: conf,
          okText: '继续',
          cancelText: '取消',
        });
        if (!ok) return;
        form.dataset.submitting = '1';
        const btn = form.querySelector('button[type="submit"]');
        if (btn) btn.disabled = true;
        showBusy(form.dataset.busy || 'AI 生成中…');
        form.submit();
      } finally {
        form.dataset.confirming = '0';
      }
      return;
    }
    form.dataset.submitting = '1';
    const btn = form.querySelector('button[type="submit"]');
    if (btn) btn.disabled = true;
    showBusy(form.dataset.busy || 'AI 生成中…');
  });
});

function bindMeshConfirms(root) {
  (root || document).querySelectorAll('form.js-confirm').forEach(form => {
    if (form.dataset.dlgBound) return;
    form.dataset.dlgBound = '1';
    form.addEventListener('submit', async e => {
      e.preventDefault();
      const ok = await MeshDialog.confirm({
        title: form.dataset.title || '请确认',
        body: form.dataset.body || '',
        okText: form.dataset.ok || '确定',
        cancelText: '取消',
        danger: true,
      });
      if (!ok) return;
      // 行内删除等：fetch 后改 DOM，不整页刷新
      if (form.classList.contains('js-ajax')) {
        const btn = form.querySelector('button[type="submit"]');
        if (btn) btn.disabled = true;
        try {
          const r = await fetch(form.action, {
            method: 'POST',
            headers: { Accept: 'application/json' },
            credentials: 'same-origin',
          });
          const data = await r.json().catch(() => ({}));
          if (!r.ok || data.ok === false) throw new Error((data && data.error) || (`HTTP ${r.status}`));
          const row = form.closest('.frow');
          if (row) row.remove();
          refreshSourceCount();
          A.sourcesDirty = true;
          toast('已移除');
        } catch (err) {
          if (btn) btn.disabled = false;
          toast(String(err.message || err));
        }
        return;
      }
      form.submit();
    });
  });
  (root || document).querySelectorAll('.js-confirm-click').forEach(btn => {
    if (btn.dataset.dlgBound) return;
    btn.dataset.dlgBound = '1';
    btn.addEventListener('click', async e => {
      e.preventDefault();
      const form = btn.closest('form');
      if (!form) return;
      const ok = await MeshDialog.confirm({
        title: btn.dataset.title || '请确认',
        body: btn.dataset.body || '',
        okText: btn.dataset.ok || '确定',
        cancelText: '取消',
      });
      if (ok) form.submit();
    });
  });
}
bindMeshConfirms();

/* ---------- 第四步：上线确认 ---------- */
const pub = $('#publish');
if (pub) {
  pub.onclick = async () => {
    if (!canEnter(3)) {
      toast('上线前检查未通过，请先处理红色项');
      return;
    }
    const ok = await MeshDialog.confirm({
      title: '确认上线',
      body: `上线后读者可见「${A.periodLabel || A.slug}」。此操作不可撤回。`,
      okText: '确认上线',
      cancelText: '再看看',
    });
    if (!ok) return;
    pub.disabled = true;
    try {
      const body = new URLSearchParams();
      const r = await fetch(`/admin/issue/${A.slug}/publish`, {
        method: 'POST',
        headers: {
          Accept: 'application/json',
          'Content-Type': 'application/x-www-form-urlencoded',
        },
        credentials: 'same-origin',
        body: body.toString(),
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok || data.ok === false) {
        throw new Error((data && data.error) || ('HTTP ' + r.status));
      }
      location.href = '/' + A.slug + '?published=1';
    } catch (err) {
      pub.disabled = false;
      await MeshDialog.alert({ title: '上线失败', body: String(err.message || err) });
    }
  };
}

/* ---------- 初始化 ---------- */
renderRules();
renderDots();
renderReviewSummary();
go(Math.min(S.step, 3));
restoreScroll();
if (A.flashErr) toast(A.flashErr);
// 若管线正在跑（例如刷新了页面），自动接上（仅写权限角色）
if (A.canWrite) {
  try {
    if (sessionStorage.getItem(AUTO_PREVIEW_KEY) === '1') S.autoPreviewAfterMining = true;
  } catch (_) {}
  fetch(`/admin/issue/${A.slug}/pipeline/status`, { headers: JSON_HDR }).then(r => r.json()).then(st => {
    if (st.running) {
      S.running = true;
      go(1, { scroll: true });
      if (S.autoPreviewAfterMining) showBusy('正在挖掘与脱敏…', '大文件可能需要几分钟，请勿关闭页面');
      poll();
    }
    else if (st.cur >= 0) paint(st);
  }).catch(() => {});
  fetch(`/admin/issue/${A.slug}/preview/status`, { headers: JSON_HDR }).then(r => r.json()).then(st => {
    if (st.running) {
      go(0, { scroll: true });
      showBusy(st.message || '正在生成要点卡与草稿…', '刷新后续跑中');
      pollPreview();
    } else if (st.done && st.preview_url && /preview_job=1/.test(location.search)) {
      location.href = st.preview_url;
    }
  }).catch(() => {});
}
