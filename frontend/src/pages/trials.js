import {trials} from '../api/entities.js';
import {session} from '../stores/session.js';
import {heading} from '../components/common/shell.js';
import {esc,date,notify} from '../utils/dom.js';
import {submit} from '../hooks/forms.js';
import {TrialStatus,ReviewStatus} from '../constants/enums.js';

let currentId=null;
const isOwner=()=>session.user.role==='admin';
const isInspector=()=>['admin','inspector'].includes(session.user.role);

function trialBadge(t){
 return '<span class="status trial-'+esc(t.status)+'">'+esc(TrialStatus[t.status]||t.status)+'</span>';
}

function reviewLine(t){
 if(!t.review)return t.status==='sealed'?'封存后等待揭盲':'—';
 const r=t.review;
 return esc(ReviewStatus[r.status]||r.status)+(r.approved?' · 已批准':'')+(r.error?' · '+esc(r.error):'');
}

function trialList(list,selectedId){
 return '<div class="trial-list">'+list.map(t=>'<a class="trial-item '+(t.id===selectedId?'active':'')+'" href="#/trials/'+t.id+'"><div><strong>'+esc(t.name)+'</strong>'+trialBadge(t)+'</div><p>'+esc(t.line)+' 窑线 · '+t.group_count+' 组 · '+t.inspected_count+'/'+t.unit_count+' 窑已检验</p><small>复盘：'+reviewLine(t)+'</small></a>').join('')+(!list.length?'<div class="empty">尚无盲评试验。负责人可登记新试验。</div>':'')+'</div>';
}

function registerForm(){
 return '<section class="panel"><div class="section-title"><h2>登记盲评试验</h2><span>负责人 · 同窑线两组</span></div><form id="trial-form"><label>试验名称<input name="name" required minlength="2" placeholder="例如：11 月釉料 A/B 盲评"></label><label>窑线<input name="line" required placeholder="例如：东窑"></label><div class="parameter-grid"><label>甲组名称<input name="group0" required value="甲组"></label><label>乙组名称<input name="group1" required value="乙组"></label></div><label>甲组配方<input name="recipe0" required placeholder="例如：釉料 A · 还原气氛"></label><label>乙组配方<input name="recipe1" required placeholder="例如：釉料 B · 还原气氛"></label><label>甲组窑次<textarea name="batches0" rows="3" required placeholder="每行一个窑次，例如 QH-001"></textarea></label><label>乙组窑次<textarea name="batches1" rows="3" required placeholder="每行一个窑次，例如 QH-002"></textarea></label><button type="submit" class="primary">登记并占用窑次</button><p class="hint">锁定后同一窑次只归一个在途试验；并发锁定会拒绝重复占用。检验员只会看到盲码。</p></form></section>';
}

function inspectionQueue(queue,owner){
 const rows=queue.map(q=>'<tr><td><span class="blind-code">'+esc(q.blind_code)+'</span></td><td>'+esc(TrialStatus[q.trial_status]||q.trial_status)+'</td><td>'+(q.inspected?'已登记 · v'+q.revision:'待检验')+'</td><td>'+(q.inspected?esc(q.fired)+' / '+esc(q.passed):'—')+'</td><td>'+(q.trial_status==='sealed'||q.trial_status==='unblinded'?'已冻结':(owner||isInspector())?'<button class="text-button" data-fill="'+esc(q.blind_code)+'" data-revision="'+q.revision+'">'+(q.inspected?'修订':'登记')+'</button>':'—')+'</td></tr>').join('');
 return '<section class="panel"><div class="section-title"><h2>盲码检验队列</h2><span>'+queue.length+' 个盲码 · 仅显示盲码与件数</span></div>'+(queue.length?'<div class="table-wrap"><table><thead><tr><th>盲码</th><th>试验状态</th><th>检验</th><th>烧成 / 合格</th><th></th></tr></thead><tbody>'+rows+'</tbody></table></div>':'<div class="empty">暂无待检验盲码。试验锁定后，盲码会出现在这里。</div>')+'<form id="inspection-form" class="inline-form"><label>盲码<input name="blind_code" required placeholder="BLD-XXXXXX"></label><div class="parameter-grid"><label>烧成件数<input name="fired" type="number" min="1" required></label><label>合格件数<input name="passed" type="number" min="0" required></label></div><label>修订版本<input name="revision" type="number" min="0" placeholder="首次登记留空"></label><input type="hidden" name="trial_id"><button type="submit" class="primary">登记检验件数</button><p class="hint">登记后返回盲码与版本号；再次修改须带上当前版本，过期版本会被拒绝。</p></form></section>';
}

