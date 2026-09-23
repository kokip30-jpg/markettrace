import { createClient } from '@supabase/supabase-js';
import { Preferences } from './preferences.cjs';
const client=createClient('https://rftgfskqvyhdgirhwmvm.supabase.co','sb_publishable_wFJUnq_itAff-rfnlORGWQ_7zIOVr8V',{
  auth:{storageKey:'mt_auth_v1',detectSessionInUrl:false},
  global:{fetch:(url,options={})=>fetch(url,{...options,signal:options.signal||AbortSignal.timeout(15000)})}
});
const $=s=>document.querySelector(s);
const status=text=>{$('#accountStatus').textContent=text;};
const prefs=new Preferences({
  storage:window.localStorage,status,
  read:async user=>{const {data,error}=await client.from('user_preferences').select('key,value').eq('user_id',user);if(error)throw error;return data;},
  write:async(user,batch)=>{const {error}=await client.from('user_preferences').upsert(Object.entries(batch).map(([key,value])=>({user_id:user,key,value})),{onConflict:'user_id,key'});if(error)throw error;}
});
const dialog=$('#accountDialog');
$('#accountBtn').onclick=()=>dialog.showModal();
$('#accountClose').onclick=()=>dialog.close();
$('#accountRetry').onclick=async()=>{if(!prefs.ready)location.reload();else await prefs.flush();};
$('#accountForm').onsubmit=async e=>{
  e.preventDefault();const submit=$('#accountSubmit');submit.disabled=true;$('#accountMessage').textContent='Přihlašuji…';
  const name=$('#accountName').value.trim().toLowerCase();
  const email=name==='test'?'test@accounts.markettrace.invalid':name;
  try {
    const {error}=await client.auth.signInWithPassword({email,password:$('#accountPassword').value});
    if(error)throw error;
    $('#accountPassword').value='';location.reload();
  } catch {$('#accountMessage').textContent='Přihlášení se nezdařilo. Zkontroluj údaje a připojení.';}
  finally{submit.disabled=false;}
};
$('#accountLogout').onclick=async()=>{
  if(!(await prefs.flush())){$('#accountMessage').textContent='Nejdřív ulož změny. Připojení k účtu teď nefunguje.';return;}
  const {error}=await client.auth.signOut({scope:'local'});
  if(error){$('#accountMessage').textContent='Odhlášení se nezdařilo. Zkus to znovu.';return;}
  location.reload();
};
async function init(){
  try {
    const {data,error}=await client.auth.getSession();if(error)throw error;
    const user=data.session?.user;
    await prefs.init(user?.id||null);
    $('#accountBtn').textContent=user?'Můj účet':'Přihlásit';
    $('#accountForm').hidden=!!user;$('#accountLogout').hidden=!user;
    $('#accountIdentity').textContent=user?(user.email==='test@accounts.markettrace.invalid'?'Účet: test':'Účet: '+user.email):'Přihlas se a ukládej nastavení do svého účtu.';
    if(!user)status('Bez přihlášení · pouze tento prohlížeč');
    $('#accountRetry').hidden=!user;
    client.auth.onAuthStateChange((event,session)=>{
      if(event!=='INITIAL_SESSION' && (session?.user?.id||null)!==(user?.id||null)) location.reload();
    });
    void prefs.flush();
    return prefs;
  } catch {
    status('Účet se nepodařilo načíst');$('#accountMessage').textContent='Aby se nepřepsalo uložené nastavení, počkáme na spojení. Zkus načtení znovu.';
    $('#accountRetry').hidden=false;dialog.showModal();
    throw new Error('Account preferences could not be loaded');
  }
}
window.MTAccounts={ready:init()};
window.addEventListener('online',()=>void prefs.flush());
window.addEventListener('beforeunload',e=>{if(prefs.dirty()){e.preventDefault();e.returnValue='';}});
setInterval(()=>{if(prefs.dirty())void prefs.flush();},15000);
