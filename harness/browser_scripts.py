"""Fixed browser functions; operands are always separate CDP JSON arguments."""

TARGET_GUARD = """for (const name of ['open','Worker','SharedWorker']) {
Object.defineProperty(globalThis,name,{value:function(){
 globalThis.__harnessUnsupportedTarget('unsupported target');
 throw new Error('Browser target unsupported');
},configurable:false,writable:false});
}"""

INTERACT = """function(selector,op,value){
const nodes=document.querySelectorAll(selector);
if(nodes.length!==1)return {ok:false,error:'selector must match exactly one element'};
const e=nodes[0],r=e.getBoundingClientRect(),style=getComputedStyle(e);
if(!r.width||!r.height||style.visibility==='hidden'||style.display==='none'||e.disabled)
 return {ok:false,error:'element unavailable'};
if(e.matches('input[type=password],input[type=file]')||
 /password|secret|token|api.?key/i.test(e.name+' '+e.id+' '+e.autocomplete))
 return {ok:false,error:'sensitive input unsupported'};
if(op==='click'){
 const a=e.closest('a');
 if(a&&(a.target&&a.target!=='_self'||a.hasAttribute('download')||
 !/^https?:$/.test(new URL(a.href,location.href).protocol)))
  return {ok:false,error:'unsupported link'};
 const f=e.form;
 if(f&&(f.target&&f.target!=='_self'||
 !/^https?:$/.test(new URL(f.action,location.href).protocol)))
  return {ok:false,error:'unsupported form'};
 e.click();
}else{
 if(!e.matches('input,textarea')||e.readOnly)return {ok:false,error:'unsupported input'};
 e.value=value;
 e.dispatchEvent(new Event('input',{bubbles:true}));
 e.dispatchEvent(new Event('change',{bubbles:true}));
}
return {ok:true};}
"""

ASSERT = """function(selector,op,expected){
const n=document.querySelectorAll(selector);
if(n.length!==1)return {ok:false};
const e=n[0],r=e.getBoundingClientRect(),s=getComputedStyle(e);
return {ok:!!r.width&&!!r.height&&s.visibility!=='hidden'&&s.display!=='none'&&
 (op==='assert_visible'||e.textContent===expected)};
}"""

MASK = """function(selectors){
const elements=[];
for(const selector of selectors){
 const es=document.querySelectorAll(selector);
 if(selector!=='input[type=password]'&&!es.length)return {ok:false};
 for(const e of es)if(!elements.includes(e))elements.push(e);
}
const masks=elements.map(e=>({value:e.style.getPropertyValue('visibility'),
 priority:e.style.getPropertyPriority('visibility')}));
globalThis.__harnessMaskElements=elements;
for(const e of elements)e.style.setProperty('visibility','hidden','important');
return {ok:true,masks};
}"""

UNMASK = """function(masks){
const elements=globalThis.__harnessMaskElements;
if(!Array.isArray(elements)||elements.length!==masks.length)return {ok:false};
elements.forEach((e,i)=>e.style.setProperty('visibility',masks[i].value,masks[i].priority));
delete globalThis.__harnessMaskElements;
return {ok:true};
}"""