function ownerActions(t){
 if(t.status==='registered')return '<button class="primary" id="lock-trial">锁定窑次并启用盲码</button> <button class="button" id="terminate-trial">终止试验</button>';
 if(t.status==='locked')return '<form id="seal-form" class="inline-seal"><input type="hidden" name="revision" value="'+t.version+'"><span>检验版本 v'+t.version+' · '+t.inspected_count+'/'+t.unit_count+' 窑齐全</span><button class="primary" type="submit">封存检验记录</button><button class="button" type="button" id="terminate-trial">终止试验</button></form>';
 if(t.status==='sealed')return '<button class="primary" id="unblind-trial">揭盲并生成两组复盘</button>';
 if(t.status==='unblinded')return '<button class="button" id="retry-review">重试复盘</button> <button class="primary" id="approve-review" '+(t.review?.status==='completed'&&!t.review.approved&&t.result?'':'disabled')+'>批准复盘</button>';
 return '';
}

function groupTable(t){
 if(!t.groups)return '';
 return '<div class="two-columns trial-groups">'+t.groups.map(g=>'<section class="panel"><div class="section-title"><h2>'+esc(g.name)+'</h2><span>'+g.units.length+' 窑</span></div><p class="recipe">配方：'+esc(g.recipe)+'</p><div class="code-grid">'+g.units.map(u=>'<div class="code-card '+(u.inspected?'done':'')+'"><span class="blind-code">'+esc(u.blind_code)+'</span><small>'+esc(u.batch)+(u.inspected?' · 已检验':' · 待检验')+'</small></div>').join('')+'</div></section>').join('')+'</div>';
}

function blindTable(t){
 const codes=t.blind_codes||[];
 return '<section class="panel"><div class="section-title"><h2>盲态检验单</h2><span>'+codes.length+' 个盲码 · 不显示配方与窑次</span></div><div class="table-wrap"><table><thead><tr><th>盲码</th><th>检验</th><th>烧成 / 合格</th><th>版本</th></tr></thead><tbody>'+codes.map(c=>'<tr><td><span class="blind-code">'+esc(c.blind_code)+'</span></td><td>'+(c.inspected?'已登记':'待检验')+'</td><td>'+(c.inspected?esc(c.fired)+' / '+esc(c.passed):'—')+'</td><td>'+(c.inspected?'v'+c.revision:'—')+'</td></tr>').join('')+'</tbody></table></div></section>';
}

