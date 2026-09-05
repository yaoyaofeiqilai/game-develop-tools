const state = { sources: [], jobs: [], selectedSource: null, job: null, view: "source", selectedComponent: null, maskTool: null, lassoPoints: [], lassoDrawing: false, zoom: 1, filter: "输入队列", theme: "literary", importEntries: [], importSkipped: 0, importRunning: false, importStop: false, batchRunning: false, batchStop: false, manageMode: false, selectedLibraryItems: new Set(), collapsedLibraryGroups: new Set() };
const $ = (id) => document.getElementById(id);
const IMAGE_EXTENSIONS = new Set(["png","jpg","jpeg","webp","bmp"]);
const EXPORT_MODE_ARTIFACTS = {aligned:["aligned"],trimmed:["trimmed"],sheet:["sheet"],masks:["masks"],all:["aligned","trimmed","masks","sheet","manifest"]};

function toast(message, error = false) {
  const item = document.createElement("div"); item.className = `toast${error ? " error" : ""}`; item.textContent = message;
  $("toastStack").appendChild(item); setTimeout(() => item.remove(), 4200);
}
async function api(url, options = {}) {
  const response = await fetch(url, options); const raw = await response.text(); let data = {};
  try { data = raw ? JSON.parse(raw) : {}; } catch (_) {}
  if (!response.ok) throw new Error(data.detail || raw || `请求失败 (${response.status})`);
  return data;
}
function formatSize(bytes) { return bytes > 1048576 ? `${(bytes / 1048576).toFixed(1)} MB` : `${Math.round(bytes / 1024)} KB`; }

async function boot() {
  bindEvents();
  restorePaneLayout();
  restoreCollapsedLibraryGroups();
  try {
    const health = await api("/api/health");
    $("enginePill").innerHTML = "<i></i> 本地引擎在线";
    $("gpuPill").textContent = health.gpu_available ? "GPU / CUDA" : "CPU MODE";
    $("gpuPill").classList.toggle("muted", !health.gpu_available);
  } catch (error) { $("enginePill").textContent = "引擎离线"; toast(error.message, true); }
  try {
    const settings = await api("/api/settings");
    state.theme = settings.ui_theme || localStorage.getItem("sprite-ui-theme") || "literary";
    applyTheme(state.theme, true);
  } catch (_) {
    state.theme = localStorage.getItem("sprite-ui-theme") || "literary";
    applyTheme(state.theme, true);
  }
  await refreshAll();
}

function applyTheme(theme, persistLocal = false) {
  if (!["industrial", "dopamine", "literary"].includes(theme)) theme = "literary";
  document.documentElement.dataset.theme = theme;
  document.querySelector(`input[name="uiTheme"][value="${theme}"]`)?.setAttribute("checked", "checked");
  document.querySelectorAll('input[name="uiTheme"]').forEach(input => input.checked = input.value === theme);
  if (persistLocal) localStorage.setItem("sprite-ui-theme", theme);
}

function exportModeFromArtifacts(artifacts){
  const normalized=Array.isArray(artifacts)?[...new Set(artifacts)].sort():["aligned"];
  return Object.entries(EXPORT_MODE_ARTIFACTS).find(([,items])=>items.length===normalized.length&&[...items].sort().every((item,index)=>item===normalized[index]))?.[0]||"";
}
function fillExportProfile(prefix,profile){
  const select=$(`${prefix}ExportArtifact`),artifacts=Array.isArray(profile?.artifacts)&&profile.artifacts.length?profile.artifacts:["aligned"];
  select.querySelector('option[data-remembered="true"]')?.remove();
  $(`${prefix}ExportPath`).value=profile?.output_path||"";
  const mode=exportModeFromArtifacts(artifacts);
  if(mode)select.value=mode;
  else{const option=document.createElement("option");option.value="remembered";option.dataset.remembered="true";option.dataset.artifacts=JSON.stringify(artifacts);option.textContent=`上次选择（${artifacts.join(" + ")}）`;select.appendChild(option);select.value="remembered";}
}
function exportPayload(prefix){const path=$(`${prefix}ExportPath`).value.trim(),select=$(`${prefix}ExportArtifact`),mode=select.value,remembered=select.selectedOptions[0]?.dataset.artifacts;let artifacts=EXPORT_MODE_ARTIFACTS[mode]||["aligned"];if(remembered){try{artifacts=JSON.parse(remembered);}catch(_){}}return{output_path:path,artifacts,ai_tag:$(`${prefix}ExportAiTag`).checked};}
async function chooseExportFolder(prefix){const input=$(`${prefix}ExportPath`),button=$(`${prefix}ExportBrowse`);button.disabled=true;button.textContent="等待选择…";try{const result=await api("/api/dialogs/select-output-folder",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({initial_path:input.value.trim()})});if(result.path){input.value=result.path;toast("已选择导出文件夹");}}catch(error){toast(error.message,true);}finally{button.disabled=false;button.textContent="选择文件夹…";}}

async function refreshAll() {
  const [sourceData, jobData] = await Promise.all([api("/api/sources"), api("/api/jobs")]);
  state.sources = sourceData.sources; state.jobs = jobData.jobs; renderSources(); updateCollectionBar();
}

function normalizedPath(value){return String(value||"").replaceAll("\\","/").toLowerCase();}
function librarySortName(item){return item.relative_name||item.name||item.source_name||"";}
function naturalLibraryOrder(first,second){return librarySortName(first).localeCompare(librarySortName(second),"zh-CN",{numeric:true,sensitivity:"base"});}
function collectionSources(name){return state.sources.filter(source=>source.group==="输入队列"&&source.collection===name).sort(naturalLibraryOrder);}
function jobForSource(source){const path=normalizedPath(source?.path);return state.jobs.find(job=>normalizedPath(job.source_path).endsWith(path));}
function activeCollection(){return state.selectedSource?.collection||"";}
function libraryItemKey(item){return item.isJob?`job:${item.id}`:`source:${item.path}`;}
function allLibraryItems(){
  if(state.filter==="ai")return state.jobs.filter(job=>(job.tags||[]).includes("ai")).map(job=>({...job,group:"AI处理记录",isJob:true,isAi:true}));
  if(state.filter==="jobs")return state.jobs.map(job=>({...job,group:"历史任务",isJob:true}));
  return state.sources.filter(source=>source.group==="输入队列");
}
function itemOperationTime(item){const epoch=Number(item.modified_at_epoch);if(Number.isFinite(epoch)&&epoch>0)return epoch;const parsed=Date.parse(String(item.modified_at||item.created_at||"").replace(" ","T"));return Number.isFinite(parsed)?parsed/1000:0;}
function libraryGroupFor(item){if(item.isAi)return{key:"jobs:ai",label:"AI 处理记录",kind:"history"};if(item.isJob)return{key:"jobs:history",label:"处理历史",kind:"history"};if(item.collection)return{key:`sources:collection:${item.collection}`,label:`动画组 · ${item.collection}`,kind:"collection"};return{key:"sources:queue",label:"输入队列",kind:"queue"};}
function groupedLibraryItems(){
  const groups=new Map();
  allLibraryItems().forEach(item=>{const descriptor=libraryGroupFor(item);if(!groups.has(descriptor.key))groups.set(descriptor.key,{...descriptor,items:[],latest:0});const group=groups.get(descriptor.key);group.items.push(item);group.latest=Math.max(group.latest,itemOperationTime(item));});
  groups.forEach(group=>group.items.sort(group.kind==="collection"?naturalLibraryOrder:(first,second)=>itemOperationTime(second)-itemOperationTime(first)||naturalLibraryOrder(first,second)));
  return Array.from(groups.values()).sort((first,second)=>second.latest-first.latest||first.label.localeCompare(second.label,"zh-CN",{numeric:true}));
}
function visibleLibraryItems(){return groupedLibraryItems().filter(group=>!state.collapsedLibraryGroups.has(group.key)).flatMap(group=>group.items);}
function formatOperationTime(epoch){
  if(!epoch)return"";const date=new Date(epoch*1000),now=new Date(),diff=Math.max(0,now-date);
  if(diff<60000)return"刚刚";if(diff<3600000)return`${Math.floor(diff/60000)} 分钟前`;
  if(date.toDateString()===now.toDateString())return`今天 ${date.toLocaleTimeString("zh-CN",{hour:"2-digit",minute:"2-digit",hour12:false})}`;
  return date.toLocaleDateString("zh-CN",{month:"2-digit",day:"2-digit"})+" "+date.toLocaleTimeString("zh-CN",{hour:"2-digit",minute:"2-digit",hour12:false});
}
function restoreCollapsedLibraryGroups(){try{const saved=JSON.parse(localStorage.getItem("sprite-collapsed-library-groups")||"[]");state.collapsedLibraryGroups=new Set(Array.isArray(saved)?saved:[]);}catch(_){state.collapsedLibraryGroups=new Set();}}
function toggleLibraryGroup(key){state.collapsedLibraryGroups.has(key)?state.collapsedLibraryGroups.delete(key):state.collapsedLibraryGroups.add(key);localStorage.setItem("sprite-collapsed-library-groups",JSON.stringify(Array.from(state.collapsedLibraryGroups)));renderSources();}

