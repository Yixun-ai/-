import {validateProject, buildProject, fileKey, reviewRows, mergeImages, MAX_BYTES} from './project.mjs';

const $ = id => document.getElementById(id);
const names = {EMPTY_SKU:'没有产品编号',SKU_WHITESPACE:'编号带有空格',SKU_CONTROL_CHARACTER:'编号中有异常字符',SKU_NOT_TEXT:'编号格式需要修改',DUPLICATE_SKU:'产品编号重复',DUPLICATE_SKU_CONFLICT:'同一编号对应不同商品',REPEATED_SKU_ROWS:'同一商品的多条记录',SKU_CASE_COLLISION:'编号容易混淆',AMBIGUOUS_MATCH:'图片对应不明确',INVALID_IMAGE:'图片无法读取',MISSING_IMAGE:'产品缺少图片',UNSUPPORTED_FILE:'有文件未被处理',UNMATCHED_IMAGE:'图片没有对应产品',SMALL_IMAGE:'图片尺寸不足'};
let batch = {table:null, images:[]}, assignments = {}, report = null, busy = false;
let activeTab = 'issues', reviewPage = 0, editingSource = null, dirty = false;
let reviewPages = 1;
let previewUrls = new Map();
const PAGE_SIZE = 24;

function node(tag, text, className) {
  const out=document.createElement(tag);
  if(text!==undefined)out.textContent=text;
  if(className)out.className=className;
  return out;
}
function message(text, state='') {$('status').textContent=text;$('status').className=state;}
function markDirty() {dirty=true;$('draftStatus').textContent='进度未保存';}
function clearPreviews() {for(const value of previewUrls.values())URL.revokeObjectURL(value);previewUrls.clear();}
function controls() {
  document.querySelectorAll('#form input,#form select,#form button,.project-actions button,#results button,#reviewToolbar input,#reviewToolbar select,#assignDialog input,#assignDialog button').forEach(x=>x.disabled=busy);
  $('allow').disabled=busy;
  $('pack').disabled=busy||!report||report.summary.errors>0||(report.summary.warnings>0&&!$('allow').checked);
  $('saveProject').disabled=busy||!batch.table;
  $('checklist').disabled=busy||!report||!report.issues.length;
  $('previous').disabled=busy||reviewPage===0;$('next').disabled=busy||reviewPage>=reviewPages-1;
  $('packNote').textContent=busy?'正在处理，请稍候。':!report?'检查完成后，这里可以下载。':report.summary.errors?'先处理需要修改的地方，再检查一次。':report.summary.warnings&&!$('allow').checked?'查看提醒并确认后，可以打包。':'原图、原表、对应记录会一起打包。';
}
function fileInfo() {
  $('tableMeta').textContent=batch.table?`${batch.table.name} · ${(batch.table.size/1024).toFixed(1)} KB`:'还没有选择';
  const total=batch.images.reduce((n,x)=>n+x.file.size,0);
  $('imageMeta').textContent=batch.images.length?`${batch.images.length} 个文件 · ${(total/1024/1024).toFixed(2)} MB`:'还没有选择';
  $('selected').textContent='一次处理最多 50 MB。补选同名图片时，唯一同名文件会在本批次中替换，原件不改动。';
  $('matchExample').textContent=$('mode').value==='exact'?'例如：001.png 对应产品 001':'例如：001_主图.png；多个编号命中时需人工确认。';
}
function invalidate() {
  report=null;$('results').hidden=true;$('warningRow').hidden=true;$('allow').checked=false;
  for(const id of ['productCount','matchedCount','problemCount'])$(id).textContent='—';
  reviewPage=0;controls();
}
function base64(file) {
  return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result.split(',')[1]);reader.onerror=()=>reject(new Error('读取失败：'+file.name));reader.readAsDataURL(file);});
}
function options() {
  const result={match_mode:$('mode').value,case_sensitive:!$('ignoreCase').checked,min_width:Number($('width').value),min_height:Number($('height').value),duplicate_policy:$('duplicates').value,assignments};
  if($('column').value)result.sku_column=$('column').value;
  if($('sheet').value)result.sheet=$('sheet').value;
  return result;
}
async function payload() {
  if(!batch.table)throw new Error('请先选择产品表。');
  if(batch.images.length>5000)throw new Error('单次最多处理 5,000 个文件。');
  if(batch.table.size+batch.images.reduce((n,x)=>n+x.file.size,0)>MAX_BYTES)throw new Error('资料超过 50 MB，请分批处理。');
  const images=[];
  for(const image of batch.images)images.push({name:image.name,content:await base64(image.file)});
  return {table:{name:batch.table.name,content:await base64(batch.table)},images,options:options(),allow_warnings:$('allow').checked};
}
async function request(action, data) {
  let response;
  try {response=await fetch('/api/'+action,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});}
  catch {throw new Error('连接本机服务失败，请确认启动窗口仍在运行，再重试。');}
  if(response.headers.get('Content-Type')?.includes('application/json')) {
    const value=await response.json();if(value.error)throw new Error(value.error);return {report:value};
  }
  if(!response.ok)throw new Error('处理未完成，请重试。');
  return {blob:await response.blob()};
}
function download(blob, name) {
  const url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(url),30000);
}
function issueText(issue) {
  if(issue.code==='UNMATCHED_IMAGE')return report.assignments?.[issue.subject]?.sku===null?'你已选择暂不归类。确认提醒后，这张图会单独放入资料包。':'文件名没有对应编号。可以看图确认归属，或单独存放。';
  if(issue.code==='INVALID_IMAGE'&&issue.message.includes('无法完整解码'))return '图片无法完整打开，请检查文件是否损坏，或补选一张可用的图片。';
  return issue.message;
}
function renderIssues() {
  $('issues').replaceChildren();
  const issues=[...report.issues].sort((a,b)=>(a.severity==='error'?0:1)-(b.severity==='error'?0:1));
  for(const issue of issues.slice(0,500)) {
    const tr=node('tr'),kind=node('td'),level=node('span',issue.severity==='error'?'需要修改':'提醒','severity '+issue.severity);
    kind.append(node('strong',names[issue.code]||'核对资料'),level);
    const productName=report.products.find(x=>x.sku===issue.subject)?.name;
    tr.append(kind,node('td',issue.subject+(productName?' · '+productName:'')+(issue.row?' · 第 '+issue.row+' 行':'')),node('td',issueText(issue)));
    const action=node('td');
    const image=report.images.find(x=>x.source===issue.subject);
    if(image) {const button=node('button',image.valid?'看图确认':'查看图片','text-button');button.type='button';button.addEventListener('click',()=>editImage(image.source));action.append(button);}
    else if(issue.code==='MISSING_IMAGE'){const button=node('button','补选图片','text-button');button.type='button';button.addEventListener('click',()=>$('appendImages').click());action.append(button);}
    tr.append(action);$('issues').append(tr);
  }
  if(!issues.length){const tr=node('tr'),td=node('td','没有需要处理的问题。可以到“看图核对”再确认一次。');td.colSpan=4;tr.append(td);$('issues').append(tr);}
  $('issueCaption').textContent=issues.length>500?'显示前 500 项，下载清单包含全部问题。':'需要修改的项目排在前面；提醒需确认后才能打包。';
}
function preview(image, large=false) {
  const asset=batch.images.find(x=>x.name===image.source);
  if(!asset||!image.valid)return node('span',image.missing?'缺图':'无法预览','image-placeholder');
  if(!previewUrls.has(image.source))previewUrls.set(image.source,URL.createObjectURL(asset.file));
  const img=node('img');img.src=previewUrls.get(image.source);img.alt=image.product_name||image.sku||asset.file.name;img.loading='lazy';img.className=large?'large-preview':'thumbnail';
  img.addEventListener('error',()=>img.replaceWith(node('span','此浏览器无法预览','image-placeholder')),{once:true});
  return img;
}
function renderReview() {
  if(!report)return;
  const search=$('reviewSearch').value.toLocaleLowerCase(),filter=$('reviewFilter').value;
  const rows=reviewRows(report).filter(x=>(!search||[x.source,x.sku,x.product_name].join(' ').toLocaleLowerCase().includes(search))&&(filter==='all'||filter==='needs'&&x.needs_review||filter==='manual'&&x.assignment_source==='manual'));
  const pageCount=Math.max(1,Math.ceil(rows.length/PAGE_SIZE));reviewPages=pageCount;reviewPage=Math.max(0,Math.min(reviewPage,pageCount-1));
  $('reviewRows').replaceChildren();
  for(const row of rows.slice(reviewPage*PAGE_SIZE,(reviewPage+1)*PAGE_SIZE)) {
    const tr=node('tr'),imageCell=node('td'),info=node('td'),product=node('td'),action=node('td');
    if(row.source){const button=node('button',undefined,'preview-button');button.type='button';button.setAttribute('aria-label','查看图片 '+row.source);button.append(preview(row));button.addEventListener('click',()=>editImage(row.source));imageCell.append(button);}
    else imageCell.append(preview(row));
    info.append(node('strong',row.source||'没有对应图片'),node('small',row.width?`${row.width} × ${row.height} px`:'需要补充或替换'));
    product.append(node('strong',row.sku||'尚未归类'),node('span',row.product_name||''));
    if(row.assignment_source==='manual')product.append(node('small','已人工确认','manual-label'));
    const button=node('button',row.source?'调整归属':'补选图片','text-button');button.type='button';button.addEventListener('click',()=>row.source?editImage(row.source):$('appendImages').click());action.append(button);
    tr.append(imageCell,info,product,action);$('reviewRows').append(tr);
  }
  if(!rows.length){const tr=node('tr'),td=node('td','没有符合条件的图片。');td.colSpan=4;tr.append(td);$('reviewRows').append(tr);}
  $('pageLabel').textContent=`${rows.length} 项 · 第 ${reviewPage+1} / ${pageCount} 页`;
  $('previous').disabled=busy||reviewPage===0;$('next').disabled=busy||reviewPage>=pageCount-1;
}
function switchTab(tab) {
  activeTab=tab;$('issuePane').hidden=tab!=='issues';$('reviewPane').hidden=tab!=='review';
  $('issuesTab').setAttribute('aria-selected',String(tab==='issues'));$('reviewTab').setAttribute('aria-selected',String(tab==='review'));
  if(tab==='review')renderReview();
}
function show(data) {
  report=data;$('results').hidden=false;$('json').textContent=JSON.stringify(data,null,2);
  $('productCount').textContent=data.summary.products+' 条';$('matchedCount').textContent=data.summary.matched_images+' 张';$('problemCount').textContent=data.issues.length?data.issues.length+' 处':'无';
  $('resultTitle').textContent='核对这一批';$('resultCaption').textContent='人工归属会随资料包一起记录。';
  $('issuesTab').textContent=`问题与提醒（${data.issues.length}）`;
  $('warningRow').hidden=data.summary.errors>0||data.summary.warnings===0;
  if(!data.issues.length)activeTab='review';
  renderIssues();switchTab(activeTab);
  message(data.summary.errors?`有 ${data.summary.errors} 处需要修改${data.summary.warnings?`，另有 ${data.summary.warnings} 处提醒`:''}。`:data.summary.warnings?`有 ${data.summary.warnings} 处提醒，请查看后确认。`:'资料检查通过，可以打包了。',data.summary.errors?'error':data.summary.warnings?'':'ok');
}
async function run(action='check') {
  if(busy)return;
  if(action==='pack'&&(!report||report.summary.errors))return;
  busy=true;controls();message(action==='check'?'正在检查…':action==='checklist'?'正在整理补件清单…':'重新检查并生成资料包…');
  try {
    const result=await request(action,await payload());
    if(result.report)show(result.report);
    else {download(result.blob,action==='checklist'?'SKU-Kit-补件清单.csv':'SKU-Kit-资料包.zip');message(action==='checklist'?'补件清单已生成，可以发给供应商。':'资料包已生成，请在浏览器下载记录里查看。','ok');}
  }catch(error){invalidate();message(error.message,'error');}
  finally{busy=false;controls();if(report&&activeTab==='review')renderReview();}
}
function editImage(source) {
  const image=report?.images.find(x=>x.source===source);if(!image||busy)return;
  editingSource=source;$('assignmentPreview').replaceChildren(preview({...image,product_name:''},true));
  $('assignmentPath').textContent=source;
  const related=report.issues.filter(x=>x.subject===source).map(issueText);
  $('assignmentContext').textContent=related.join(' ')||'请核对图片内容与产品名称，再确认归属。';
  $('assignmentSku').value=image.sku||'';$('assignmentError').textContent='';
  $('confirmAssignment').disabled=!image.valid;$('unassign').disabled=!image.valid;
  const seen=new Set();$('skuOptions').replaceChildren();
  for(const product of report.products)if(product.sku&&!seen.has(product.sku)){seen.add(product.sku);const option=node('option',product.name||product.sku);option.value=product.sku;$('skuOptions').append(option);}
  $('assignDialog').showModal();
}
async function assign(mode) {
  if(!editingSource||!report||busy)return;
  const image=report.images.find(x=>x.source===editingSource),sku=$('assignmentSku').value;
  if(mode==='assign'&&!report.products.some(x=>x.sku===sku)){$('assignmentError').textContent='请填写产品表中存在的完整编号。';return;}
  if(mode==='auto')delete assignments[editingSource];
  else assignments[editingSource]={sku:mode==='unassign'?null:sku,sha256:image.sha256,table_sha256:report.table_sha256,context_sha256:report.context_sha256};
  $('assignDialog').close();editingSource=null;$('allow').checked=false;markDirty();activeTab='review';await run();
}
function editedFiles() {
  clearPreviews();invalidate();$('reviewSearch').value='';$('reviewFilter').value='all';fileInfo();markDirty();message('资料已更新，请重新检查。');
}
function appendFiles(files) {
  if(busy||!files.length)return;
  try {
    const merged=mergeImages(batch.images,files);
    if((batch.table?.size||0)+merged.images.reduce((n,x)=>n+x.file.size,0)>MAX_BYTES)throw new Error('补选后资料超过 50 MB，当前资料保持不变。');
    batch.images=merged.images;for(const name of merged.replaced)delete assignments[name];
    editedFiles();if(merged.replaced.length)message(`本批次已替换 ${merged.replaced.length} 张同名图片，相关人工确认已清除。原文件不改动。`);
  }catch(error){message(error.message,'error');}
  $('appendImages').value='';
}
async function saveProject() {
  if(busy||!batch.table)return;busy=true;controls();message('正在保存原表、图片和核对进度…');
  try {const project=buildProject(await payload());download(new Blob([JSON.stringify(project)],{type:'application/json'}),'商品资料进度.sku-kit.json');dirty=false;$('draftStatus').textContent='已生成进度文件';message('进度文件已生成，包含原表和图片，请妥善保存。','ok');}
  catch(error){message(error.message,'error');}finally{busy=false;controls();}
}
function decodedFile(entry) {
  const raw=atob(entry.content),bytes=new Uint8Array(raw.length);for(let i=0;i<raw.length;i++)bytes[i]=raw.charCodeAt(i);
  return new File([bytes],entry.name.split('/').pop());
}
async function restoreProject(file) {
  if(!file||busy)return;busy=true;controls();message('正在恢复进度，并重新检查文件…');
  try {
    if(file.size>72*1024*1024)throw new Error('进度文件超过 72 MB。');
    const data=validateProject(JSON.parse(await file.text()));
    const restored={table:decodedFile(data.table),images:data.images.map(x=>({name:x.name,file:decodedFile(x)}))};
    const result=await request('check',data);if(!result.report)throw new Error('恢复检查失败。');
    clearPreviews();batch=restored;assignments=data.options.assignments||{};
    $('table').value='';$('images').value='';$('column').value=data.options.sku_column||'';$('sheet').value=data.options.sheet||'';
    $('mode').value=data.options.match_mode||'exact';$('duplicates').value=data.options.duplicate_policy||'error';
    $('ignoreCase').checked=data.options.case_sensitive===false;$('width').value=data.options.min_width??0;$('height').value=data.options.min_height??0;
    $('allow').checked=false;$('reviewSearch').value='';$('reviewFilter').value='all';fileInfo();activeTab='review';reviewPage=0;show(result.report);dirty=false;$('draftStatus').textContent='已恢复进度';
  }catch(error){message('未恢复：'+error.message+' 当前资料保持不变。','error');}
  finally{busy=false;controls();$('projectInput').value='';}
}
$('form').addEventListener('submit',event=>{event.preventDefault();run();});
$('pack').addEventListener('click',()=>run('pack'));$('checklist').addEventListener('click',()=>run('checklist'));
$('saveProject').addEventListener('click',saveProject);$('openProject').addEventListener('click',()=>$('projectInput').click());
$('projectInput').addEventListener('change',event=>restoreProject(event.target.files[0]));
$('table').addEventListener('change',()=>{if($('table').files[0]){batch.table=$('table').files[0];assignments={};editedFiles();}});
$('images').addEventListener('change',()=>{batch.images=Array.from($('images').files).map(file=>({name:fileKey(file),file}));assignments={};editedFiles();});
for(const id of ['column','sheet','mode','width','height','ignoreCase','duplicates'])$(id).addEventListener('change',()=>{if(id==='column'||id==='sheet')assignments={};invalidate();fileInfo();markDirty();message('规则已修改，请重新检查。');});
$('appendButton').addEventListener('click',()=>$('appendImages').click());$('appendImages').addEventListener('change',event=>appendFiles(Array.from(event.target.files)));
$('allow').addEventListener('change',controls);
$('issuesTab').addEventListener('click',()=>switchTab('issues'));$('reviewTab').addEventListener('click',()=>switchTab('review'));
for(const id of ['reviewSearch','reviewFilter'])$(id).addEventListener(id==='reviewSearch'?'input':'change',()=>{reviewPage=0;renderReview();});
$('previous').addEventListener('click',()=>{reviewPage--;renderReview();});$('next').addEventListener('click',()=>{reviewPage++;renderReview();});
$('confirmAssignment').addEventListener('click',()=>assign('assign'));$('unassign').addEventListener('click',()=>assign('unassign'));$('autoAssign').addEventListener('click',()=>assign('auto'));
$('cancelAssignment').addEventListener('click',()=>$('assignDialog').close());
fileInfo();controls();