function resultView(t){
 const r=t.result;
 if(!r)return '<section class="panel"><div class="section-title"><h2>两组复盘</h2><span>'+(ReviewStatus[t.review?.status]||'生成中')+'</span></div><div class="empty">'+(t.review?.status==='failed'?'复盘生成失败：'+esc(t.review.error||'')+'。可点击“重试复盘”。':'揭盲后由后台生成两组复盘，稍后刷新查看。')+'</div>'+(t.review?.status==='failed'&&isOwner()?'':'')+'</section>';
 return '<section class="panel"><div class="section-title"><h2>两组复盘</h2><span>'+(t.review?.approved?'已批准':'待批准')+'</span></div><h3 class="trial-summary">'+esc(r.summary)+'</h3><div class="metrics">'+(r.metrics||[]).map(m=>'<div><span>'+esc(m.label)+'</span><strong>'+esc(m.value)+'<small>'+esc(m.unit||'')+'</small></strong></div>').join('')+'</div><div class="table-wrap"><table><thead><tr><th>组别</th><th>配方</th><th>窑次</th><th>烧成</th><th>合格</th><th>加权良率</th><th>原始检验</th></tr></thead><tbody>'+(r.groups||[]).map(g=>'<tr><td>'+esc(g.name)+'</td><td>'+esc(g.recipe)+'</td><td>'+g.units+'</td><td>'+g.fired+'</td><td>'+g.passed+'</td><td>'+esc(g.yield)+'%</td><td>#'+(g.linked_inspections||[]).join('、#')+'</td></tr>').join('')+'</tbody></table></div><ol class="questions">'+(r.findings||[]).map(v=>'<li>'+esc(v)+'</li>').join('')+'</ol>'+(r.warnings||[]).map(v=>'<p class="warning">'+esc(v)+'</p>').join('')+'</section>';
}

function revealedTable(t){
 if(!t.revealed)return '';
 return '<section class="panel"><div class="section-title"><h2>揭盲关联原始检验</h2><span>'+t.revealed.length+' 窑</span></div><div class="table-wrap"><table><thead><tr><th>盲码</th><th>窑次</th><th>组别</th><th>烧成 / 合格</th><th>原始检验</th><th>检验版本</th></tr></thead><tbody>'+t.revealed.map(r=>'<tr><td><span class="blind-code">'+esc(r.blind_code)+'</span></td><td>'+esc(r.batch)+'</td><td>'+esc(r.group)+'</td><td>'+esc(r.fired)+' / '+esc(r.passed)+'</td><td>#'+esc(r.inspection_id)+'</td><td>v'+esc(r.inspection_revision)+'</td></tr>').join('')+'</tbody></table></div></section>';
}

export async function trialPage(id){
 const owner=isOwner();
 const list=await trials.list();
 const selected=id?await trials.get(id):(list[0]?await trials.get(list[0].id):null);
 currentId=selected?.id||null;
 const queue=isInspector()?await trials.queue():[];
 let body='';
 if(selected){
   body='<div class="trial-detail"><div class="section-title"><h2>'+esc(selected.name)+'</h2><span>'+esc(selected.line)+' 窑线 · 创建 '+date(selected.created_at)+'</span></div><div class="trial-facts"><span>状态 '+trialBadge(selected)+'</span><span>检验 '+selected.inspected_count+'/'+selected.unit_count+'</span><span>版本 v'+selected.version+'</span><span>复盘 '+reviewLine(selected)+'</span></div><div class="trial-actions">'+(owner?ownerActions(selected):'')+(isOwner()||isInspector()?'<a class="button" href="#/trials/'+selected.id+'/export" data-export="'+selected.id+'">导出当前视图 ↓</a>':'')+'</div>'+
     (selected.groups?groupTable(selected):blindTable(selected))+
     (selected.status==='sealed'?'<p class="warning">已封存：检验修订冻结，等待负责人揭盲。封存版本 v'+esc(selected.seal_version)+'。</p>':'')+
     (selected.revealed?revealedTable(selected):'')+
     resultView(selected)+'</div>';
 } else body='<div class="empty">先登记一个盲评试验，或在左侧选择试验。</div>';
 return heading('06 / BLIND TRIAL','工艺盲评试验','负责人按窑线登记窑次与配方；检验员只见盲码登记件数；全部检验齐全后封存，再由负责人揭盲生成两组复盘。','<button class="button" id="refresh-trials">刷新状态 ↻</button>')+'<div class="trial-grid"><aside class="trial-side"><div class="section-title"><h2>试验列表</h2><span>'+list.length+' 个</span></div>'+trialList(list,selected?.id)+(owner?registerForm():'<p class="hint">登记、锁定、封存、揭盲和终止由业务负责人执行。</p>')+'</aside><div class="trial-main">'+body+(isInspector()?inspectionQueue(queue,owner):'')+'</div></div>';
}