function renderLibraryItem(item,list){
    const card = document.createElement("div"); card.className="source-card";card.tabIndex=state.manageMode?-1:0;card.setAttribute("role",state.manageMode?"group":"button");
    if(item.isAi)card.classList.add("ai-record");
    if ((!item.isJob && state.selectedSource?.path === item.path) || (item.isJob && state.job?.id === item.id)) card.classList.add("active");
    const key=libraryItemKey(item);if(state.selectedLibraryItems.has(key))card.classList.add("selected-for-trash");
    if (item.isJob) {
      const destination=item.last_export?.delivery_dir?` · ${escapeHtml(item.last_export.delivery_dir)}`:"";
      const preview=item.thumbnail_url||item.source_url||"",fallback=preview!==item.source_url?item.source_url||"":"";
      card.innerHTML = `<div class="thumb history-thumb">${preview?`<img src="${escapeHtml(preview)}" data-fallback="${escapeHtml(fallback)}" loading="lazy" decoding="async" alt="${escapeHtml(item.source_name)} 处理预览">`:'<span class="thumb-empty">无预览</span>'}<b class="frame-count" title="识别帧数">${item.frame_count}</b></div><div class="source-info"><strong><span>${escapeHtml(item.source_name)}</span>${item.isAi?'<em class="ai-tag">AI</em>':""}</strong><span title="${destination?escapeHtml(item.last_export.delivery_dir):""}">${item.status} · ${formatOperationTime(itemOperationTime(item))}${destination}</span></div>`;
      const image=card.querySelector(".history-thumb img");if(image)image.onerror=()=>{const next=image.dataset.fallback;if(next&&image.src!==new URL(next,location.href).href){image.dataset.fallback="";image.src=next;}else{image.remove();card.querySelector(".history-thumb")?.classList.add("missing-preview");}};
    } else {
      const position=item.collection?`${collectionSources(item.collection).findIndex(source=>source.path===item.path)+1}/${collectionSources(item.collection).length} · `:"";
      card.innerHTML = `<div class="thumb"><img src="${item.url}" loading="lazy" alt=""></div><div class="source-info"><strong>${escapeHtml(item.name)}</strong><span>${position}${item.width}×${item.height} · ${formatSize(item.size_bytes)}</span></div>`;
    }
    const openItem=()=>item.isJob?loadJob(item.id):selectSource(item);
    card.onclick=()=>state.manageMode?toggleLibrarySelection(key):openItem();
    card.onkeydown=event=>{if(!state.manageMode&&(event.key==="Enter"||event.key===" ")){event.preventDefault();openItem();}};
    if(state.manageMode){const check=document.createElement("button");check.type="button";check.className="library-check";check.setAttribute("aria-label",state.selectedLibraryItems.has(key)?"取消选择":"选择");check.innerHTML=state.selectedLibraryItems.has(key)?"✓":"";check.onclick=event=>{event.stopPropagation();toggleLibrarySelection(key);};card.appendChild(check);}
    list.appendChild(card);
}

function renderSources() {
  const list = $("sourceList"); list.innerHTML = "";
  const groups=groupedLibraryItems(),items=allLibraryItems();
  $("sourceCount").textContent = items.length;
  groups.forEach(group=>{const collapsed=state.collapsedLibraryGroups.has(group.key),label=document.createElement("button");label.type="button";label.className=`source-group${collapsed?" collapsed":""}`;label.setAttribute("aria-expanded",String(!collapsed));label.setAttribute("aria-label",`${collapsed?"展开":"折叠"}${group.label}`);label.innerHTML=`<span><i>⌄</i><strong>${escapeHtml(group.label)}</strong></span><span><small>${formatOperationTime(group.latest)}</small><b>${group.items.length}</b></span>`;label.onclick=()=>toggleLibraryGroup(group.key);list.appendChild(label);if(!collapsed)group.items.forEach(item=>renderLibraryItem(item,list));});
  if (!items.length){const empty=state.filter==="jobs"?["暂无处理历史","完成分析后会在这里留下记录"]:state.filter==="ai"?["暂无 AI 处理记录","通过 CLI 处理或导出时会自动加入这里"]:["素材库还是空的","拖入图片或文件夹即可开始"];list.innerHTML=`<div class="library-empty"><strong>${empty[0]}</strong><span>${empty[1]}</span></div>`;}
  updateLibraryManageBar();
}
function escapeHtml(value) { const div=document.createElement("div"); div.textContent=value; return div.innerHTML; }

function updateLibraryManageBar(){
  const count=state.selectedLibraryItems.size,items=visibleLibraryItems(),allSelected=items.length>0&&items.every(item=>state.selectedLibraryItems.has(libraryItemKey(item)));
  $("libraryManageBar").classList.toggle("hidden",!state.manageMode);$("manageLibraryButton").classList.toggle("active",state.manageMode);$("manageLibraryButton").textContent=state.manageMode?"完成":"管理";
  $("manageSelectionCount").textContent=`已选 ${count} 项`;$("trashSelectedButton").disabled=count===0;$("selectVisibleAssets").textContent=allSelected?"取消全选":"全选当前";
}
function toggleManageMode(){state.manageMode=!state.manageMode;state.selectedLibraryItems.clear();renderSources();}
function toggleLibrarySelection(key){state.selectedLibraryItems.has(key)?state.selectedLibraryItems.delete(key):state.selectedLibraryItems.add(key);renderSources();}
function toggleSelectVisible(){
  const items=visibleLibraryItems(),allSelected=items.length>0&&items.every(item=>state.selectedLibraryItems.has(libraryItemKey(item)));
  items.forEach(item=>{const key=libraryItemKey(item);allSelected?state.selectedLibraryItems.delete(key):state.selectedLibraryItems.add(key);});renderSources();
}
function openTrashDialog(){
  const sourceCount=Array.from(state.selectedLibraryItems).filter(key=>key.startsWith("source:")).length,jobCount=state.selectedLibraryItems.size-sourceCount;if(!sourceCount&&!jobCount)return;
  $("trashSummary").innerHTML=`<div><strong>${sourceCount}</strong><span>项素材</span></div><div><strong>${jobCount}</strong><span>条处理历史</span></div>`;$("trashRelatedJobs").checked=true;$("trashRelatedJobs").closest("label").classList.toggle("hidden",sourceCount===0);$("trashDialog").showModal();
}
function resetActiveDocument(){
  state.selectedSource=null;state.job=null;state.selectedComponent=null;state.maskTool=null;state.lassoPoints=[];state.view="source";$("documentTitle").textContent="选择一张图片开始";$("documentMeta").textContent="支持不规则排列、分离特效与尺寸变化";$("emptyStage").classList.remove("hidden");$("imageViewport").classList.add("hidden");$("resultSection").classList.add("hidden");$("assignmentCard").classList.add("hidden");$("analyzeButton").disabled=true;enableResultTabs(false);updateCollectionBar();
}
async function confirmTrashSelection(){
  const sourcePaths=[],jobIds=[];state.selectedLibraryItems.forEach(key=>{if(key.startsWith("source:"))sourcePaths.push(key.slice(7));else if(key.startsWith("job:"))jobIds.push(key.slice(4));});
  const selectedSourcePath=state.selectedSource?.path,selectedJobId=state.job?.id;$("confirmTrash").disabled=true;$("confirmTrash").textContent="正在整理…";
  try{const result=await api("/api/library/trash",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({source_paths:sourcePaths,job_ids:jobIds,include_related_jobs:$("trashRelatedJobs").checked})});$("trashDialog").close();state.selectedLibraryItems.clear();await refreshAll();const sourceGone=selectedSourcePath&&!state.sources.some(source=>source.path===selectedSourcePath),jobGone=selectedJobId&&!state.jobs.some(job=>job.id===selectedJobId);if(sourceGone||jobGone)resetActiveDocument();toast(`已移入回收站：${result.source_count} 项素材，${result.job_count} 条历史`);}
  catch(error){toast(error.message,true);}finally{$("confirmTrash").disabled=false;$("confirmTrash").textContent="移入回收站";}
}

