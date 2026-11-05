export const PROJECT_FORMAT = 'sku-kit-project';
export const MAX_BYTES = 50 * 1024 * 1024;

export function validateProject(value) {
  if (!value || value.format !== PROJECT_FORMAT || value.version !== 1) throw new Error('这不是支持的进度文件。');
  const data = value.payload;
  if (!data || !data.table || typeof data.table.name !== 'string' || !Array.isArray(data.images)) throw new Error('进度文件缺少产品表或图片。');
  if (data.images.length > 5000) throw new Error('进度文件超过 5,000 个文件。');
  const names = new Set();
  let total = 0;
  for (const file of [data.table, ...data.images]) {
    if (!file || typeof file.name !== 'string' || !file.name || file.name.startsWith('/') || file.name.includes('\\') || file.name.split('/').includes('..')) throw new Error('进度文件包含无效路径。');
    if (typeof file.content !== 'string' || file.content.length % 4 || /[^A-Za-z0-9+/=]/.test(file.content)) throw new Error('进度中的文件内容无效。');
    const padding=file.content.endsWith('==')?2:file.content.endsWith('=')?1:0;
    if(file.content.slice(0,file.content.length-padding).includes('='))throw new Error('进度中的文件内容无效。');
    total += file.content.length / 4 * 3 - (file.content.endsWith('==') ? 2 : file.content.endsWith('=') ? 1 : 0);
  }
  if (total > MAX_BYTES) throw new Error('进度中的原始资料超过 50 MB。');
  for (const image of data.images) {
    if (names.has(image.name)) throw new Error('进度中的图片路径重复。');
    names.add(image.name);
  }
  if (!data.options || typeof data.options !== 'object' || Array.isArray(data.options)) throw new Error('进度缺少处理规则。');
  const allowed = new Set(['sku_column','sheet','match_mode','case_sensitive','min_width','min_height','duplicate_policy','assignments']);
  if (Object.keys(data.options).some(key => !allowed.has(key))) throw new Error('进度包含不支持的规则。');
  return {...data, allow_warnings: false};
}

export function buildProject(payload) {
  return {format: PROJECT_FORMAT, version: 1, saved_at: new Date().toISOString(), payload: {...payload, allow_warnings: false}};
}

export function fileKey(file) {
  return file.webkitRelativePath || file.name;
}

export function mergeImages(existing, files) {
  const images=existing.map(x=>({...x})), replaced=[];
  for(const file of files) {
    const matches=images.filter(x=>x.file.name===file.name);
    if(matches.length===1){matches[0].file=file;replaced.push(matches[0].name);}
    else {
      let name='补选/'+file.name,index=2;
      while(images.some(x=>x.name===name))name='补选/'+index+++'_'+file.name;
      images.push({name,file});
    }
  }
  if(images.length>5000||images.reduce((n,x)=>n+x.file.size,0)>MAX_BYTES)throw new Error('补选后超过 5,000 个文件或 50 MB，请分批处理。');
  return {images,replaced};
}

export function reviewRows(report) {
  const names = new Map();
  for (const product of report.products) if (!names.has(product.sku)) names.set(product.sku, product.name || '');
  const withImages = new Set();
  const issues = new Set(report.issues.map(x => x.subject));
  const rows = report.images.map(image => {
    if (image.sku && image.valid) withImages.add(image.sku);
    return {...image, product_name: names.get(image.sku) || '',
      needs_review: issues.has(image.source) || !image.valid || !image.sku};
  });
  for (const [sku, name] of names) if (sku && !withImages.has(sku)) rows.push({source:'',sku,product_name:name,valid:false,missing:true,needs_review:true});
  return rows;
}
