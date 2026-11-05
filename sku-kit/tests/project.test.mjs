import {test} from 'node:test';
import assert from 'node:assert/strict';
import {buildProject,validateProject,reviewRows,mergeImages} from '../static/project.mjs';

function payload(){return {table:{name:'products.csv',content:'U0tVCjAwMQ=='},images:[{name:'images/001.png',content:'cGljdHVyZQ=='}],options:{match_mode:'exact'},allow_warnings:true};}
test('project roundtrip preserves all files and resets warning consent',()=>{
  const original=payload(),project=JSON.parse(JSON.stringify(buildProject(original)));
  const restored=validateProject(project);
  assert.deepEqual(restored.table,original.table);assert.deepEqual(restored.images,original.images);
  assert.equal(restored.allow_warnings,false);
});
test('wrong format and malformed base64 rejected',()=>{
  assert.throws(()=>validateProject({format:'other'}));
  for(const text of ['bad','a===','AA=A','====']){const data=buildProject(payload());data.payload.images[0].content=text;assert.throws(()=>validateProject(data));}
});
test('duplicate paths and path traversal rejected',()=>{
  const data=buildProject(payload());data.payload.images.push(data.payload.images[0]);assert.throws(()=>validateProject(data));
  data.payload.images=[{name:'../001.png',content:'AAAA'}];assert.throws(()=>validateProject(data));
});
test('large base64 validation does not overflow regexp stack',()=>{
  const data=buildProject(payload());data.payload.images[0].content='AAAA'.repeat(1000000);assert.doesNotThrow(()=>validateProject(data));
});
test('review includes product name, unassigned images, and missing products',()=>{
  const rows=reviewRows({products:[{sku:'001',name:'cup'},{sku:'002',name:'bag'}],images:[{source:'a.png',sku:'001',valid:true},{source:'b.png',sku:null,valid:true}],issues:[{subject:'b.png'}]});
  assert.equal(rows[0].product_name,'cup');assert.equal(rows[1].needs_review,true);
  assert.equal(rows[2].missing,true);assert.equal(rows[2].sku,'002');
});
test('image replacement preserves source path and does not mutate existing inputs',()=>{
  const old={name:'a.png',size:12},replacement={name:'a.png',size:20};
  const source=[{name:'images/a.png',file:old}];
  const result=mergeImages(source,[replacement]);
  assert.equal(source[0].file,old);assert.equal(result.images[0].name,'images/a.png');
  assert.equal(result.images[0].file,replacement);assert.deepEqual(result.replaced,['images/a.png']);
});
test('ambiguous same basenames are appended, never silently overwritten',()=>{
  const source=[{name:'folder1/a.png',file:{name:'a.png',size:1}},{name:'folder2/a.png',file:{name:'a.png',size:1}}];
  const result=mergeImages(source,[{name:'a.png',size:2}]);
  assert.equal(result.images.length,3);assert.equal(result.replaced.length,0);
});
test('oversized attachment fails without changing the current batch',()=>{
  const source=[{name:'images/a.png',file:{name:'a.png',size:1}}];
  assert.throws(()=>mergeImages(source,[{name:'a.png',size:60*1024*1024}]));
  assert.equal(source[0].file.size,1);
});