function updateCollectionBar(){
  const name=activeCollection(),bar=$("collectionBar"),controls=$("groupControlSection"),dock=$("workflowActionDock");
  const previousButton=$("canvasPreviousSource"),nextButton=$("canvasNextSource");
  if(!name){bar.classList.add("hidden");controls.classList.add("hidden");dock.classList.remove("has-group");previousButton.classList.add("hidden");nextButton.classList.add("hidden");return;}
  const sources=collectionSources(name),index=Math.max(0,sources.findIndex(source=>source.path===state.selectedSource?.path)),jobs=sources.map(jobForSource).filter(Boolean),processed=jobs.length,review=jobs.filter(job=>job.review_count>0).length,ready=processed-review,missing=sources.length-processed;
  bar.classList.remove("hidden");$("collectionName").textContent=name;$("collectionMeta").textContent=`${index+1} / ${sources.length} · 已分析 ${processed}${review?` · 待核对 ${review}`:""}`;
  controls.classList.remove("hidden");dock.classList.add("has-group");$("groupControlName").textContent=name;$("groupControlMeta").textContent=`共 ${sources.length} 张 · 已分析 ${processed} · 可导出 ${ready}${review?` · 待核对 ${review}`:""}`;$("groupControlStatus").textContent=state.batchRunning?"处理中":missing?`待分析 ${missing}`:"分析已齐";
  previousButton.classList.remove("hidden");nextButton.classList.remove("hidden");previousButton.disabled=index<=0||state.batchRunning;nextButton.disabled=index>=sources.length-1||state.batchRunning;
  $("previousSourceName").textContent=index>0?sources[index-1].name:"已经是第一张";$("nextSourceName").textContent=index<sources.length-1?sources[index+1].name:"已经是最后一张";$("batchAnalyzeButton").disabled=state.batchRunning||sources.length<2;$("batchAnalyzeMeta").textContent=missing?`待分析 ${missing} 张`:"全部已有分析结果";
  $("batchExportButton").disabled=state.batchRunning||processed===0;$("batchExportMeta").textContent=processed===0?"需先完成分析":`${ready} 张可直接导出${review?` · ${review} 张待核对`:""}`;
  if($("groupExportResult").dataset.collection!==name)$("groupExportResult").classList.add("hidden");
}

function navigateCollection(direction){const sources=collectionSources(activeCollection()),index=sources.findIndex(source=>source.path===state.selectedSource?.path),target=sources[index+direction];if(target)selectSource(target);}

function selectSource(source) {
  const existingJob=jobForSource(source);if(existingJob){loadJob(existingJob.id);return;}
  state.selectedSource=source; state.job=null; state.selectedComponent=null; state.maskTool=null; state.lassoPoints=[]; state.view="source";
  document.querySelectorAll(".mask-tool,.lasso-tool").forEach(button=>button.classList.remove("active"));$("lassoCanvas").hidden=true;$("lassoActions").classList.add("hidden");$("localRefineOptions").classList.add("hidden");document.querySelector(".region-restore").classList.remove("refine-mode");
  $("documentTitle").textContent=source.name; $("documentMeta").textContent=`${source.width} × ${source.height} px · ${source.collection?`动画组 ${source.collection}`:source.group}`;
  $("analyzeButton").disabled=false; $("resultSection").classList.add("hidden"); $("assignmentCard").classList.add("hidden");
  $("emptyStage").classList.add("hidden"); $("imageViewport").classList.remove("hidden","pick-mode");
  setView("source"); enableResultTabs(false); renderSources(); updateCollectionBar(); resetZoom();
}

async function loadJob(jobId) {
  showBusy("正在载入分析任务", "读取实例映射与核对状态");
  try { state.job=await api(`/api/jobs/${jobId}`); const source=state.sources.find(s=>state.job.source_path.replaceAll("\\","/").endsWith(s.path)); state.selectedSource=source || {name:state.job.source_name,path:state.job.source_path,url:state.job.source_url,width:state.job.source_size[0],height:state.job.source_size[1],group:"历史任务",collection:""}; displayJob(); }
  catch(error){ toast(error.message,true); } finally { hideBusy(); }
}

function analyzeBody(source){return{ source_path:source.path, expected_frames:+$("expectedFrames").value||0, rows:+$("rows").value||0, columns:+$("columns").value||0, background_mode:$("backgroundMode").value, background_model:$("backgroundModel").value, component_sensitivity:+$("sensitivity").value/100, confidence_threshold:+$("confidence").value/100, refine_mode:$("refineMode").value, refine_tolerance:+$("refineTolerance").value, matte_width:+$("matteWidth").value };}

async function analyze() {
  if (!state.selectedSource) return;
  showBusy("正在分析整张图片", "初步抠图 → 蒙版精修 → 帧锚点 → 特效归属"); $("analyzeButton").disabled=true;
  const body=analyzeBody(state.selectedSource);
  try { state.job=await api("/api/analyze",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)}); displayJob(); toast(`完成：识别到 ${state.job.layout.frame_count} 帧`); await refreshAll(); }
  catch(error){ toast(error.message,true); }
  finally { hideBusy(); $("analyzeButton").disabled=false; }
}

function displayJob() {
  const job=state.job; state.selectedComponent=null; state.maskTool=null; state.lassoPoints=[];
  document.querySelectorAll(".mask-tool,.lasso-tool").forEach(button=>button.classList.remove("active"));$("lassoCanvas").hidden=true;$("lassoActions").classList.add("hidden");$("localRefineOptions").classList.add("hidden");document.querySelector(".region-restore").classList.remove("refine-mode");
  $("documentTitle").textContent=job.source_name; $("documentMeta").textContent=`${job.source_size[0]} × ${job.source_size[1]} px · ${job.engine.name}`;
  $("emptyStage").classList.add("hidden"); $("imageViewport").classList.remove("hidden"); $("analyzeButton").disabled=false;
  $("refineButton").disabled=false; document.querySelectorAll(".mask-tool,.lasso-tool").forEach(button=>button.disabled=false);
  if(job.config){$("refineMode").value=job.config.refine_mode||"conservative";$("refineTolerance").value=job.config.refine_tolerance??18;$("matteWidth").value=job.config.matte_width??3;updateRangeOutputs();}
  enableResultTabs(true); renderResult(); loadComponentMap(); setView("overlay"); renderSources(); updateCollectionBar();
}
function enableResultTabs(enabled){ document.querySelectorAll('#viewTabs button:not([data-view="source"])').forEach(b=>b.disabled=!enabled||!state.job?.files?.[b.dataset.view]); }

