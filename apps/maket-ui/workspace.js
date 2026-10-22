// No secrets in JS or localStorage. Persistent pairing grants viewing only.
let authorized=false,persistent=false;
export async function connectLocal(api){
 try{const p=await api('permissions');authorized=p.setup_authorized;persistent=p.persistent_viewer===true;return;}catch{}
 try{await api('session','POST');authorized=true;return;}catch(e){if(e.status!==403)throw e;}
 if(!confirm('Разрешить этому браузеру просмотр RohAI на 7 дней? Настройка и физическое управление разрешаются отдельно. Доступ можно отозвать в диагностике.'))throw Error('Локальный просмотр не разрешён. Нажмите Подключить для повторного сопряжения.');
 await api('pair-viewer','POST',{consent:true});authorized=false;persistent=true;
}
export async function authorizeSetup(api){
 if(authorized){try{const p=await api('permissions');if(p.setup_authorized)return;authorized=false;}catch(e){if(e.status!==404)authorized=false;}}
 let remembered=false;try{remembered=localStorage.getItem('rohai-camera-consent')==='granted';}catch{}
 if(!remembered&&!confirm('Разрешить RohAI находить и подключать камеру на этом компьютере? Разрешение запомнится в этом браузере. Его можно отозвать в разделе «Диагностика». Физический запуск перекрёстка потребует отдельного подтверждения.'))throw Error('Доступ к камере не разрешён.');
 await api('session','POST',{consent:true});authorized=true;
 try{localStorage.setItem('rohai-camera-consent','granted');}catch{}
}
export const canConfigure=()=>authorized;
export const canDiscoverAutomatically=()=>authorized&&!persistent;
export const confirmPhysical=()=>confirm('Начать физическое управление учебным макетом? Проверьте независимую аппаратную защиту, аварийную кнопку и свободную зону макета. Потребуется локальный ARM на ESP32.');
export function publicError(detail){return detail&&typeof detail==='object'&&detail.code?`${detail.code}: ${detail.message??'Операция не выполнена'}. ${detail.action??''}`:null;}