export function bindTrials(refresh){
 document.querySelector('#refresh-trials')?.addEventListener('click',refresh);
 document.querySelectorAll('[data-fill]').forEach(b=>b.onclick=()=>{const form=document.querySelector('#inspection-form');if(!form)return;form.blind_code.value=b.dataset.fill;const rev=Number(b.dataset.revision||0);form.revision.value=rev||'';form.revision.disabled=!rev;form.fired.focus();});
 document.querySelector('#inspection-form')&&submit(document.querySelector('#inspection-form'),async f=>{
   const payload={blind_code:f.get('blind_code').trim().toUpperCase(),fired:Number(f.get('fired')),passed:Number(f.get('passed'))};
   const revision=f.get('revision');
   if(revision!==null&&revision!=='')payload.revision=Number(revision);
   const saved=await trials.inspect(payload);
   notify('已登记盲码 '+saved.blind_code+' · 版本 v'+saved.revision+'（仅盲码与件数）');
   await refresh();
 });
 document.querySelector('#trial-form')&&submit(document.querySelector('#trial-form'),async f=>{
   const split=v=>String(v||'').split(/[\n,，、;；]+/).map(x=>x.trim()).filter(Boolean);
   const data={name:f.get('name').trim(),line:f.get('line').trim(),groups:[
     {name:f.get('group0').trim(),recipe:f.get('recipe0').trim(),batches:split(f.get('batches0'))},
     {name:f.get('group1').trim(),recipe:f.get('recipe1').trim(),batches:split(f.get('batches1'))},
   ]};
   const t=await trials.create(data);
   notify('试验已登记，窑次已占用并生成盲码');
   location.hash='#/trials/'+t.id;
 });
 document.querySelector('#lock-trial')?.addEventListener('click',async()=>{try{await trials.lock(currentId);notify('窑次已锁定，盲码已下发给检验员');await refresh();}catch(e){notify(e.message,true);}});
 document.querySelector('#terminate-trial')?.addEventListener('click',async()=>{if(!confirm('终止后释放窑次占用，记录保留。确认终止？'))return;try{await trials.terminate(currentId);notify('试验已终止，窑次占用已释放');await refresh();}catch(e){notify(e.message,true);}});
 document.querySelector('#seal-form')&&submit(document.querySelector('#seal-form'),async f=>{try{await trials.seal(currentId,Number(f.get('revision')));notify('检验记录已封存');await refresh();}catch(e){notify(e.message,true);}});
 document.querySelector('#unblind-trial')?.addEventListener('click',async()=>{if(!confirm('揭盲后配方与窑次将向负责人公开，确认揭盲？'))return;try{await trials.unblind(currentId);notify('已揭盲，后台正在生成两组复盘');await refresh();}catch(e){notify(e.message,true);}});
 document.querySelector('#retry-review')?.addEventListener('click',async()=>{try{await trials.retryReview(currentId);notify('已重新排队生成复盘');await refresh();}catch(e){notify(e.message,true);}});
 document.querySelector('#approve-review')?.addEventListener('click',async()=>{try{await trials.approveReview(currentId);notify('复盘已批准');await refresh();}catch(e){notify(e.message,true);}});
 document.querySelector('[data-export]')?.addEventListener('click',async e=>{e.preventDefault();const id=e.currentTarget.dataset.export;try{const r=await fetch('/api/trials/'+id+'/export?format=csv',{headers:{Authorization:'Bearer '+session.token}});if(!r.ok){const d=await r.json().catch(()=>({detail:'下载失败'}));throw new Error(d.detail||'下载失败');}const u=URL.createObjectURL(await r.blob()),a=document.createElement('a');a.href=u;a.download='盲评试验-'+id+'.csv';a.click();setTimeout(()=>URL.revokeObjectURL(u),1000);}catch(error){notify(error.message,true);}});
}