function renderResult() {
  const job=state.job, review=job.review_component_ids;
  $("resultSection").classList.remove("hidden"); $("resultCount").textContent=`${job.layout.frame_count} FRAMES`;
  $("metricFrames").textContent=job.layout.frame_count; $("metricComponents").textContent=job.components.length; $("metricReview").textContent=review.length; $("metricTime").textContent=`${job.elapsed_seconds}s`;
  $("resultStatus").textContent=review.length ? "建议核对" : "可以导出"; $("resultStatus").classList.toggle("ready",!review.length);
  $("reviewBadge").textContent=review.length; $("reviewPrompt").textContent=review.length ? "红色边缘表示归属置信度较低；点击编号可在画布定位并重新指定。" : "所有组件都已确认，可以直接导出。";
  $("frameLegend").innerHTML=job.frames.map(f=>`<button class="frame-chip" style="--chip:${f.color}"><b>F${f.id}</b><span>${Math.round(f.confidence*100)}%</span></button>`).join("");
  $("reviewList").innerHTML=review.map(id=>`<button class="review-token${state.selectedComponent===id?' active':''}" data-component="${id}">#${id}</button>`).join("");
  document.querySelectorAll(".review-token").forEach(button=>button.onclick=()=>selectComponent(+button.dataset.component));
  if (state.selectedComponent && !job.components.some(c=>c.id===state.selectedComponent)) state.selectedComponent=null;
  renderAssignment(); renderRefinement();
}
function renderRefinement(){
  const panel=$("refineStats"), data=state.job?.refinement;
  if(!data){panel.classList.add("hidden");return;}
  panel.classList.remove("hidden");
  const semantic=data.semantic_assist?"语义保护已启用":"单蒙版判断";
  const strengthLabels={gentle:"轻度",standard:"标准",strong:"强力",maximum:"极强"};
  panel.innerHTML=`<b>精修报告</b><span>清除 ${data.removed_pixels||0} px</span><span>封闭背景 ${data.enclosed_regions_removed||0} 处</span><span>${semantic}</span>${data.manual_edit_count?`<span>手动修正 ${data.manual_edit_count} 次</span>`:""}${data.last_region_restored_pixels!==undefined?`<span>最近圈选恢复 ${data.last_region_restored_pixels} px</span>`:""}${data.last_region_refined_pixels!==undefined?`<span>最近局部精修 ${data.last_region_refined_pixels} px</span><span>彻底清除 ${data.last_region_removed_pixels||0} px · ${strengthLabels[data.last_local_refine_strength]||data.last_local_refine_strength}</span>${data.last_region_protected_pixels!==undefined?`<span>前景保护 ${data.last_region_protected_pixels} px</span>`:""}`:""}`;
}
function renderAssignment(){
  const card=$("assignmentCard"); if(!state.selectedComponent||!state.job){card.classList.add("hidden");return;} card.classList.remove("hidden");
  $("selectedComponent").textContent=`#${state.selectedComponent}`; const assigned=state.job.assignments[String(state.selectedComponent)]; const anchor=state.job.frames.find(f=>f.anchor_component===state.selectedComponent);
  $("assignmentPrompt").textContent=anchor?`这是 F${anchor.id} 的主体锚点；主体不能移动或忽略。`:"将这个身体部件、武器或特效归入：";
  $("assignmentButtons").innerHTML=anchor?`<button data-frame="${anchor.id}" style="--chip:${anchor.color}">F${anchor.id} 主体</button>`:`<button class="ignore" data-frame="0">忽略</button>`+state.job.frames.map(f=>`<button data-frame="${f.id}" style="--chip:${f.color}" title="${assigned===f.id?'当前归属':''}">F${f.id}</button>`).join("");
  $("assignmentButtons").querySelectorAll("button").forEach(b=>{if(+b.dataset.frame===assigned)b.style.boxShadow="inset 0 0 0 1px var(--mint)"; b.onclick=()=>assignComponent(+b.dataset.frame);});
}
function selectComponent(id,event){ state.selectedComponent=id; renderResult(); if(event){$("selectionReadout").style.left=`${event.offsetX}px`;$("selectionReadout").style.top=`${event.offsetY}px`;} $("selectionReadout").textContent=`COMPONENT #${id}`;$("selectionReadout").classList.remove("hidden"); }

async function assignComponent(frameId){
  if(!state.selectedComponent)return; const componentId=state.selectedComponent;
  try { state.job=await api(`/api/jobs/${state.job.id}/assign`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({component_id:componentId,frame_id:frameId})});const summary=state.jobs.find(job=>job.id===state.job.id);if(summary){summary.review_count=state.job.review_component_ids.length;summary.status=state.job.status;}renderResult();updateCollectionBar();setView("overlay");toast(frameId?`组件 #${componentId} 已归入 F${frameId}`:`组件 #${componentId} 已忽略`); }
  catch(error){toast(error.message,true);}
}

async function loadComponentMap(){
  if(!state.job)return; const image=new Image(); image.src=state.job.files.component_map; await image.decode(); const canvas=$("componentCanvas"); canvas.width=image.naturalWidth;canvas.height=image.naturalHeight;canvas.getContext("2d",{willReadFrequently:true}).drawImage(image,0,0);
}
function setView(view){
  if(view!=="cutout"&&state.maskTool){state.maskTool=null;state.lassoPoints=[];document.querySelectorAll(".mask-tool,.lasso-tool").forEach(button=>button.classList.remove("active"));$("lassoCanvas").hidden=true;$("lassoActions").classList.add("hidden");$("localRefineOptions").classList.add("hidden");document.querySelector(".region-restore").classList.remove("refine-mode");}
  state.view=view; document.querySelectorAll("#viewTabs button").forEach(b=>b.classList.toggle("active",b.dataset.view===view));
  let url=state.selectedSource?.url; if(state.job&&view!=="source")url=state.job.files[view]||state.job.files.cutout; if(state.job&&view==="source")url=state.job.source_url;
  $("mainImage").src=url||""; $("imageViewport").classList.toggle("pick-mode",view==="overlay"||view==="cutout"&&!!state.maskTool); $("imageViewport").classList.toggle("mask-edit-mode",view==="cutout"&&!!state.maskTool); $("selectionReadout").classList.add("hidden");syncLassoCanvas();
}

async function canvasPick(event){
  if(state.view==="cutout"&&["background","foreground"].includes(state.maskTool)&&state.job){await editMaskAt(event);return;}
  if(state.view!=="overlay"||!state.job)return; const img=$("mainImage"),rect=img.getBoundingClientRect(); const x=Math.floor((event.clientX-rect.left)*img.naturalWidth/rect.width),y=Math.floor((event.clientY-rect.top)*img.naturalHeight/rect.height); if(x<0||y<0||x>=img.naturalWidth||y>=img.naturalHeight)return;
  const pixel=$("componentCanvas").getContext("2d",{willReadFrequently:true}).getImageData(x,y,1,1).data; const id=pixel[0]+(pixel[1]<<8)+(pixel[2]<<16); if(id)selectComponent(id,{offsetX:event.clientX-$("imageViewport").getBoundingClientRect().left,offsetY:event.clientY-$("imageViewport").getBoundingClientRect().top});
}

async function refineCurrent(){
  if(!state.job)return; $("refineButton").disabled=true; showBusy("正在精修蒙版", "检测封闭背景 → 羽化边缘 → 净化白边");
  const body={mode:$("refineMode").value,tolerance:+$("refineTolerance").value,matte_width:+$("matteWidth").value};
  try{state.job=await api(`/api/jobs/${state.job.id}/refine`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});enableResultTabs(true);renderResult();await loadComponentMap();setView("cutout");toast(`精修完成：清除 ${state.job.refinement.removed_pixels||0} 个背景像素`);}
  catch(error){toast(error.message,true);}finally{hideBusy();$("refineButton").disabled=false;}
}

function toggleMaskTool(tool){
  if(!state.job)return; state.maskTool=state.maskTool===tool?null:tool;
  state.lassoPoints=[];state.lassoDrawing=false;
  const regionTool=["restore-region","refine-region"].includes(state.maskTool);
  document.querySelectorAll(".mask-tool,.lasso-tool").forEach(button=>button.classList.toggle("active",button.dataset.tool===state.maskTool));
  $("lassoActions").classList.toggle("hidden",!regionTool);$("localRefineOptions").classList.toggle("hidden",state.maskTool!=="refine-region");$("applyLasso").disabled=true;
  document.querySelector(".region-restore").classList.toggle("refine-mode",state.maskTool==="refine-region");
  $("applyLasso").textContent=state.maskTool==="refine-region"?"应用局部精修":"应用恢复";
  if(state.maskTool){setView("cutout");$("cursorHint").textContent=state.maskTool==="background"?"点击残留背景：仅清除与点击位置颜色相近的连通区域":state.maskTool==="foreground"?"点击误删区域：恢复与点击位置颜色相近的连通区域":state.maskTool==="refine-region"?"按住鼠标圈住残留背景；圈内会重新执行原背景扣除":"按住鼠标圈住误删部分；圈内将恢复为初抠结果";}
  else{$("lassoCanvas").hidden=true;$("imageViewport").classList.remove("mask-edit-mode","pick-mode");$("cursorHint").textContent="选择“实例归属”可纠正组件；选择点选或圈选工具可精修蒙版";}
  syncLassoCanvas();
}

