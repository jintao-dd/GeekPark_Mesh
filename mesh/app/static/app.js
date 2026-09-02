(function(){
  const CFG = window.MESH || {};
  const nav=document.querySelector('.nav'),hero=document.querySelector('.hero');
  if(nav&&hero){const navIO=new IntersectionObserver(es=>{es.forEach(e=>nav.classList.toggle('show',!e.isIntersecting));},{rootMargin:'-120px 0px 0px 0px',threshold:0});navIO.observe(hero);}

  // ===== 实体悬停浮窗（D 样式）=====
  const tip=document.createElement('div');tip.id='tip';document.body.appendChild(tip);let hideT=null;
  function chipName(el){
    const n=el.querySelector('[data-path$=".name"]');
    if(n) return (n.textContent||'').trim();
    return [...el.childNodes].filter(n=>n.nodeType===3).map(n=>n.textContent).join('').trim();
  }
  function showTip(el){
    clearTimeout(hideT);
    const raw=el.dataset.tip||'';
    const base=el.dataset.tipPath||'';
    const rows=raw.split('||').map(r=>r.split('::')).filter(r=>r.length>1);
    const editing=document.body.classList.contains('editor') && !!base;
    let h='<div class="r"><b>'+escTip(chipName(el))+'</b></div>';
    if(editing){
      h+=rows.map((r,i)=>'<div class="r"><i data-path="'+base+'.rows.'+i+'.k">'+escTip(r[0])+'</i><span data-path="'+base+'.rows.'+i+'.v">'+escTip(r[1]||'')+'</span></div>').join('');
      if(!rows.length) h+='<div class="r"><span style="color:#6B7280;font-size:12px">暂无详情，可在编辑后新增字段</span></div>';
    }else{
      h+=rows.map(([k,v])=>'<div class="r"><i>'+escTip(k)+'</i><span>'+escTip(v)+'</span></div>').join('');
    }
    tip.innerHTML=h;
    tip.classList.add('show');
    moveTip(el);
  }
  function escTip(t){return String(t||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}
  function moveTip(el){const r=el.getBoundingClientRect();const w=tip.offsetWidth,h=tip.offsetHeight;
    let x=r.left, y=r.bottom+6; if(x+w>window.innerWidth-12) x=window.innerWidth-12-w; if(y+h>window.innerHeight-12) y=r.top-6-h;
    tip.style.left=x+'px';tip.style.top=y+'px';}
  function hideTip(){if(document.body.classList.contains('editor')&&tip.contains(document.activeElement))return;hideT=setTimeout(()=>tip.classList.remove('show'),160);}
  function bindTips(){document.querySelectorAll('.ent').forEach(el=>{if(el.dataset.tb)return;el.dataset.tb='1';el.addEventListener('mouseenter',()=>showTip(el));el.addEventListener('mouseleave',hideTip);el.addEventListener('focus',()=>showTip(el));el.addEventListener('blur',hideTip);el.tabIndex=0;});}
  bindTips();
  tip.addEventListener('mouseenter',()=>clearTimeout(hideT));tip.addEventListener('mouseleave',hideTip);
  window.addEventListener('scroll',()=>tip.classList.remove('show'),{passive:true});

  // ===== 关键词排序：按关注度 / 按部门 =====
  const TEAM_ORDER=CFG.teamOrder||["编辑部","商业化团队","硅谷 BD 团队","Global Partnership 团队","英文站","品牌创意团队","社群","投资团队","音频播客团队","视频号团队","CEO / 总裁办"];
  function applyMode(sec,mode){
    sec.querySelectorAll('.ents').forEach(g=>{
      const chips=[...g.querySelectorAll('.ent')];g.querySelectorAll('.tlab').forEach(x=>x.remove());let out=[];
      if(mode==='team'){
        chips.sort((a,b)=>TEAM_ORDER.indexOf((a.dataset.teams||'').split('|')[0])-TEAM_ORDER.indexOf((b.dataset.teams||'').split('|')[0]) || ((+b.dataset.w||0)-(+a.dataset.w||0)));
        let last=null;chips.forEach(c=>{const t=(c.dataset.teams||'').split('|')[0]; if(t&&t!==last){const l=document.createElement('span');l.className='tlab';l.textContent=t;out.push(l);last=t;} out.push(c);});
      }else{chips.sort((a,b)=>((+b.dataset.w||0)-(+a.dataset.w||0)));out=chips;}
      out.forEach(n=>g.appendChild(n));
    });
  }
  document.querySelectorAll('.sortbar').forEach(bar=>{const sec=bar.closest('section');
    bar.querySelectorAll('button').forEach(b=>b.addEventListener('click',()=>{bar.querySelectorAll('button').forEach(x=>x.classList.remove('on'));b.classList.add('on');applyMode(sec,b.dataset.mode);}));
    applyMode(sec,'attn');});

  // ===== 搜索弹窗：关键词（本期页内 + 跨期 FTS）/ AI 问答（真实调用）=====
  (function(){
    const modal=document.getElementById('modal');if(!modal)return;
    const mq=document.getElementById('mq'),mbody=document.getElementById('mbody');let tab='kw',timer=null;
    let askAbort=null, searchAbort=null, searchSeq=0;
    const idx=[];const clean=t=>t.replace(/\s+/g,' ').trim();
    const secName=el=>{const h=el.closest('section')?.querySelector('h2');return h?clean(h.textContent):'本期';};
    const SEC_HASH={'可同步的关系':'rel','接触过的人和公司':'who','关注了什么':'what','日程与计划':'next','沟通中提到的看法':'views'};
    function pushIdx(entry){entry.i=idx.length;idx.push(entry);}
    document.querySelectorAll('.card').forEach(c=>{const t=c.querySelector('.t');if(!t)return;pushIdx({el:c,type:secName(c),title:clean(t.textContent),text:clean(c.textContent)});});
    document.querySelectorAll('.ent').forEach(e=>{pushIdx({el:e,type:secName(e),title:chipName(e),text:chipName(e)+' '+(e.dataset.tip||'').replace(/<[^>]+>/g,' ').replace(/\|\|/g,' ').replace(/::/g,' ')});});
    document.querySelectorAll('.row').forEach(r=>{const l=r.querySelector('.l');if(!l)return;pushIdx({el:r,type:secName(r),title:clean(l.textContent),text:clean(r.textContent)});});
    function jumpToHit(x){if(!x||!x.el)return;closeModal();x.el.scrollIntoView({behavior:'smooth',block:'center'});x.el.classList.remove('hit');void x.el.offsetWidth;x.el.classList.add('hit');}
    function issuePeekUrl(slug,q,section,title){const hash=SEC_HASH[section]||'';let u='/'+encodeURIComponent(slug)+'?peek='+encodeURIComponent(q);if(title)u+='&at='+encodeURIComponent(title);return hash?u+'#'+hash:u;}
    const AI_EX=['过去一个月里有哪些硬件公司是编辑部接触过、但 Founder Park 团队还没接触过的？','商业化团队在跟进的客户里，哪些同时也是编辑部的采访对象？','最近哪些海外接触是国内还没有部门跟进的？','关于 AI 助听器，公司内部各团队分别知道什么？'];
    function esc(t){return String(t||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}
    function hl(t,q){const i=t.toLowerCase().indexOf(q.toLowerCase());if(i<0)return esc(t.slice(0,110));const a=Math.max(0,i-36);return esc(t.slice(a,i))+'<mark>'+esc(t.slice(i,i+q.length))+'</mark>'+esc(t.slice(i+q.length,i+q.length+70));}
    function cancelAsk(){
      if(askAbort){try{askAbort.abort();}catch(_){ }askAbort=null;}
      ask._busy=false;
    }
    function openModal(prefill,t){tab=t||tab;document.querySelectorAll('.mtabs button').forEach(b=>b.classList.toggle('on',b.dataset.tab===tab));modal.classList.add('show');if(prefill!==undefined)mq.value=prefill;render();setTimeout(()=>mq.focus(),30);}
    function closeModal(){cancelAsk();modal.classList.remove('show');}
    async function render(){
      const q=mq.value.trim();
      if(tab==='ai'){
        let h='<div class="grp2" style="display:flex;align-items:center;justify-content:space-between;gap:8px"><span>试试这样问</span><button type="button" id="aiNewChat" class="ghost" style="font-size:12px;padding:4px 10px">新对话</button></div><div class="ai-ex">'+AI_EX.map(x=>'<button data-q="'+esc(x)+'">'+esc(x)+'</button>').join('')+'</div>';
        h+='<div class="grp2">输入问题后按回车提问；回答的每一句都会给出来源。追问会自动带上文。</div><div id="aiOut"></div>';
        mbody.innerHTML=h;
        mbody.querySelectorAll('.ai-ex button').forEach(b=>b.addEventListener('click',()=>{mq.value=b.dataset.q;ask();}));
        const nb=document.getElementById('aiNewChat');
        if(nb) nb.addEventListener('click',()=>meshAskNewSession().then(()=>{document.getElementById('aiOut').innerHTML='';mq.value='';mq.focus();}));
        return;
      }
      if(!q){mbody.innerHTML='<div class="grp2">输入即搜。本期结果可直接跳转；往期结果跳到对应期。</div>';return;}
      const hits=idx.filter(x=>x.text.toLowerCase().includes(q.toLowerCase())).slice(0,12);
      let h='<div class="grp2">本期 · '+hits.length+' 条</div><div class="res">'+(hits.map(x=>'<a class="it" href="#" data-i="'+x.i+'"><span class="k">'+esc(x.type)+'</span><span class="t">'+esc(x.title)+'</span><div class="sn">'+hl(x.text,q)+'</div></a>').join('')||'<div class="empty">本期无匹配</div>')+'</div>';
      h+='<div class="grp2" id="oldHead">往期 · 检索中…</div><div class="res" id="oldRes"></div>';
      mbody.innerHTML=h;
      mbody.querySelectorAll('.it[data-i]').forEach(a=>a.addEventListener('click',e=>{e.preventDefault();jumpToHit(idx[+a.dataset.i]);}));
      if(searchAbort){try{searchAbort.abort();}catch(_){ }}
      searchAbort=new AbortController();
      const seq=++searchSeq;
      try{
        const r=await fetch('/api/search?q='+encodeURIComponent(q)+'&slug='+encodeURIComponent(CFG.slug||''),{headers:{Accept:'application/json'},signal:searchAbort.signal});const j=await r.json();
        if(seq!==searchSeq)return;
        const old=(j.hits||[]).filter(x=>x.issue_slug!==CFG.slug);
        const oh=document.getElementById('oldHead'),orr=document.getElementById('oldRes');if(!oh)return;
        oh.textContent='往期 · '+old.length+' 条';
        orr.innerHTML=old.map(x=>'<a class="it" href="'+issuePeekUrl(x.issue_slug,q,x.section,x.title)+'"><span class="k old">'+esc(x.issue_slug)+' · '+esc(x.section)+'</span><span class="t">'+esc(x.title)+'</span><div class="sn">'+(x.sn||'')+'</div></a>').join('')||'<div class="empty">往期无匹配</div>';
      }catch(e){if(e&&e.name==='AbortError')return;const oh=document.getElementById('oldHead');if(oh)oh.textContent='往期 · 检索失败';}
    }
    function meshAskStore(){
      try{return sessionStorage;}catch(_){return localStorage;}
    }
    function meshAskPayload(q){
      const store=meshAskStore();
      let sid=store.getItem('mesh_ask_session');
      if(!sid){ sid=(crypto.randomUUID&&crypto.randomUUID())||String(Date.now()); store.setItem('mesh_ask_session',sid); }
      const p={q, session_id:sid};
      if(window.MESH&&window.MESH.slug) p.slug=window.MESH.slug;
      return p;
    }
    async function meshAskNewSession(){
      const store=meshAskStore();
      try{
        const r=await fetch('/api/ask/new_session',{method:'POST',headers:{'Content-Type':'application/json'}});
        if(r.ok){ const j=await r.json(); if(j.session_id){ store.setItem('mesh_ask_session',j.session_id); return j.session_id; } }
      }catch(_){}
      const sid=(crypto.randomUUID&&crypto.randomUUID())||String(Date.now());
      store.setItem('mesh_ask_session',sid);
      return sid;
    }
    async function ask(){
      if (ask._busy) return;
      const q=mq.value.trim();if(!q)return;const out=document.getElementById('aiOut');if(!out)return;
      ask._busy=true;
      if(askAbort){try{askAbort.abort();}catch(_){ }}
      askAbort=new AbortController();
      const signal=askAbort.signal;
      let askOnce=null;
      try{
      const reduceMotion=window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      const modeLabel=m=>{
        const s=String(m||'');
        if(s.includes('structured')) return '结构化交叉';
        if(s.includes('hybrid')||s.includes('analysis')) return '智能分析';
        return '全文检索';
      };
      const THINK_HINTS=[
        '正在理解问题',
        '正在检索相关材料',
        '正在梳理多源线索',
        '正在对照核对',
        '正在组织回答'
      ];
      const stageIndex=step=>{
        if(step==='route'||step==='retrieve') return 1;
        if(step==='group'||step==='source') return 2;
        if(step==='cross') return 3;
        if(step==='verify') return 4;
        return 0;
      };
      const waitShell=()=>(
        '<div class="ai-think" id="aiProgress">'
        +'<div class="ai-think-row">'
        +'<span class="ai-think-orb" aria-hidden="true"></span>'
        +'<span class="ai-think-label" id="aiHint">'+THINK_HINTS[0]+'</span>'
        +'<span class="ai-think-dots" aria-hidden="true"><i></i><i></i><i></i></span>'
        +'</div>'
        +'<div class="ai-skel" aria-hidden="true"><i></i><i></i><i></i><i></i><i></i></div>'
        +'</div>'
      );
      const setAnalysisStage=idx=>{
        if(idx<0) return;
        const hint=document.getElementById('aiHint');
        const lab=document.getElementById('aiLab');
        const text=THINK_HINTS[Math.min(idx, THINK_HINTS.length-1)]||THINK_HINTS[0];
        if(hint) hint.textContent=text;
        if(lab) lab.textContent='AI 问答 · 思考中';
      };
      const winLabel=(df,meta)=>{
        const dt=meta&&meta.date_to;
        const qmeta=meta&&meta.query;
        const from=df!==undefined&&df!==null&&df!==false&&df!==''?df:(qmeta&&qmeta.date_from);
        const to=dt!==undefined&&dt!==null&&dt!==''?dt:(qmeta&&qmeta.date_to);
        if(from===null&&(df===null||(qmeta&&qmeta.date_from===null))) return ' · 不限时间';
        if(!from&&!to) return '';
        if(from&&to) return ' · '+from+'～'+to;
        if(from) return ' · 自 '+from;
        return ' · 至 '+to;
      };
      const paint=(mode,nCtx,pending,phase,meta)=>{
        // phase: wait | stream | ready
        const ph=phase||(pending?'stream':'ready');
        const win=winLabel(meta&&meta.date_from, meta);
        const lab=ph==='wait'
          ? 'AI 问答 · 思考中'
          : ('AI 问答 · '+modeLabel(mode)+' · 基于 '+(nCtx||0)+' 条记录'+win+(ph==='stream'?' · 生成中…':''));
        const body=ph==='wait'
          ? waitShell()
          : ('<div class="ai-body"><span id="aiCommitted" class="ai-committed"></span><span id="aiFresh" class="ai-fresh"></span>'+(ph==='stream'?'<span class="ai-caret" aria-hidden="true"></span>':'')+'</div>');
        const existing=out.querySelector('.ai-ans');
        // 同卡片内切阶段，避免二次弹入
        if(existing){
          existing.className='ai-ans is-'+ph;
          const labNode=document.getElementById('aiLab');
          if(labNode) labNode.textContent=lab;
          const prog=existing.querySelector('.ai-think')||existing.querySelector('.ai-progress');
          const skel=existing.querySelector('.ai-skel');
          const bod=existing.querySelector('.ai-body');
          if(ph==='wait' && !prog){
            if(bod) bod.remove();
            existing.insertAdjacentHTML('beforeend', body);
          } else if(ph!=='wait' && !bod){
            if(prog) prog.remove();
            else if(skel) skel.remove();
            existing.insertAdjacentHTML('beforeend', body);
          } else if(ph==='stream' && bod && !existing.querySelector('.ai-caret')){
            bod.insertAdjacentHTML('beforeend','<span class="ai-caret" aria-hidden="true"></span>');
          }
          return;
        }
        out.innerHTML='<div class="ai-ans is-'+ph+'"><span class="lab" id="aiLab">'+lab+'</span>'+body+'</div>';
      };
      // Cursor 风格：时间轴自适应速度 + 尾部淡入；落后多时加速追上，不整段蹦字
      const makeSmooth=(pending)=>{
        const committedEl=()=>document.getElementById('aiCommitted');
        const freshEl=()=>document.getElementById('aiFresh');
        const ansEl=()=>out.querySelector('.ai-ans');
        const caretEl=()=>out.querySelector('.ai-caret');
        let chars=[], shown=0, carry=0, raf=0, done=false, last=0, scrollY=mbody?mbody.scrollTop:0;
        const FRESH=reduceMotion?0:18; // 尾部淡入窗口（字）
        const paintText=()=>{
          const c=committedEl(), f=freshEl(); if(!c||!f) return;
          const cut=Math.max(0, shown-FRESH);
          c.textContent=chars.slice(0,cut).join('');
          f.textContent=chars.slice(cut,shown).join('');
        };
        const easeScroll=()=>{
          if(!mbody||reduceMotion) return;
          const body=out.querySelector('.ai-body'); if(!body) return;
          const box=mbody.getBoundingClientRect();
          const br=body.getBoundingClientRect();
          if(br.bottom>box.bottom-28){
            const want=mbody.scrollTop+(br.bottom-box.bottom)+28;
            scrollY+=(want-scrollY)*0.22;
            mbody.scrollTop=scrollY;
          } else {
            scrollY=mbody.scrollTop;
          }
        };
        const tick=(now)=>{
          raf=0;
          if(!last) last=now;
          const dt=Math.min(0.05, (now-last)/1000);
          last=now;
          const lag=chars.length-shown;
          if(lag>0){
            if(reduceMotion){
              shown=chars.length; carry=0;
            } else {
              // 落后少：~36 字/秒更稳；落后多：最高 ~240 字/秒追上
              const t=Math.min(1, lag/64);
              const rate=36 + (240-36)*(t*t*(3-2*t));
              carry+=rate*dt;
              const n=Math.floor(carry);
              if(n>0){ carry-=n; shown=Math.min(chars.length, shown+n); }
            }
            paintText();
            easeScroll();
          }
          if(shown<chars.length || (!done && pending())){
            raf=requestAnimationFrame(tick);
          } else if(done){
            // 收束：把 fresh 并入 committed，去掉光标
            const c=committedEl(), f=freshEl();
            if(c&&f){ c.textContent=chars.join(''); f.textContent=''; }
            const a=ansEl(); if(a){a.classList.remove('is-stream','is-wait');a.classList.add('is-ready');}
            const care=caretEl(); if(care) care.remove();
          }
        };
        const kick=()=>{ if(!raf){ last=0; raf=requestAnimationFrame(tick); } };
        return {
          push(t){
            if(!t) return;
            // 首个 token：从骨架切到正文，保留当前 lab 文案
            if(!chars.length && (out.querySelector('.ai-skel')||out.querySelector('.ai-think')||out.querySelector('.ai-progress'))){
              const prevLab=(document.getElementById('aiLab')||{}).textContent||'';
              paint('lexical', 0, true, 'stream');
              const lab2=document.getElementById('aiLab');
              if(lab2){
                if(/思考|分析|检索|梳理|核对|校验/.test(prevLab)) lab2.textContent='AI 问答 · 生成中…';
                else if(prevLab.includes('检索')) lab2.textContent=prevLab.replace(/检索中…?/,'生成中…');
                else lab2.textContent=prevLab||lab2.textContent;
              }
            }
            chars=chars.concat(Array.from(t));
            kick();
          },
          finish(finalText){
            if(finalText!=null && finalText!==''){
              const next=Array.from(finalText);
              if(next.length>=chars.length) chars=next;
            }
            done=true; kick();
          },
          snap(t){
            chars=Array.from(t||''); shown=chars.length; done=true;
            if(out.querySelector('.ai-skel')||out.querySelector('.ai-think')||out.querySelector('.ai-progress')) paint('lexical',0,false,'ready');
            const c=committedEl(), f=freshEl();
            if(c){ c.textContent=chars.join(''); }
            if(f) f.textContent='';
            const a=ansEl(); if(a){a.classList.remove('is-stream','is-wait');a.classList.add('is-ready');}
            const care=caretEl(); if(care) care.remove();
          }
        };
      };
      askOnce=async()=>{
        const r=await fetch('/api/ask',{method:'POST',headers:{'Content-Type':'application/json','Accept':'application/json'},body:JSON.stringify(meshAskPayload(q)),signal});
        if(!r.ok) throw new Error('HTTP '+r.status);
        const j=await r.json();
        paint(j.mode||'lexical', j.n_context||0, true, 'stream');
        const labEl=document.getElementById('aiLab');
        if(labEl){
          const win=winLabel(j.date_from, j);
          labEl.textContent='AI 问答 · '+modeLabel(j.mode||'lexical')+' · 基于 '+(j.n_context||0)+' 条记录'+win+' · 生成中…';
        }
        const sm=makeSmooth(()=>false);
        sm.push(j.answer||'');
        sm.finish(j.answer||'');
      };
      const preferStream = !(window.MESH && window.MESH.askStream === false);
        if(!preferStream){ await askOnce(); return; }
        paint('lexical', 0, true, 'wait');
        const r=await fetch('/api/ask/stream',{method:'POST',headers:{'Content-Type':'application/json','Accept':'text/event-stream,application/json'},body:JSON.stringify(meshAskPayload(q)),signal});
        if(r.status===404 || r.status===405 || r.status===502 || r.status===503 || r.status===504){ await askOnce(); return; }
        if(!r.ok) throw new Error('HTTP '+r.status);
        const reader=r.body.getReader(); const dec=new TextDecoder();
        let buf='', answer='', mode='lexical', nCtx=0, streaming=true, dateFrom=undefined, dateTo=undefined, queryMeta=null;
        let streamFailed=false, streamDone=false;
        const labEl=()=>document.getElementById('aiLab');
        const smooth=makeSmooth(()=>streaming);
        const setLab=(pending)=>{
          const el=labEl(); if(!el) return;
          const win=winLabel(dateFrom, {date_to: dateTo, query: queryMeta});
          el.textContent='AI 问答 · '+modeLabel(mode)+' · 基于 '+nCtx+' 条记录'+win+(pending?' · 生成中…':'');
        };
        while(true){
          const {value,done}=await reader.read(); if(done) break;
          buf+=dec.decode(value,{stream:true});
          const parts=buf.split('\n\n'); buf=parts.pop()||'';
          for(const block of parts){
            const line=block.split('\n').find(l=>l.startsWith('data: '));
            if(!line) continue;
            let ev; try{ev=JSON.parse(line.slice(6));}catch(_){continue;}
            if(ev.type==='status'){
              if(labEl() && !document.getElementById('aiProgress'))
                labEl().textContent='AI 分析 · '+(ev.message||'处理中')+'…';
            } else if(ev.type==='step'){
              const idx=stageIndex(ev.step||'');
              if(idx>=0) setAnalysisStage(idx);
              // timeout/error 仍推进骨架阶段，不向用户展示技术细节
            } else if(ev.type==='meta'){
              mode=ev.mode||mode; nCtx=ev.n_context||0;
              if('date_from' in ev) dateFrom=ev.date_from;
              if('date_to' in ev) dateTo=ev.date_to;
              if(ev.query) queryMeta=ev.query;
              if(ev.query && 'date_from' in ev.query && dateFrom===undefined) dateFrom=ev.query.date_from;
              if(ev.query && 'date_to' in ev.query && dateTo===undefined) dateTo=ev.query.date_to;
              if(!document.getElementById('aiProgress')) setLab(true);
              else if(nCtx) setAnalysisStage(Math.max(stageIndex('retrieve'), 0));
            } else if(ev.type==='token'){
              const t=ev.text||'';
              answer+=t;
              smooth.push(t);
              setLab(true);
            } else if(ev.type==='replace'){
              if(ev.answer!=null){
                answer=ev.answer;
                smooth.snap(answer);
                setLab(true);
              }
            } else if(ev.type==='error'){
              streaming=false;
              streamFailed=true;
              smooth.snap(ev.message||'问答失败');
              if(labEl()) labEl().textContent='AI 问答 · 失败';
            } else if(ev.type==='done'){
              if(ev.answer!=null) answer=ev.answer;
              streaming=false;
              streamDone=true;
              smooth.finish(answer);
              setLab(false);
            }
          }
        }
        streaming=false;
        if(streamFailed){
          /* 保留 snap 的错误文案，勿用半截 answer 覆盖 */
        }else if(!streamDone){
          if(answer){
            smooth.finish(answer);
            if(labEl()) labEl().textContent=(labEl().textContent||'').replace(/ · 生成中…$/,'')+' · 连接中断，回答可能不完整';
          }else{
            smooth.snap('连接中断，未收到完整回答');
            if(labEl()) labEl().textContent='AI 问答 · 中断';
          }
        }else{
          smooth.finish(answer);
          setLab(false);
        }
      }catch(e){
        if(e&&e.name==='AbortError') return;
        try{
          if(typeof askOnce==='function') await askOnce();
          else throw e;
        }catch(e2){
          if(e2&&e2.name==='AbortError') return;
          out.innerHTML='<div class="ai-ans is-ready">问答失败：'+esc(e2.message||e.message)+'</div>';
        }
      }finally{
        ask._busy=false;
      }
    }
    function bindSearchOpen(box){
      const inp=box.querySelector('input[type="search"],input[type="text"]');
      if(!inp)return;
      const open=()=>openModal(inp.value,'kw');
      box.addEventListener('click',e=>{if(e.target.closest('button,a'))return;open();});
      inp.addEventListener('focus',()=>{open();inp.blur();});
    }
    document.querySelectorAll('.search.big,.nav .search').forEach(bindSearchOpen);
    const nb=document.getElementById('navSbtn'); if(nb) nb.addEventListener('click',()=>openModal('','kw'));
    document.querySelectorAll('.mtabs button').forEach(b=>b.addEventListener('click',()=>{
      tab=b.dataset.tab;
      document.querySelectorAll('.mtabs button').forEach(x=>x.classList.toggle('on',x===b));
      if(tab!=='ai') cancelAsk();
      render();
      mq.focus();
    }));
    mq.addEventListener('input',()=>{clearTimeout(timer);timer=setTimeout(()=>{if(tab==='kw')render();},180);});
    mq.addEventListener('keydown',e=>{if(e.key==='Enter'&&tab==='ai'){e.preventDefault();ask();}});
    document.getElementById('mx').addEventListener('click',closeModal);
    modal.addEventListener('click',e=>{if(e.target===modal)closeModal();});
    document.addEventListener('keydown',e=>{if(e.key==='Escape'){closeModal();document.querySelectorAll('.pop').forEach(p=>p.classList.remove('show'));} if(e.key==='/'&&document.activeElement.tagName!=='INPUT'&&document.activeElement.contentEditable!=='true'){e.preventDefault();openModal('','kw');}});
    // URL ?q= 打开搜索；?ask=1 则进 AI 问答并自动提问（EDM 预搜索链接）
    const urlParams=new URLSearchParams(location.search);
    const qp=urlParams.get('q');
    const askAuto=urlParams.get('ask')==='1';
    const peek=urlParams.get('peek');
    const peekAt=urlParams.get('at')||'';
    if(peek){
      let target=null;
      if(peekAt) target=idx.find(x=>x.title===peekAt)||idx.find(x=>x.title.includes(peekAt));
      if(!target) target=idx.find(x=>x.text.toLowerCase().includes(peek.toLowerCase()));
      if(target) setTimeout(()=>jumpToHit(target),150);
    }else if(qp!==null){
      if(askAuto){
        openModal(qp,'ai');
        setTimeout(()=>ask(),40);
      }else{
        openModal(qp,'kw');
      }
    }
    // 往期弹层
    document.querySelectorAll('.calbtn[data-pop]').forEach(b=>{const pop=b.parentElement.querySelector('.pop');b.addEventListener('click',e=>{e.stopPropagation();const on=pop.classList.contains('show');document.querySelectorAll('.pop').forEach(p=>p.classList.remove('show'));document.querySelectorAll('.calbtn').forEach(x=>x.classList.remove('on'));if(!on){pop.classList.add('show');b.classList.add('on');}});});
    document.addEventListener('click',e=>{if(!e.target.closest('.search')){document.querySelectorAll('.pop').forEach(p=>p.classList.remove('show'));document.querySelectorAll('.calbtn').forEach(x=>x.classList.remove('on'));}});
  })();

  // ===== 智能搜索入口：占位文案轮播 =====
  (function(){const inp=document.querySelector('.hero .search.big input');if(!inp)return;
    const P=CFG.placeholders||['搜人：韩乾源、Lilyann、王小川…','搜公司：破壳创智、vivo、Armaro Capital…','搜关键词：具身智能、AI 助听器、峰谷定价…','问一句：哪些硬件公司编辑部接触过、Founder Park 还没接触？','问一句：最近哪些海外接触国内还没有部门跟进？'];
    let i=0,c=0,dir=1,t;const mm=window.matchMedia('(prefers-reduced-motion: reduce)');
    if(mm.matches){inp.placeholder=P[3];return;}
    function step(){if(document.activeElement===inp||inp.value){t=setTimeout(step,800);return;}
      const s=P[i];c+=dir;inp.placeholder=s.slice(0,c)+(dir>0?'▍':'');inp.classList.toggle('typing',true);
      if(c>=s.length&&dir>0){dir=-1;t=setTimeout(step,1600);return;}
      if(c<=0&&dir<0){dir=1;i=(i+1)%P.length;t=setTimeout(step,300);return;}
      t=setTimeout(step,dir>0?42:16);}
    step();})();

  // ===== 编辑模式（editor/admin/owner）：双击编辑写回 /api/edit；删卡；新增卡；标签；预览页发布 =====
  (function(){
    if(!CFG.canEdit){
      const puberr=new URLSearchParams(location.search).get('puberr');
      if(puberr){const t=document.getElementById('toast');if(t){t.textContent=puberr;t.classList.add('show');}}
      return;
    }
    const toast=document.getElementById('toast');let tt=null;
    const COLORS=CFG.flagColors||{};
    const LABELS=Object.keys(COLORS);
    let pickEl=null;
    function say(m,ms){if(!toast)return;toast.textContent=m;toast.classList.add('show');clearTimeout(tt);tt=setTimeout(()=>toast.classList.remove('show'),ms||1800);}
    const previewNote=document.getElementById('previewNote');
    const qs=new URLSearchParams(location.search);
    function setPreviewNote(on){
      if(!previewNote)return;
      previewNote.hidden=!on;
    }
    function stripEditParam(){
      if(!CFG.isPreview)return;
      const u=new URL(location.href);
      if(u.searchParams.get('edit')!=='1')return;
      u.searchParams.delete('edit');
      u.searchParams.delete('sync');
      history.replaceState(null,'',u.pathname+u.search);
    }
    function exitEdit(){
      document.body.classList.remove('editor');
      const btn=document.getElementById('editToggle');
      btn&&btn.classList.remove('on');
      closePick();
      setPreviewNote(false);
      stripEditParam();
      say('已退出编辑模式');
    }
    const puberr=new URLSearchParams(location.search).get('puberr');
    if(puberr) say(puberr, 8000);
    async function save(path,value,op){
      const r=await fetch('/api/edit',{method:'POST',headers:{'Content-Type':'application/json','Accept':'application/json'},body:JSON.stringify({slug:CFG.slug,target:CFG.target,path,value,op})});
      if(!r.ok){
        let msg='保存失败';
        try{const j=await r.json();msg+='：'+(j.detail||j.error||r.status);}catch(_){msg+='：'+r.status;}
        say(msg);return false;
      }
      return true;
    }
    function closePick(){if(pickEl){pickEl.remove();pickEl=null;}}
    function applyFlagStyle(flag, lab){
      flag.textContent=lab;
      flag.style.background=COLORS[lab]||'#344054';
    }
    function startFlagTextEdit(flag){
      closePick();
      flag.contentEditable='true';
      flag.dataset.orig=flag.textContent;
      flag.focus();
      const range=document.createRange();range.selectNodeContents(flag);
      const sel=window.getSelection();sel.removeAllRanges();sel.addRange(range);
    }
    function openFlagPick(flag){
      closePick();
      const box=document.createElement('div');box.className='flag-pick';
      const head=document.createElement('div');head.className='fh';head.textContent='选择标签，或点底部直接改文字';box.appendChild(head);
      const cur=(flag.textContent||'').trim();
      LABELS.forEach(lab=>{
        const b=document.createElement('button');b.type='button';
        b.className=lab===cur?'on':'';
        b.innerHTML='<i style="background:'+(COLORS[lab]||'#344054')+'"></i><span></span>';
        b.querySelector('span').textContent=lab;
        b.addEventListener('mousedown',e=>e.preventDefault());
        b.addEventListener('click',async e=>{
          e.stopPropagation();
          if(lab===cur){closePick();return;}
          if(await save(flag.dataset.path,lab)){
            applyFlagStyle(flag,lab);
            say('标签已保存');
          }
          closePick();
        });
        box.appendChild(b);
      });
      const free=document.createElement('button');free.type='button';free.className='free';free.textContent='直接改文字…';
      free.addEventListener('mousedown',e=>e.preventDefault());
      free.addEventListener('click',e=>{e.stopPropagation();startFlagTextEdit(flag);});
      box.appendChild(free);
      document.body.appendChild(box);pickEl=box;
      const r=flag.getBoundingClientRect();
      const top=Math.min(window.innerHeight-12-box.offsetHeight, Math.max(12, r.bottom+6));
      const left=Math.min(window.innerWidth-12-box.offsetWidth, Math.max(12, r.left));
      box.style.top=top+'px';box.style.left=left+'px';
    }
    function markEditable(){
      function escHtml(t){return String(t||'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
      function tipFromRows(rows){
        return (rows||[]).map(r=>String(r.k||'')+'::'+String(r.v||'')).join('||');
      }
      function attachEntDelete(ent){
        if(ent.querySelector('.x'))return;
        const x=document.createElement('button');
        x.type='button';x.className='x';x.title='删除标签';x.textContent='×';
        x.addEventListener('click',async e=>{
          e.preventDefault();e.stopPropagation();
          if(!(await MeshDialog.confirm({title:'删除标签',body:'删除这个标签？',okText:'删除',danger:true})))return;
          if(await save(ent.dataset.path,null,'delete')){ent.remove();say('已删除');}
        });
        ent.appendChild(x);
      }
      function makeEnt(basePath, item, kind){
        const ent=document.createElement('span');
        ent.className='ent';
        ent.dataset.path=basePath;
        ent.dataset.tipPath=basePath;
        ent.dataset.tip=tipFromRows(item.rows);
        ent.dataset.teams=(item.teams||[]).join('|');
        ent.dataset.w=String(item.weight||0);
        if(kind==='plan' && item.cert) ent.dataset.cert=item.cert;
        let h='<span data-path="'+escHtml(basePath)+'.name">'+escHtml(item.name||'新标签')+'</span>';
        if(item.sub) h+='<small data-path="'+escHtml(basePath)+'.sub">'+escHtml(item.sub)+'</small>';
        if(kind==='plan' && item.cert) h+='<small class="cert" data-path="'+escHtml(basePath)+'.cert">'+escHtml(item.cert)+'</small>';
        ent.innerHTML=h;
        attachEntDelete(ent);
        return ent;
      }
      function wireAddTag(g){
        if(g.querySelector('.addtag'))return;
        const a=document.createElement('button');
        a.type='button';a.className='addtag';a.textContent='＋ 新增';
        a.title='在本组新增标签';
        a.addEventListener('click',async e=>{
          e.preventDefault();e.stopPropagation();
          if(a.disabled)return;
          a.disabled=true;
          try{
            const r=await fetch('/api/add_item',{method:'POST',headers:{'Content-Type':'application/json','Accept':'application/json'},body:JSON.stringify({slug:CFG.slug,target:CFG.target,path:g.dataset.addPath,kind:g.dataset.addKind||'tag'})});
            const j=await r.json().catch(()=>({}));
            if(!r.ok){say('新增失败：'+(j.detail||j.error||r.status));return;}
            const base=(g.dataset.addPath||'')+'.'+j.index;
            const ent=makeEnt(base, j.item||{name:'新标签',rows:[{k:'说明',v:'双击修改'}],weight:1,cert:(g.dataset.addKind==='plan'?'计划中':undefined)}, g.dataset.addKind||'tag');
            g.insertBefore(ent, a);
            bindTips();
            say('已新增标签');
            const nameEl=ent.querySelector('[data-path$=".name"]');
            if(nameEl){
              nameEl.contentEditable='true';
              nameEl.dataset.orig=nameEl.textContent;
              nameEl.focus();
              try{document.execCommand('selectAll',false,null);}catch(_){}
            }
          }finally{a.disabled=false;}
        });
        g.appendChild(a);
      }
      function syncRelationsKpi(){
      // 只计读者区 #rel 卡，不含 #rel-backlog 草稿积压
      const n=document.querySelectorAll('#rel .cards > .card[data-path^="relations."]').length;
      document.querySelectorAll('.kpis .kpi').forEach(k=>{
        const lab=(k.querySelector('span')?.textContent||'').trim();
        if(lab==='可同步的关系'){
          const b=k.querySelector('b');
          if(b) b.textContent=String(n);
        }
      });
    }
    document.querySelectorAll('.card').forEach(c=>{if(!c.dataset.path||c.querySelector('.del'))return;const d=document.createElement('button');d.type='button';d.className='del';d.title='删除卡片';d.textContent='×';
        d.addEventListener('click',async e=>{e.preventDefault();e.stopPropagation();if(!(await MeshDialog.confirm({title:'删除卡片',body:'删除这张卡片？会写入版本记录。',okText:'删除',danger:true})))return;if(await save(c.dataset.path,null,'delete')){c.remove();if((c.dataset.path||'').startsWith('relations.'))syncRelationsKpi();say('已删除并保存');}});c.appendChild(d);});
      document.querySelectorAll('.cards[data-addable]').forEach(g=>{
        if(g.querySelector('.addcard'))return;
        const section=g.dataset.addable||'relations';
        const a=document.createElement('div');
        a.className='addcard';
        a.textContent=section==='contacts'?'＋ 新增人和公司卡片':'＋ 新增卡片';
        a.addEventListener('click',async e=>{
          e.preventDefault();e.stopPropagation();
          if(a.dataset.busy==='1')return;
          a.dataset.busy='1';
          try{
            const r=await fetch('/api/add_card',{method:'POST',headers:{'Content-Type':'application/json','Accept':'application/json'},body:JSON.stringify({slug:CFG.slug,target:CFG.target,section})});
            const j=await r.json().catch(()=>({}));
            if(!r.ok){say('新增失败：'+(j.detail||j.error||r.status));return;}
            const idx=j.index;
            const card=j.card||{};
            const sec=j.section||section;
            const path=sec+'.'+idx;
            const el=document.createElement('div');
            el.className='card';
            el.dataset.path=path;
            if(sec==='contacts'){
              const groups=card.groups||[];
              let gh='';
              groups.forEach((grp,gi)=>{
                const itemsPath=path+'.groups.'+gi+'.items';
                gh+='<div class="grp" data-path="'+path+'.groups.'+gi+'.title">'+escHtml(grp.title||'新分组')+'</div>';
                gh+='<div class="ents" data-add-path="'+itemsPath+'" data-add-kind="tag">';
                (grp.items||[]).forEach((it,ii)=>{ /* filled below after insert */ });
                gh+='</div>';
              });
              el.innerHTML=
                '<span class="flag" data-path="'+path+'.label" title="单击换标签，双击改文字" style="background:'+(COLORS[card.label]||'#1E3A8A')+'">'+escHtml(card.label||'一手接触')+'</span>'+
                '<p class="t" data-path="'+path+'.title">'+escHtml(card.title||'新卡片')+'</p>'+
                '<p class="b" data-path="'+path+'.body">'+escHtml(card.body||'')+'</p>'+
                '<div class="detail">'+gh+(card.note?'<p class="note" style="margin-top:10px" data-path="'+path+'.note">'+escHtml(card.note)+'</p>':'')+'</div>'+
                '<div class="srcs">'+(card.sources||[]).map((s,si)=>'<p><span class="lab">来源</span><span data-path="'+path+'.sources.'+si+'">'+escHtml(s)+'</span></p>').join('')+'</div>';
              const detail=el.querySelector('.detail');
              groups.forEach((grp,gi)=>{
                const ents=detail.querySelectorAll('.ents')[gi];
                if(!ents)return;
                (grp.items||[]).forEach((it,ii)=>{
                  ents.appendChild(makeEnt(path+'.groups.'+gi+'.items.'+ii, it, 'tag'));
                });
                wireAddTag(ents);
              });
            }else{
              el.innerHTML=
                '<span class="flag" data-path="'+path+'.label" title="单击换标签，双击改文字" style="background:'+(COLORS[card.label]||'#344054')+'">'+escHtml(card.label||'已联动')+'</span>'+
                '<p class="t" data-path="'+path+'.title">'+escHtml(card.title||'新卡片')+'</p>'+
                '<p class="b" data-path="'+path+'.body">'+escHtml(card.body||'')+'</p>'+
                '<div class="srcs">'+(card.sources||[]).map((s,si)=>'<p><span class="lab">来源</span><span data-path="'+path+'.sources.'+si+'">'+escHtml(s)+'</span></p>').join('')+'</div>'+
                '<div class="deps">'+(card.teams||[]).map((t,ti)=>'<span class="dep'+((/^→|^->/.test(String(t||'')))?' dep-sug':'')+'" data-path="'+path+'.teams.'+ti+'">'+escHtml(t)+'</span>').join('')+'</div>';
            }
            g.insertBefore(el, a);
            if(sec==='relations') syncRelationsKpi();
            // 给新卡挂删除钮
            if(!el.querySelector('.del')){
              const d=document.createElement('button');d.type='button';d.className='del';d.title='删除卡片';d.textContent='×';
              d.addEventListener('click',async ev=>{ev.preventDefault();ev.stopPropagation();if(!(await MeshDialog.confirm({title:'删除卡片',body:'删除这张卡片？会写入版本记录。',okText:'删除',danger:true})))return;if(await save(el.dataset.path,null,'delete')){el.remove();if((el.dataset.path||'').startsWith('relations.'))syncRelationsKpi();say('已删除并保存');}});
              el.appendChild(d);
            }
            if(sec==='contacts' && !el.querySelector('.addgroup')){
              const detail=el.querySelector('.detail');
              if(detail){
                const ag=document.createElement('button');
                ag.type='button';ag.className='addgroup';ag.textContent='＋ 新增分组';
                ag.addEventListener('click',async ev=>{
                  ev.preventDefault();ev.stopPropagation();
                  const rr=await fetch('/api/add_item',{method:'POST',headers:{'Content-Type':'application/json','Accept':'application/json'},body:JSON.stringify({slug:CFG.slug,target:CFG.target,path:path+'.groups',kind:'group'})});
                  const jj=await rr.json().catch(()=>({}));
                  if(!rr.ok){say('新增失败：'+(jj.detail||jj.error||rr.status));return;}
                  insertGroup(detail, path+'.groups', jj, ag);
                });
                detail.appendChild(ag);
              }
            }
            bindTips();
            say('已新增卡片');
            const title=el.querySelector('.t');
            if(title){title.contentEditable='true';title.dataset.orig=title.textContent;title.focus();}
          }finally{a.dataset.busy='0';}
        });
        g.appendChild(a);
      });
      function insertGroup(detail, groupsPath, j, beforeEl){
        const gi=j.index;
        const item=j.item||{title:'新分组',items:[{name:'新对象',rows:[{k:'说明',v:'双击修改'}],weight:1}]};
        const titlePath=groupsPath+'.'+gi+'.title';
        const itemsPath=groupsPath+'.'+gi+'.items';
        const grp=document.createElement('div');
        grp.className='grp';grp.dataset.path=titlePath;grp.textContent=item.title||'新分组';
        const ents=document.createElement('div');
        ents.className='ents';ents.dataset.addPath=itemsPath;ents.dataset.addKind='tag';
        (item.items||[]).forEach((it,ii)=>ents.appendChild(makeEnt(itemsPath+'.'+ii, it, 'tag')));
        wireAddTag(ents);
        detail.insertBefore(grp, beforeEl);
        detail.insertBefore(ents, beforeEl);
        bindTips();
        say('已新增分组');
        grp.contentEditable='true';grp.dataset.orig=grp.textContent;grp.focus();
      }
      // 联系人卡片内也可新增分组
      document.querySelectorAll('#who .card[data-path]').forEach(card=>{
        if(card.querySelector('.addgroup'))return;
        const detail=card.querySelector('.detail');
        if(!detail)return;
        const path=card.dataset.path; // contacts.0
        const a=document.createElement('button');
        a.type='button';a.className='addgroup';a.textContent='＋ 新增分组';
        a.addEventListener('click',async e=>{
          e.preventDefault();e.stopPropagation();
          const r=await fetch('/api/add_item',{method:'POST',headers:{'Content-Type':'application/json','Accept':'application/json'},body:JSON.stringify({slug:CFG.slug,target:CFG.target,path:path+'.groups',kind:'group'})});
          const j=await r.json().catch(()=>({}));
          if(!r.ok){say('新增失败：'+(j.detail||j.error||r.status));return;}
          insertGroup(detail, path+'.groups', j, a);
        });
        detail.appendChild(a);
      });
      // 关键词 / 日程 / 联系人：每组末尾＋新增标签；每个标签可×删除
      document.querySelectorAll('.ents[data-add-path]').forEach(g=>{
        wireAddTag(g);
        g.querySelectorAll('.ent[data-path]').forEach(attachEntDelete);
      });
    }
    let flagClickT=null;
    document.addEventListener('click',e=>{
      if(!document.body.classList.contains('editor'))return;
      const flag=e.target.closest('.flag[data-path]');
      if(flag && flag.contentEditable!=='true'){
        e.preventDefault();
        e.stopPropagation();
        clearTimeout(flagClickT);
        flagClickT=setTimeout(()=>openFlagPick(flag),220);
        return;
      }
      if(pickEl&&!e.target.closest('.flag-pick')&&!e.target.closest('.flag[data-path]'))closePick();
    });
    document.addEventListener('dblclick',e=>{
      if(!document.body.classList.contains('editor'))return;
      const flag=e.target.closest('.flag[data-path]');
      if(flag){
        e.preventDefault();
        e.stopPropagation();
        clearTimeout(flagClickT);
        startFlagTextEdit(flag);
        return;
      }
      // 优先点中的最深一层带 path 的节点（名字 / 小标签 / 来源 / 分组标题 / tip 字段）
      const el=e.target.closest('[data-path]');
      if(!el||el.classList.contains('card')||el.classList.contains('flag')||el.classList.contains('ent'))return;
      e.preventDefault();
      el.contentEditable='true';
      el.dataset.orig=el.textContent;
      el.focus();
    });
    document.addEventListener('focusout',async e=>{
      const el=e.target;
      if(!(el&&el.dataset&&el.dataset.path&&el.contentEditable==='true'))return;
      el.contentEditable='false';
      const v=el.textContent.trim();
      if(v===(el.dataset.orig||'').trim())return;
      if(await save(el.dataset.path,v)){
        if(el.classList.contains('flag')) applyFlagStyle(el,v);
        // tip 内改完后，同步回对应 chip 的 data-tip，避免悬停还是旧文案
        const tipHost=el.closest('#tip');
        if(tipHost){
          const path=el.dataset.path||'';
          const m=path.match(/^(.*)\.rows\.(\d+)\.(k|v)$/);
          if(m){
            const host=document.querySelector('.ent[data-tip-path="'+m[1]+'"]');
            if(host){
              const rows=(host.dataset.tip||'').split('||').map(r=>{const p=r.split('::');return {k:p[0]||'',v:p.slice(1).join('::')};});
              const idx=+m[2];
              while(rows.length<=idx) rows.push({k:'',v:''});
              rows[idx][m[3]]=v;
              host.dataset.tip=rows.map(r=>r.k+'::'+r.v).join('||');
            }
          }
        }
        say('已保存并写入版本记录');
      }
    });
    document.addEventListener('keydown',e=>{
      if(e.key!=='Escape')return;
      closePick();
      const pub=document.getElementById('pubMask');
      if(pub&&!pub.hidden&&pub.classList.contains('on'))return;
      const a=document.activeElement;
      if(a&&a.contentEditable==='true'){
        a.textContent=a.dataset.orig||a.textContent;
        a.blur();
        return;
      }
      if(document.body.classList.contains('editor')) exitEdit();
    });
    const btn=document.getElementById('editToggle');
    if(btn){btn.addEventListener('click',()=>{
      // 读者页不能直接改线上稿：跳进草稿预览，改完再上线
      if(!CFG.isPreview){
        location.href='/'+CFG.slug+'?preview=1&edit=1&sync=1';
        return;
      }
      const on=document.body.classList.toggle('editor');
      btn.classList.toggle('on',on);
      if(on){markEditable();setPreviewNote(CFG.isPreview);say(CFG.canPublish?'编辑草稿中 · 改完点「重新上线」':'编辑草稿中 · 上线需所有者确认');}
      else exitEdit();
    });}
    if(qs.get('edit')==='1'&&btn&&!document.body.classList.contains('editor')) btn.click();
    else if(CFG.isPreview&&qs.get('edit')!=='1') setPreviewNote(true);
    const ex=document.getElementById('edExit');if(ex)ex.addEventListener('click',exitEdit);
    const es=document.getElementById('edSave');if(es)es.addEventListener('click',()=>say(CFG.canPublish?'已写入草稿；读者页要等「重新上线」后才更新':'已写入草稿；发布请所有者确认上线'));

    // 预览页直接发布（仅 owner）——居中弹窗
    const pubBtn=document.getElementById('edPublish');
    const mask=document.getElementById('pubMask');
    if(pubBtn&&mask){
      const ok=document.getElementById('pubOk');
      const cancel=document.getElementById('pubCancel');
      const pubForm=mask.querySelector('form');
      const okLabel=ok?(ok.textContent||'确认上线'):'确认上线';
      let pubAbort=null;
      function resetPubUi(){
        if(pubForm) pubForm.dataset.submitting='0';
        if(ok){
          ok.disabled=false;
          ok.textContent=okLabel;
        }
      }
      function openPub(){
        // 上次上线卡住/失败后关闭弹窗时，必须清掉 disabled，否则再点「没反应」
        if(pubAbort){try{pubAbort.abort();}catch(_){}}
        pubAbort=null;
        resetPubUi();
        mask.hidden=false;
        mask.classList.add('on');
        document.body.style.overflow='hidden';
      }
      function closePub(){
        mask.classList.remove('on');
        mask.hidden=true;
        document.body.style.overflow='';
        // 关闭时若未在飞行中，恢复按钮；飞行中保留禁用，等回调重置
        if(!pubForm||pubForm.dataset.submitting!=='1') resetPubUi();
      }
      pubBtn.addEventListener('click',e=>{e.preventDefault();e.stopPropagation();openPub();});
      if(cancel)cancel.addEventListener('click',()=>{if(pubAbort){try{pubAbort.abort();}catch(_){}}pubAbort=null;resetPubUi();closePub();});
      mask.addEventListener('click',e=>{if(e.target===mask){if(pubAbort){try{pubAbort.abort();}catch(_){}}pubAbort=null;resetPubUi();closePub();}});
      document.addEventListener('keydown',e=>{if(e.key==='Escape'&&mask.classList.contains('on')){if(pubAbort){try{pubAbort.abort();}catch(_){}}pubAbort=null;resetPubUi();closePub();}});
      if(pubForm){
        pubForm.addEventListener('submit',async e=>{
          e.preventDefault();
          if(pubForm.dataset.submitting==='1')return;
          pubForm.dataset.submitting='1';
          if(ok){ok.disabled=true;ok.textContent='上线中…';}
          pubAbort=typeof AbortController!=='undefined'?new AbortController():null;
          const timer=setTimeout(()=>{try{pubAbort&&pubAbort.abort();}catch(_){}},90000);
          try{
            const body=new URLSearchParams();
            const r=await fetch(pubForm.action,{
              method:'POST',
              headers:{Accept:'application/json','Content-Type':'application/x-www-form-urlencoded'},
              credentials:'same-origin',
              body:body.toString(),
              signal:pubAbort?pubAbort.signal:undefined,
            });
            const data=await r.json().catch(()=>({}));
            if(!r.ok||data.ok===false) throw new Error((data&&data.error)||('HTTP '+r.status));
            location.href='/'+CFG.slug+'?published=1';
          }catch(err){
            const aborted=err&&(err.name==='AbortError'||/abort/i.test(String(err.message||'')));
            pubAbort=null;
            resetPubUi();
            closePub();
            if(aborted){
              if(window.MeshDialog) await MeshDialog.alert({title:'已取消',body:'上线请求已取消，可再试一次。'});
              return;
            }
            if(window.MeshDialog) await MeshDialog.alert({title:'上线失败',body:String(err.message||err)});
            else say('上线失败：'+String(err.message||err),8000);
          }finally{
            clearTimeout(timer);
          }
        });
      }
    }
  })();

  // ===== 菜单高亮 =====
  const links=[...document.querySelectorAll('#menu a')];
  const secs=links.map(a=>document.querySelector(a.getAttribute('href'))).filter(Boolean);
  if(secs.length){const io=new IntersectionObserver(es=>{es.forEach(e=>{if(e.isIntersecting){links.forEach(l=>l.classList.toggle('active',l.getAttribute('href')==='#'+e.target.id));}});},{rootMargin:'-30% 0px -60% 0px'});secs.forEach(s=>io.observe(s));}
})();
