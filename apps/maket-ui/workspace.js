// No secrets in JS or localStorage. Persistent pairing grants viewing only.
let authorized=false,persistent=false;
export async function connectLocal(api){
 try{const p=await api('permissions');authorized=p.setup_authorized;persistent=p.persistent_viewer===true;return;}catch{}
 try{await api('session','POST');authorized=true;return;}catch(e){if(e.status!==403)throw e;}
 if(!confirm('Разрешить этому браузеру просмотр RohAI на 7 дней? Настройка и физическое управление разрешаются отдельно. Доступ можно отозвать в диагностике.'))throw Error('Локальный просмотр не разрешён. Нажмите Подключить для повторного сопряжения.');
 await api('pair-viewer','POST',{consent:true});authorized=false;persistent=true;
}
export async function authorizeSetup(api){
 if(authorized){try{const p=await api('permissions');if(p.setup_authorized)return;authorized=false;}catch(e){if(e.status===404)return;authorized=false;}}
 if(!confirm('Разрешить настройку и управление учебным макетом на текущий запуск (до 8 часов)? Физический запуск потребует отдельного подтверждения и проверок безопасности.'))throw Error('Разрешение на настройку не предоставлено.');
 await api('session','POST',{consent:true});authorized=true;
}
export const canConfigure=()=>authorized;
export const canDiscoverAutomatically=()=>authorized&&!persistent;
export const confirmPhysical=()=>confirm('Начать физическое управление учебным макетом? Проверьте независимую аппаратную защиту, аварийную кнопку и свободную зону макета. Потребуется локальный ARM на ESP32.');
export function publicError(detail){return detail&&typeof detail==='object'&&detail.code?`${detail.code}: ${detail.message??'Операция не выполнена'}. ${detail.action??''}`:null;}