async function editMaskAt(event){
  const img=$("mainImage"),rect=img.getBoundingClientRect();
  const x=Math.floor((event.clientX-rect.left)*img.naturalWidth/rect.width),y=Math.floor((event.clientY-rect.top)*img.naturalHeight/rect.height);
  if(x<0||y<0||x>=img.naturalWidth||y>=img.naturalHeight)return;
  const action=state.maskTool;showBusy(action==="background"?"正在清除残留背景":"正在恢复误删前景","更新蒙版与实例归属，不重新运行 AI 模型");
  try{state.job=await api(`/api/jobs/${state.job.id}/mask-point`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({x,y,action,tolerance:+$("refineTolerance").value,feather:+$("matteWidth").value})});renderResult();await loadComponentMap();setView("cutout");state.maskTool=action;document.querySelector(`.mask-tool[data-tool="${action}"]`)?.classList.add("active");$("imageViewport").classList.add("pick-mode","mask-edit-mode");toast(action==="background"?"已清除点击区域背景":"已恢复点击区域前景");}
  catch(error){toast(error.message,true);}finally{hideBusy();}
}

function syncLassoCanvas(){
  const canvas=$("lassoCanvas"),img=$("mainImage");
  if(!img.naturalWidth||state.view!=="cutout"||!["restore-region","refine-region"].includes(state.maskTool)){canvas.hidden=true;return;}
  canvas.hidden=false;canvas.width=img.naturalWidth;canvas.height=img.naturalHeight;
  canvas.style.left=`${img.offsetLeft}px`;canvas.style.top=`${img.offsetTop}px`;canvas.style.width=`${img.clientWidth}px`;canvas.style.height=`${img.clientHeight}px`;
  drawLasso();
}
function lassoPoint(event){const canvas=$("lassoCanvas"),rect=canvas.getBoundingClientRect();return[Math.round((event.clientX-rect.left)*canvas.width/rect.width),Math.round((event.clientY-rect.top)*canvas.height/rect.height)];}
function drawLasso(){
  const canvas=$("lassoCanvas"),ctx=canvas.getContext("2d");ctx.clearRect(0,0,canvas.width,canvas.height);if(!state.lassoPoints.length)return;
  ctx.beginPath();ctx.moveTo(...state.lassoPoints[0]);state.lassoPoints.slice(1).forEach(point=>ctx.lineTo(...point));if(!state.lassoDrawing&&state.lassoPoints.length>=3)ctx.closePath();
  const refining=state.maskTool==="refine-region";ctx.lineWidth=Math.max(2,canvas.width/Math.max(canvas.clientWidth,1)*2);ctx.setLineDash([ctx.lineWidth*3,ctx.lineWidth*2]);ctx.strokeStyle=refining?"#ff6b35":"#2bd6a4";ctx.fillStyle=refining?"rgba(255,107,53,.23)":"rgba(49,91,78,.22)";if(!state.lassoDrawing&&state.lassoPoints.length>=3)ctx.fill();ctx.stroke();
}
function beginLasso(event){if(!["restore-region","refine-region"].includes(state.maskTool))return;event.preventDefault();const canvas=$("lassoCanvas");canvas.setPointerCapture(event.pointerId);state.lassoPoints=[lassoPoint(event)];state.lassoDrawing=true;$("applyLasso").disabled=true;drawLasso();}
function moveLasso(event){if(!state.lassoDrawing)return;event.preventDefault();const point=lassoPoint(event),last=state.lassoPoints.at(-1);if(Math.hypot(point[0]-last[0],point[1]-last[1])>=3){state.lassoPoints.push(point);drawLasso();}}
function endLasso(event){if(!state.lassoDrawing)return;event.preventDefault();state.lassoDrawing=false;if(state.lassoPoints.length>=3){drawLasso();$("applyLasso").disabled=false;$("cursorHint").textContent=state.maskTool==="refine-region"?"圈选完成：选择精修程度后应用，或重新圈选":"圈选完成：确认范围后应用恢复，或重新圈选";}else{clearLasso();}}
function clearLasso(){state.lassoPoints=[];state.lassoDrawing=false;$("applyLasso").disabled=true;drawLasso();$("cursorHint").textContent=state.maskTool==="refine-region"?"按住鼠标圈住残留背景；圈内会重新执行原背景扣除":"按住鼠标圈住误删部分；圈内将恢复为初抠结果";}
async function applyRegionEdit(){
  if(!state.job||state.lassoPoints.length<3)return;const points=state.lassoPoints.map(point=>[Math.round(point[0]),Math.round(point[1])]),refining=state.maskTool==="refine-region";showBusy(refining?"正在局部重新抠图":"正在恢复圈选区域",refining?"裁取原图上下文 → 原背景扣除 → 局部强度精修 → 圈内融合":"读取初抠蒙版 → 局部羽化融合 → 更新实例归属");$("applyLasso").disabled=true;
  const endpoint=refining?"refine-region":"mask-region",body=refining?{points,strength:$("localRefineStrength").value,tolerance:+$("refineTolerance").value,matte_width:+$("matteWidth").value,feather:Math.max(2,+$("matteWidth").value+1)}:{points,feather:Math.max(2,+$("matteWidth").value+1)};
  try{state.job=await api(`/api/jobs/${state.job.id}/${endpoint}`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});renderResult();await loadComponentMap();setView("cutout");const tool=refining?"refine-region":"restore-region";state.maskTool=tool;document.querySelector(`.lasso-tool[data-tool="${tool}"]`)?.classList.add("active");$("lassoActions").classList.remove("hidden");$("localRefineOptions").classList.toggle("hidden",!refining);document.querySelector(".region-restore").classList.toggle("refine-mode",refining);clearLasso();if(refining){const refined=state.job.refinement?.last_region_refined_pixels??0,removed=state.job.refinement?.last_region_removed_pixels??0,protectedPixels=state.job.refinement?.last_region_protected_pixels??0;toast(`局部精修完成：清理 ${refined} px，保护前景 ${protectedPixels} px`);}else{const restored=state.job.refinement?.last_region_restored_pixels??0;toast(`圈选区域已从初抠图恢复 ${restored} 个像素`);}}
  catch(error){toast(error.message,true);$("applyLasso").disabled=false;}finally{hideBusy();}
}

function openSingleExportDialog(){if(!state.job)return;const profile=state.job.export_profile;fillExportProfile("single",profile);$("singleExportAiTag").checked=(state.job.tags||[]).includes("ai");$("singleExportIntro").textContent=profile?.remembered?`已载入“${state.job.source_name}”上次使用的导出路径和内容；本次修改只会更新这张素材。`:`“${state.job.source_name}”尚无导出记录，默认导出 frames_aligned。`;$("singleExportDialog").showModal();}

async function exportJob(){
  if(!state.job)return;const jobId=state.job.id,payload=exportPayload("single");$("confirmSingleExport").disabled=true;$("confirmSingleExport").textContent="正在导出…";
  try {const result=await api(`/api/jobs/${jobId}/export`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});state.job.export_profile=result.export_profile||state.job.export_profile;$("singleExportDialog").close();const delivery=result.delivery_dir||result.output_dir,panel=$("exportResult");panel.classList.remove("hidden");panel.innerHTML=`<div class="export-summary"><strong>已导出 ${result.frame_count} 帧 · ${result.artifacts.join(" + ")}</strong><div class="export-actions"><button type="button" id="copyOutputPath">复制路径</button><button type="button" class="open-output" id="openOutputFolder">打开文件夹 <i>↗</i></button></div></div><code>${escapeHtml(delivery)}</code>`;$("copyOutputPath").onclick=async()=>{try{await navigator.clipboard.writeText(delivery);$("copyOutputPath").textContent="已复制";toast("输出路径已复制");}catch(_){toast("复制失败，请手动选择路径",true);}};$("openOutputFolder").onclick=async()=>{const button=$("openOutputFolder");button.disabled=true;try{await api(`/api/jobs/${jobId}/open-output`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({output_path:delivery})});toast("已打开本次导出文件夹");}catch(error){toast(error.message,true);}finally{button.disabled=false;}};await refreshAll();toast(`导出完成：${result.artifacts.join("、")}`);}
  catch(error){toast(error.message,true);}finally{$("confirmSingleExport").disabled=false;$("confirmSingleExport").textContent="开始导出";}
}

