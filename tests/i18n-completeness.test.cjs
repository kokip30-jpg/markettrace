const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');

function english(){
  const values=new Map([['mt_language','en']]);
  const document={documentElement:{lang:'cs'},querySelectorAll:()=>[],createTreeWalker:()=>({nextNode:()=>false})};
  const context={localStorage:{getItem:k=>values.get(k)||null,setItem:(k,v)=>values.set(k,v)},document,window:{},Node:{TEXT_NODE:3,ELEMENT_NODE:1},NodeFilter:{SHOW_TEXT:4},MutationObserver:class{observe(){}}};
  vm.runInNewContext(fs.readFileSync('i18n.js','utf8'),context);
  return context.window.MTI18n.text;
}

test('every explicitly translated dynamic Czech label has an English translation',()=>{
  const translate=english(),source=fs.readFileSync('app.js','utf8');
  const labels=[...source.matchAll(/\btr\('((?:\\.|[^'])*)'\)/g)].map(m=>m[1].replace(/\\'/g,"'"));
  const czech=/[áčďéěíňóřšťúůýž]/i;
  const missing=[...new Set(labels.filter(label=>czech.test(label)&&translate(label)===label))];
  assert.deepEqual(missing,[],`Missing English translations: ${missing.join(' | ')}`);
});

test('market radar stays English after dynamic rendering',()=>{
  const translate=english();
  const labels=['DNEŠNÍ RADAR','Začít tady','Seřazeno podle aktuálního MarketTrace skóre a aktivity','Nejzajímavější dnes','Rizikový pohyb','Watchlist vyžaduje pozornost','zkontroluj zprávy a objem','titulů má dnes silnější signál','bez výrazného signálu'];
  for(const label of labels)assert.notEqual(translate(label),label,`Untranslated: ${label}`);
});
