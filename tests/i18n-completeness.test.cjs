const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');

function language(lang){
  const values=new Map([['mt_language',lang]]);
  const document={documentElement:{lang:'cs'},querySelectorAll:()=>[],createTreeWalker:()=>({nextNode:()=>false})};
  const context={localStorage:{getItem:k=>values.get(k)||null,setItem:(k,v)=>values.set(k,v)},document,window:{},Node:{TEXT_NODE:3,ELEMENT_NODE:1},NodeFilter:{SHOW_TEXT:4},MutationObserver:class{observe(){}}};
  vm.runInNewContext(fs.readFileSync('i18n.js','utf8'),context);
  return context.window.MTI18n.text;
}

const languages=['en','de','it','fr'];
const czech=/[áčďéěíňóřšťúůýž]/i;
function labelsIn(source){return [...source.matchAll(/\btr\('((?:\\.|[^'])*)'\)/g)].map(m=>m[1].replace(/\\'/g,"'"));}

test('every dynamic Czech label is translated in every supported interface',()=>{
  const source=fs.readFileSync('app.js','utf8');
  const labels=labelsIn(source);
  for(const lang of languages){const translate=language(lang);const missing=[...new Set(labels.filter(label=>czech.test(label)&&translate(label)===label))];assert.deepEqual(missing,[],`Missing ${lang} translations: ${missing.join(' | ')}`);}
});

test('static Czech labels and the market radar never leak outside Czech',()=>{
  const html=fs.readFileSync('index.html','utf8');
  const staticLabels=[...html.matchAll(/>([^<>{}]+)</g)].map(m=>m[1].trim().replace(/&amp;/g,'&')).filter(label=>czech.test(label));
  const labels=['DNEŠNÍ RADAR','Začít tady','Seřazeno podle aktuálního MarketTrace skóre a aktivity','Nejzajímavější dnes','Rizikový pohyb','Watchlist vyžaduje pozornost','zkontroluj zprávy a objem','titulů má dnes silnější signál','bez výrazného signálu'];
  for(const lang of languages){const translate=language(lang);for(const label of [...staticLabels,...labels])assert.notEqual(translate(label),label,`Untranslated ${lang}: ${label}`);}
});