function isImageFile(file){const extension=file.name.split(".").pop()?.toLowerCase();return file.type.startsWith("image/")&&IMAGE_EXTENSIONS.has(extension)||IMAGE_EXTENSIONS.has(extension);}
function fileDescriptor(file,relativePath=""){return{file,relativePath:(relativePath||file.webkitRelativePath||file.name).replaceAll("\\","/").replace(/^\/+/,"")};}
function cleanCollectionName(value){return value.replace(/[<>:"/\\|?*\x00-\x1f]/g,"_").replace(/[. ]+$/g,"").trim().slice(0,80)||"animation-set";}

function prepareFolderImport(entries){
  const valid=entries.filter(entry=>isImageFile(entry.file));if(!valid.length){toast("文件夹中没有可导入的图片",true);return;}
  state.importEntries=valid;state.importSkipped=Math.max(0,entries.length-valid.length);state.importStop=false;
  const firstParts=valid[0].relativePath.split("/"),root=firstParts.length>1?firstParts[0]:"animation-set";
  const folders=new Set(valid.map(entry=>entry.relativePath.split("/").slice(1,-1).join("/")).filter(Boolean));
  $("importCollectionName").value=cleanCollectionName(root);$("importImageCount").textContent=valid.length;$("importFolderCount").textContent=folders.size;$("importTotalSize").textContent=formatSize(valid.reduce((total,entry)=>total+entry.file.size,0));$("importSkippedCount").textContent=state.importSkipped;
  $("importPreview").innerHTML=valid.slice(0,60).map(entry=>`<div class="import-preview-row"><i>◆</i><span title="${escapeHtml(entry.relativePath)}">${escapeHtml(entry.relativePath)}</span><small>${formatSize(entry.file.size)}</small></div>`).join("")+(valid.length>60?`<div class="import-preview-row"><i>…</i><span>还有 ${valid.length-60} 张图片</span><small>将在确认后全部导入</small></div>`:"");
  $("importProgress").classList.add("hidden");$("confirmFolderImport").disabled=false;$("cancelFolderImport").disabled=false;$("cancelFolderImport").textContent="取消";$("folderImportDialog").querySelector(".close-button").disabled=false;$("folderImportDialog").showModal();
}

async function uploadEntries(entries,collectionName="",showProgress=false){
  const imported=[],errors=[];state.importStop=false;
  for(let index=0;index<entries.length;index++){
    if(state.importStop)break;const entry=entries[index],parts=entry.relativePath.split("/").filter(Boolean),rest=parts.length>1?parts.slice(1):[entry.file.name],relativePath=collectionName?[cleanCollectionName(collectionName),...rest].join("/"):entry.file.name;
    if(showProgress){$("importProgressText").textContent=`正在复制 ${entry.file.name}`;$("importProgressValue").textContent=`${index+1} / ${entries.length}`;$("importProgressBar").max=entries.length;$("importProgressBar").value=index;}
    const data=new FormData();data.append("file",entry.file);data.append("relative_path",relativePath);
    try{imported.push(await api("/api/upload",{method:"POST",body:data}));}catch(error){errors.push(`${entry.file.name}: ${error.message}`);}
  }
  if(showProgress)$("importProgressBar").value=imported.length+errors.length;
  await refreshAll();state.filter="输入队列";document.querySelectorAll("#sourceTabs button").forEach(button=>button.classList.toggle("active",button.dataset.filter===state.filter));renderSources();
  const first=state.sources.find(source=>source.path===imported[0]?.path);if(first)selectSource(first);
  if(errors.length)toast(`已导入 ${imported.length} 张，${errors.length} 张失败：${errors[0]}`,true);else if(imported.length)toast(`已导入 ${imported.length} 张图片${collectionName?`，动画组：${cleanCollectionName(collectionName)}`:""}`);
  return{imported,errors};
}

async function confirmFolderImport(){
  const name=cleanCollectionName($("importCollectionName").value);$("importCollectionName").value=name;$("confirmFolderImport").disabled=true;$("importProgress").classList.remove("hidden");$("cancelFolderImport").textContent="完成当前后停止";$("folderImportDialog").querySelector(".close-button").disabled=true;state.importRunning=true;
  try{await uploadEntries(state.importEntries,name,true);}finally{state.importRunning=false;$("folderImportDialog").querySelector(".close-button").disabled=false;$("folderImportDialog").close();state.importEntries=[];}
}

async function readDirectoryEntry(entry){
  if(entry.isFile)return new Promise((resolve,reject)=>entry.file(file=>resolve([fileDescriptor(file,entry.fullPath)]),reject));
  if(!entry.isDirectory)return[];const reader=entry.createReader(),children=[];
  while(true){const batch=await new Promise((resolve,reject)=>reader.readEntries(resolve,reject));if(!batch.length)break;children.push(...batch);}
  const nested=await Promise.all(children.map(readDirectoryEntry));return nested.flat();
}

async function handleDrop(dataTransfer){
  const roots=Array.from(dataTransfer.items||[]).map(item=>item.webkitGetAsEntry?.()).filter(Boolean);
  if(roots.some(entry=>entry.isDirectory)){const nested=await Promise.all(roots.map(readDirectoryEntry));prepareFolderImport(nested.flat());return;}
  await handleChosenFiles(Array.from(dataTransfer.files||[]));
}

async function handleChosenFiles(fileList){
  const files=Array.from(fileList||[]).filter(isImageFile);
  if(!files.length){toast("没有找到可导入的图片",true);return;}
  const entries=files.map(file=>fileDescriptor(file));
  if(entries.length===1)await uploadEntries(entries);
  else prepareFolderImport(entries);
}

function bindImportDropTarget(target){
  let dragDepth=0;
  target.addEventListener("dragenter",event=>{if(!Array.from(event.dataTransfer?.types||[]).includes("Files"))return;event.preventDefault();dragDepth++;target.classList.add("import-dragging");if(target===$("dropZone"))target.classList.add("dragging");});
  target.addEventListener("dragover",event=>{if(!Array.from(event.dataTransfer?.types||[]).includes("Files"))return;event.preventDefault();event.dataTransfer.dropEffect="copy";});
  target.addEventListener("dragleave",event=>{if(!Array.from(event.dataTransfer?.types||[]).includes("Files"))return;dragDepth=Math.max(0,dragDepth-1);if(!dragDepth){target.classList.remove("import-dragging","dragging");}});
  target.addEventListener("drop",async event=>{if(!Array.from(event.dataTransfer?.types||[]).includes("Files"))return;event.preventDefault();event.stopPropagation();dragDepth=0;target.classList.remove("import-dragging","dragging");if(state.importRunning||state.batchRunning){toast("当前任务完成后再导入新素材",true);return;}try{await handleDrop(event.dataTransfer);}catch(error){toast(`读取素材失败：${error.message}`,true);}});
}

function openBatchDialog(){
  const name=activeCollection(),sources=collectionSources(name);if(!name||sources.length<2)return;const processed=sources.filter(jobForSource).length,expected=+$("expectedFrames").value||0;
  $("batchDialogIntro").textContent=`“${name}”中的图片将按当前右侧参数逐张处理。处理期间可以要求在完成当前图片后停止。`;
  $("batchPlan").innerHTML=`共 <b>${sources.length}</b> 张图片 · 已有任务 <b>${processed}</b> 张<br>帧数提示：<b>${expected||"自动判断"}</b> · 背景：<b>${$("backgroundMode").selectedOptions[0].textContent}</b> · 精修：<b>${$("refineMode").selectedOptions[0].textContent}</b>`;
  $("batchDialog").showModal();
}

async function openGroupExportDialog(){
  const name=activeCollection(),sources=collectionSources(name);if(!name||!sources.length)return;
  const jobs=sources.map(jobForSource).filter(Boolean),review=jobs.filter(job=>job.review_count>0).length,ready=jobs.length-review,missing=sources.length-jobs.length;
  let profile=null;try{profile=await api(`/api/collections/${encodeURIComponent(name)}/export-profile`);}catch(error){toast(`读取整组导出记录失败：${error.message}`,true);}
  fillExportProfile("group",profile);$("groupExportAiTag").checked=jobs.some(job=>(job.tags||[]).includes("ai"));
  $("groupExportIntro").textContent=profile?.remembered?`已载入“${name}”上次使用的整组导出路径和内容。`:`“${name}”将使用每张素材最近一次分析及局部修正后的结果导出。`;
  $("groupExportPlan").innerHTML=`共 <b>${sources.length}</b> 张素材 · 已分析 <b>${jobs.length}</b> 张<br>可直接导出 <b>${ready}</b> 张${review?` · 待核对 <b>${review}</b> 张`:""}${missing?` · 未分析 <b>${missing}</b> 张`:""}`;
  $("includeReviewRow").classList.toggle("hidden",review===0);$("includeReviewJobs").checked=false;
  $("groupExportNote").textContent=missing?`还有 ${missing} 张未分析，本次只导出已有结果。可取消后先运行“整组分析”。`:review?"默认跳过待核对结果；如果你已目视确认，也可以勾选一并导出。":"全部分析结果均可直接导出。";
  $("confirmGroupExport").textContent=review||missing?`导出可用结果（${ready} 张）`:`导出全部 ${ready} 张`;
  $("confirmGroupExport").disabled=ready===0;$("groupExportDialog").showModal();
}

async function startGroupExport(){
  const name=activeCollection();if(!name)return;const includeReview=$("includeReviewJobs").checked,payload={collection:name,include_review:includeReview,...exportPayload("group")};$("groupExportDialog").close();state.batchRunning=true;showBusy("正在导出整组动画",`${name} → ${payload.artifacts.join(" + ")}`);updateCollectionBar();
  try{const result=await api("/api/collections/export",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});const panel=$("groupExportResult"),partial=result.missing_count||result.skipped_review_count||result.failed_count;panel.dataset.collection=name;panel.dataset.outputPath=result.output_dir;panel.classList.remove("hidden");panel.innerHTML=`<div><strong>${partial?"整组导出已完成可用部分":"整组导出完成"}</strong><span>成功 ${result.exported_count}/${result.source_count}${result.skipped_review_count?` · 待核对 ${result.skipped_review_count}`:""}${result.missing_count?` · 未分析 ${result.missing_count}`:""}${result.failed_count?` · 失败 ${result.failed_count}`:""}<br>${result.exported[0]?.artifacts?.join(" + ")||"aligned"} · 集中预览：result 文件夹</span></div><button type="button" id="openGroupOutput">打开 result 预览 ↗</button>`;$("openGroupOutput").onclick=openGroupOutput;await refreshAll();toast(partial?`已导出 ${result.exported_count} 张；可在 result 文件夹集中预览`:`整组 ${result.exported_count} 张已导出到指定位置`,Boolean(result.failed_count));}
  catch(error){toast(error.message,true);}finally{state.batchRunning=false;hideBusy();updateCollectionBar();}
}

async function openGroupOutput(){
  const name=activeCollection();if(!name)return;const button=$("openGroupOutput"),outputPath=$("groupExportResult").dataset.outputPath||"";if(button)button.disabled=true;
  try{await api("/api/collections/open-output",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({collection:name,view:"result",output_path:outputPath})});toast("已打开 result 集中预览文件夹");}catch(error){toast(error.message,true);}finally{if(button)button.disabled=false;}
}

async function startBatchAnalysis(){
  const name=activeCollection(),sources=collectionSources(name),skip=$("skipExistingJobs").checked,autoExport=$("exportReadyJobs").checked;if(!sources.length)return;
  $("batchDialog").close();state.batchRunning=true;state.batchStop=false;$("batchProgress").classList.remove("hidden");$("batchProgressBar").max=sources.length;$("batchProgressBar").value=0;$("stopBatchButton").disabled=false;$("stopBatchButton").textContent="完成当前后停止";$("analyzeButton").disabled=true;updateCollectionBar();
  let completed=0,skipped=0,failed=0,exported=0,lastJob=null,firstReview=null;
  for(let index=0;index<sources.length;index++){
    if(state.batchStop)break;const source=sources[index],existing=jobForSource(source);$("batchProgressText").textContent=`${source.name}`;$("batchProgressValue").textContent=`${index+1} / ${sources.length}`;showBusy(`整组分析 ${index+1} / ${sources.length}`,`${name} → ${source.name}`);
    if(skip&&existing){skipped++;$("batchProgressBar").value=index+1;continue;}
    try{const job=await api("/api/analyze",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(analyzeBody(source))});lastJob=job;completed++;if(job.review_component_ids?.length&&!firstReview)firstReview=job;if(autoExport&&!job.review_component_ids?.length){await api(`/api/jobs/${job.id}/export`,{method:"POST"});exported++;}}
    catch(error){failed++;toast(`${source.name}：${error.message}`,true);}$("batchProgressBar").value=index+1;
  }
  hideBusy();state.batchRunning=false;$("analyzeButton").disabled=!state.selectedSource;$("batchProgress").classList.add("hidden");await refreshAll();updateCollectionBar();
  const focusJob=firstReview||lastJob;if(focusJob)await loadJob(focusJob.id);
  const stopped=state.batchStop?"，已按要求停止":"";toast(`整组完成：新分析 ${completed}，跳过 ${skipped}，失败 ${failed}${autoExport?`，导出 ${exported}`:""}${stopped}`,failed>0);
}
function showBusy(title,detail){$("busyTitle").textContent=title;$("busyDetail").textContent=detail;$("busyLayer").classList.remove("hidden");} function hideBusy(){$("busyLayer").classList.add("hidden");}
function resetZoom(){state.zoom=1;applyZoom();} function applyZoom(){$("imageViewport").style.transform=`scale(${state.zoom})`;$("zoomValue").textContent=`${Math.round(state.zoom*100)}%`;}

function updateRangeOutputs(){
  $("sensitivityValue").textContent=$("sensitivity").value;$("confidenceValue").textContent=$("confidence").value;
  $("refineToleranceValue").textContent=$("refineTolerance").value;$("matteWidthValue").textContent=$("matteWidth").value;
}

function paneDefaults(){return document.documentElement.dataset.theme==="literary"?{source:272,inspector:360}:{source:286,inspector:348};}
function restorePaneLayout(){
  let saved=null;try{saved=JSON.parse(localStorage.getItem("sprite-pane-layout"));}catch(_){}
  const sizes=saved||paneDefaults();setPaneWidths(sizes.source,sizes.inspector,false);
}
function setPaneWidths(sourceWidth,inspectorWidth,persist=true){
  const shell=document.querySelector(".app-shell"),total=shell.getBoundingClientRect().width||window.innerWidth;
  const source=Math.round(Math.max(190,Math.min(480,sourceWidth)));
  const inspector=Math.round(Math.max(280,Math.min(560,inspectorWidth)));
  const centreMinimum=380,resizers=12;
  let safeSource=source,safeInspector=inspector;
  if(safeSource+safeInspector+centreMinimum+resizers>total){
    const overflow=safeSource+safeInspector+centreMinimum+resizers-total;
    const sourceRoom=Math.max(0,safeSource-190),inspectorRoom=Math.max(0,safeInspector-280),room=sourceRoom+inspectorRoom;
    if(room>0){safeSource-=overflow*(sourceRoom/room);safeInspector-=overflow*(inspectorRoom/room);}
  }
  safeSource=Math.max(190,Math.round(safeSource));safeInspector=Math.max(280,Math.round(safeInspector));
  shell.style.setProperty("--source-width",`${safeSource}px`);shell.style.setProperty("--inspector-width",`${safeInspector}px`);
  if(persist)localStorage.setItem("sprite-pane-layout",JSON.stringify({source:safeSource,inspector:safeInspector}));
}
function currentPaneWidths(){const shell=document.querySelector(".app-shell"),style=getComputedStyle(shell);return{source:parseFloat(style.getPropertyValue("--source-width"))||272,inspector:parseFloat(style.getPropertyValue("--inspector-width"))||360};}
function bindPaneResizer(element,side){
  element.addEventListener("pointerdown",event=>{if(window.innerWidth<=860)return;event.preventDefault();element.setPointerCapture(event.pointerId);document.body.classList.add("resizing-panes");
    const shell=document.querySelector(".app-shell"),bounds=shell.getBoundingClientRect(),start=currentPaneWidths();
    const move=moveEvent=>{if(side==="left")setPaneWidths(moveEvent.clientX-bounds.left,start.inspector,false);else setPaneWidths(start.source,bounds.right-moveEvent.clientX,false);};
    const end=()=>{element.removeEventListener("pointermove",move);document.body.classList.remove("resizing-panes");const sizes=currentPaneWidths();setPaneWidths(sizes.source,sizes.inspector,true);};
    element.addEventListener("pointermove",move);element.addEventListener("pointerup",end,{once:true});element.addEventListener("pointercancel",end,{once:true});
  });
  element.addEventListener("dblclick",()=>{const defaults=paneDefaults();setPaneWidths(defaults.source,defaults.inspector,true);toast("面板宽度已恢复默认");});
  element.addEventListener("keydown",event=>{if(!["ArrowLeft","ArrowRight"].includes(event.key))return;event.preventDefault();const sizes=currentPaneWidths(),step=event.shiftKey?24:8,direction=event.key==="ArrowRight"?1:-1;if(side==="left")setPaneWidths(sizes.source+direction*step,sizes.inspector);else setPaneWidths(sizes.source,sizes.inspector-direction*step);});
}

async function openSettings(){
  try{const s=await api("/api/settings");state.theme=s.ui_theme||state.theme;applyTheme(state.theme);$("httpProxy").value=s.http_proxy||"";$("httpsProxy").value=s.https_proxy||"";$("visionBase").value=s.vision_base_url||"";$("visionModel").value=s.vision_model||"";$("visionKey").value="";$("visionKey").placeholder=s.vision_api_key_set?"已保存，留空则保留":"留空则不启用";$("settingsDialog").showModal();}catch(error){toast(error.message,true);}
}
async function saveSettings(){
  const current=await api("/api/settings");const selectedTheme=document.querySelector('input[name="uiTheme"]:checked')?.value||"literary";const body={ui_theme:selectedTheme,http_proxy:$("httpProxy").value.trim(),https_proxy:$("httpsProxy").value.trim(),vision_base_url:$("visionBase").value.trim(),vision_model:$("visionModel").value.trim(),vision_api_key:$("visionKey").value|| (current.vision_api_key_set?"__KEEP__":"")};
  try{await api("/api/settings",{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});state.theme=selectedTheme;applyTheme(state.theme,true);$("settingsDialog").close();toast("设置与界面风格已保存");}catch(error){toast(error.message,true);}
}

function bindEvents(){
  $("refreshButton").onclick=refreshAll; $("settingsButton").onclick=openSettings; $("saveSettings").onclick=saveSettings; $("analyzeButton").onclick=analyze; $("refineButton").onclick=refineCurrent; $("exportButton").onclick=openSingleExportDialog;$("confirmSingleExport").onclick=exportJob;
  $("singleExportBrowse").onclick=()=>chooseExportFolder("single");$("groupExportBrowse").onclick=()=>chooseExportFolder("group");
  $("sourceTabs").onclick=e=>{if(!e.target.dataset.filter)return;state.filter=e.target.dataset.filter;document.querySelectorAll("#sourceTabs button").forEach(b=>b.classList.toggle("active",b===e.target));renderSources();};
  $("manageLibraryButton").onclick=toggleManageMode;$("selectVisibleAssets").onclick=toggleSelectVisible;$("trashSelectedButton").onclick=openTrashDialog;$("confirmTrash").onclick=confirmTrashSelection;
  $("viewTabs").onclick=e=>{if(e.target.dataset.view&&!e.target.disabled)setView(e.target.dataset.view);}; $("mainImage").onclick=canvasPick;
  ["sensitivity","confidence","refineTolerance","matteWidth"].forEach(id=>$(id).oninput=updateRangeOutputs);
  document.querySelectorAll(".mask-tool").forEach(button=>button.onclick=()=>toggleMaskTool(button.dataset.tool));
  $("lassoTool").onclick=()=>toggleMaskTool("restore-region");$("localRefineTool").onclick=()=>toggleMaskTool("refine-region");$("cancelLasso").onclick=clearLasso;$("applyLasso").onclick=applyRegionEdit;
  $("lassoCanvas").addEventListener("pointerdown",beginLasso);$("lassoCanvas").addEventListener("pointermove",moveLasso);$("lassoCanvas").addEventListener("pointerup",endLasso);$("lassoCanvas").addEventListener("pointercancel",endLasso);
  $("mainImage").addEventListener("load",syncLassoCanvas);if(window.ResizeObserver)new ResizeObserver(syncLassoCanvas).observe($("mainImage"));
  $("zoomIn").onclick=()=>{state.zoom=Math.min(3,state.zoom+.1);applyZoom();};$("zoomOut").onclick=()=>{state.zoom=Math.max(.25,state.zoom-.1);applyZoom();};$("zoomFit").onclick=resetZoom;
  $("smartImportButton").onclick=()=>$("fileInput").click();
  $("fileInput").onchange=async e=>{const files=Array.from(e.target.files||[]);e.target.value="";await handleChosenFiles(files);};
  bindImportDropTarget($("dropZone"));bindImportDropTarget($("workspacePanel"));
  $("confirmFolderImport").onclick=confirmFolderImport;$("cancelFolderImport").onclick=()=>{if(state.importRunning){state.importStop=true;$("cancelFolderImport").disabled=true;$("cancelFolderImport").textContent="将在当前文件后停止";}else $("folderImportDialog").close();};
  $("folderImportDialog").addEventListener("cancel",event=>{if(state.importRunning){event.preventDefault();state.importStop=true;}});
  $("canvasPreviousSource").onclick=()=>navigateCollection(-1);$("canvasNextSource").onclick=()=>navigateCollection(1);$("batchAnalyzeButton").onclick=openBatchDialog;$("batchExportButton").onclick=openGroupExportDialog;$("confirmBatchAnalysis").onclick=startBatchAnalysis;$("confirmGroupExport").onclick=startGroupExport;$("includeReviewJobs").onchange=()=>{const jobs=collectionSources(activeCollection()).map(jobForSource).filter(Boolean),ready=jobs.filter(job=>!job.review_count).length,include=$("includeReviewJobs").checked;$("confirmGroupExport").textContent=include?`导出全部已分析（${jobs.length} 张）`:`导出可用结果（${ready} 张）`;$("confirmGroupExport").disabled=!include&&ready===0;};$("stopBatchButton").onclick=()=>{state.batchStop=true;$("stopBatchButton").disabled=true;$("stopBatchButton").textContent="将在当前图片后停止";};
  $("themePicker").onchange=e=>{if(e.target.name==="uiTheme")applyTheme(e.target.value);};
  $("settingsDialog").addEventListener("close",()=>{if($("settingsDialog").returnValue==="cancel")applyTheme(state.theme);});
  document.addEventListener("dragover",event=>{if(Array.from(event.dataTransfer?.types||[]).includes("Files"))event.preventDefault();});
  document.addEventListener("drop",event=>{if(Array.from(event.dataTransfer?.types||[]).includes("Files"))event.preventDefault();});
  document.addEventListener("keydown",e=>{if((e.ctrlKey||e.metaKey)&&e.key==="Enter"&&!$("analyzeButton").disabled)analyze();const typing=/^(INPUT|SELECT|TEXTAREA|BUTTON)$/.test(e.target.tagName),dialogOpen=document.querySelector("dialog[open]");if(!typing&&!dialogOpen&&!state.maskTool&&!state.batchRunning&&(e.key==="ArrowLeft"||e.key==="ArrowRight")){e.preventDefault();navigateCollection(e.key==="ArrowLeft"?-1:1);}});
  bindPaneResizer($("leftResizer"),"left");bindPaneResizer($("rightResizer"),"right");
  window.addEventListener("resize",()=>{const sizes=currentPaneWidths();setPaneWidths(sizes.source,sizes.inspector,false);});
}
boot();
