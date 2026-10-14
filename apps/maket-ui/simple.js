import {overlayState, liveGeometry, frameTracks} from './overlay.js';
import {displayedSignal} from './signals.js';
import {connectLocal,authorizeSetup,canConfigure,canDiscoverAutomatically,confirmPhysical,publicError} from './workspace.js';
const $=id=>document.getElementById(id);
const canvas=$('video'),ctx=canvas.getContext('2d'),directions=['north','south','east','west'];
let config,geometry,state,bitmap,bitmapAt=0,connected=false,pollBusy=false,frameBusy=false,editing=false,newPoints=null,drag=null,setupBusy=false;
let frameContext=null, anchorMode=false, autoSuggestedKey=null;
const reviewed=new Set();
const strategy=document.createElement('select');strategy.id='camera-strategy';strategy.setAttribute('aria-label','Стратегия рекомендаций');
for(const [value,name] of [['ADAPTIVE','Adaptive · длительность по нагрузке'],['ACTUATED','Actuated · выбор по очередям'],['FIXED','Fixed · фиксированный цикл']]){const option=document.createElement('option');option.value=value;option.textContent=name;strategy.append(option);}
const strategyLabel=document.createElement('label');strategyLabel.append('Стратегия рекомендаций ',strategy);document.querySelector('.workspace-controls').append(strategyLabel);
strategy.onchange=async()=>{strategy.disabled=true;try{await authorizeSetup(api);state=await api('control','POST',{action:'policy',policy:strategy.value,policy_mode:'SHADOW'});render();}catch(e){error(e);}finally{strategy.disabled=!!(state?.running||state?.demo_running);}};
const copy=x=>structuredClone(x);
const text=x=>x===null||x===undefined?'—':String(x);
let prefs={};try{prefs=JSON.parse(localStorage.getItem('rohai-ui')??'{}');}catch{}
for(const id of ['boxes','ids','zones','geometry-toggle','ai-decision']){if(!['zones','geometry-toggle'].includes(id)&&typeof prefs[id]==='boolean')$(id).checked=prefs[id];$(id).onchange=()=>{prefs[id]=$(id).checked;localStorage.setItem('rohai-ui',JSON.stringify(prefs));draw();};}
// Store display preferences only. Authentication stays in the HttpOnly cookie.
async function api(path,method='GET',body){
 let r;try{r=await fetch('/api/maket/'+path,{method,credentials:'same-origin',headers:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});}catch{const e=Error('Сервер недоступен. Проверьте, что RohAI запущен.');e.offline=true;throw e;}
 if(!r.ok){if(r.status===401)connected=false;const raw=await r.text();let detail;try{detail=JSON.parse(raw).error??JSON.parse(raw).detail;}catch{}const e=Error(publicError(detail)??(r.status===401?'Сессия завершена. Нажмите Подключить.':'Операция не выполнена. Проверьте диагностику.'));e.detail=raw;e.status=r.status;throw e;}
 return r.status===204?null:r.json();
}
function error(e){if(e.offline||!connected){connected=false;bitmap?.close();bitmap=null;if(state){state={...state,camera_connection:'DISCONNECTED',connection_lost:true,running:false,demo_running:false,vision_state:null,phase_remaining_seconds:null,hardware:{connected:false,ack:null,command_pending:false},pipeline:{state:'unavailable'},performance:{},readiness:{VISION_READY:false,CONTROL_READY:false,HARDWARE_READY:false},tracks:[],proposed:null,virtual_signal_state:null};render();}}$('notice').textContent=e.message;$('retry').hidden=false;}
function completeDraft(g){
 for(const a of g.approaches){a.lanes??={};if(!Object.keys(a.lanes).length)a.lanes['lane-1']=copy(a.incoming);
 const s=a.stop_line,u=s.upstream,m={x:(s.start.x+s.end.x)/2,y:(s.start.y+s.end.y)/2};
 const shift={x:(u.x-m.x)*.75,y:(u.y-m.y)*.75};
 const p=p=>({x:Math.max(.001,Math.min(.999,p.x+shift.x)),y:Math.max(.001,Math.min(.999,p.y+shift.y))});
 a.entry_line??={start:p(s.start),end:p(s.end),upstream:copy(u)};
 a.exit_line??=copy(s);a.waiting_zone??=copy(a.pedestrian_crossing??a.queue_zone);
 a.crosswalk_zone??=copy(a.pedestrian_crossing??a.queue_zone);a.exit_zone??=copy(a.pedestrian_crossing??a.queue_zone);
 }return g;
}
function render(){
 if(state?.camera_connection!=='CONNECTED'){bitmap?.close();bitmap=null;}
 if(!state)return;const m=state.readiness?.VISION_READY?(state.traffic?.directions??{}):{},values=directions.map(d=>m[d]);
 const sum=key=>values.every(v=>typeof v?.[key]==='number')?values.reduce((s,v)=>s+v[key],0):null;
 $('cars').textContent=text(sum('car_count'));$('queues').textContent=values.map(v=>text(v?.queue_count)).join(' / ');
 $('waiting').textContent=values.map(v=>v?.max_wait_seconds==null?'—':v.max_wait_seconds.toFixed(0)+'s').join(' / ');
 const count=sum('car_count');const average=count>0&&values.every(v=>typeof v?.average_wait_seconds==='number')?values.reduce((s,v)=>s+v.car_count*v.average_wait_seconds,0)/count:null;
 const passed=state.readiness?.VISION_READY?state.maket_metrics?.stop_line_departures_per_minute:null;
 $('outcomes').textContent=text(passed)+' / '+(average==null?'—':average.toFixed(1)+'s');
 const proposal=$('ai-decision').checked&&(state.demo_running||state.running)&&state.readiness?.CONTROL_READY?state.proposed:null;const phase=proposal?.phase;$('recommendation').textContent={north_south:'СЕВЕР ↔ ЮГ',east_west:'ВОСТОК ↔ ЗАПАД',pedestrian:'ПЕШЕХОДЫ'}[phase]??'Ожидание';
 $('duration').textContent=proposal?.green_seconds==null?'—':proposal.green_seconds+' сек';
 const group=(a,b)=>[m[a],m[b]].every(v=>typeof v?.queue_count==='number')?m[a].queue_count+m[b].queue_count:null;
 const oldest=(a,b)=>[m[a],m[b]].every(v=>typeof v?.max_wait_seconds==='number')?Math.max(m[a].max_wait_seconds,m[b].max_wait_seconds).toFixed(0):'—';
 $('factors').textContent=`${proposal?.reason?proposal.reason+' · ':''}Измерения: очередь EW ${text(group('east','west'))}, NS ${text(group('north','south'))}. Старейшее ожидание EW ${oldest('east','west')}s, NS ${oldest('north','south')}s.`;
 const ready=state.readiness??{};const checks=[['Камера',state.camera_connection==='CONNECTED'],['AI Vision',ready.VISION_READY],['Контроллер',state.demo_running||state.running]];
 const preflight=state.preflight??{};
 const preparation=[['camera_healthy','Камера передаёт кадры'],['model_compatible','Модель распознавания доступна'],['calibration_complete','Все направления, полосы и линии размечены'],['calibration_valid','Положение камеры и эталон подтверждены'],['observation_fresh','Кадры свежие'],['analytics_healthy','Измерения всех направлений доступны'],['controller_loaded','Контроллер доступен']];
 $('control-checklist').replaceChildren(...preparation.map(([key,label])=>{const item=document.createElement('li');const ok=preflight[key]===true;item.textContent=(ok?'✓ ':'○ ')+label+(ok?' — готово':' — требуется проверка');return item;}));
 $('system').replaceChildren(...checks.map(([label,ok])=>{const span=document.createElement('span'),dot=document.createElement('i');dot.className='dot'+(ok?' ok':'');span.append(dot,label);return span;}));
 let message=ready.CONTROL_READY?(state.output_mode==='PHYSICAL_OUTPUT'?(ready.HARDWARE_READY?'Готов к подтверждению физического старта':'Vision готов · аппаратные проверки не пройдены'):'REAL VISION готов ✓'):!state.preflight?.model_compatible?'Модель AI недоступна':state.camera_connection!=='CONNECTED'?'Камера недоступна':!state.preflight?.calibration_valid||!state.preflight?.calibration_complete?'Требуется калибровка':'Ожидаем свежие измерения';
 if(state.camera_revalidation_required||String(state.calibration_status).startsWith('INVALID')||String(state.calibration_status).startsWith('SUSPECT'))message='Положение камеры не подтверждено — проверьте калибровку.';
 $('ready').textContent=message;$('calibration-open').hidden=state.camera_connection!=='CONNECTED'||state.demo_running||state.running;
 const activeControl=state.running||state.demo_running;
 strategy.disabled=!!activeControl||state.output_mode!=='CAMERA_ONLY';if(['FIXED','ADAPTIVE','ACTUATED'].includes(state.policy))strategy.value=state.policy;
 $('main-action').textContent=activeControl?'ОСТАНОВИТЬ':'НАЧАТЬ';$('main-action').classList.toggle('stopping',!!activeControl);
 $('main-action').disabled=setupBusy||(!activeControl&&(!ready.CONTROL_READY||(state.output_mode==='PHYSICAL_OUTPUT'&&!ready.HARDWARE_READY)));
 const shown=displayedSignal(state),virtual=shown.phase;const lights=$('lights');lights.replaceChildren(...directions.map((d,i)=>{const el=document.createElement('div');el.className='signal';el.append(['N','S','E','W'][i]);const prefix=i<2?'NS':'EW';for(const c of ['red','yellow','green']){const lamp=document.createElement('i');const on=virtual&&(c==='red'?!virtual.startsWith(prefix):virtual===prefix+'_'+c.toUpperCase());lamp.className='lamp '+c+(on?' on':'');el.append(lamp);}return el;}));
 lights.setAttribute('aria-label',virtual?(shown.source==='virtual'?'Виртуальное состояние ':'Подтверждено ESP32: ')+virtual:'Состояние светофоров недоступно');
 const lightTitle=lights.previousElementSibling;lightTitle.firstChild.textContent=shown.source==='virtual'?'Виртуальные светофоры ':'Состояние ESP32 ';lightTitle.querySelector('.badge').textContent=shown.source==='virtual'?'PREVIEW':virtual?'ACK':'НЕТ СВЕЖЕГО ACK';
 $('physical').textContent=state.hardware?.connected?'PHYSICAL CONNECTED · ACK не доказывает свечение ламп':'Физические лампы не подключены';
 if(!editing&&connected){$('wizard').hidden=!!ready.CONTROL_READY||state.demo_running;}
 const valid=!!liveGeometry(state,canvas.width,canvas.height);
 for(const id of ['zones','geometry-toggle'])$(id).disabled=!valid;
 const status=overlayState(state,canvas.width,canvas.height,editing,document.body.classList.contains('present'));
 $('calibration-message').textContent=status==='CALIBRATING'?'Разметка — гипотеза. Проверьте все зоны и линии.':status==='NO_CALIBRATION'?'Калибровка макета не выполнена':status==='CALIBRATION_INVALID'?'Требуется повторная калибровка':'';
 $('calibration-open').textContent='Настроить';
 workspaceState();draw();
}
function selected(){const a=geometry?.approaches.find(a=>a.direction===$('direction').value);const f=$('field').value;if(f==='detection_zone')return geometry?.detection_zone;if(f==='lane')return a?.lanes?.['lane-1'];return a?.[f];}
function selectedPoints(){const v=selected();return v?.points??(v?[v.start,v.end,v.upstream]:[]);}
function poly(p,color){if(!p?.points)return;ctx.strokeStyle=color;ctx.beginPath();p.points.forEach((v,i)=>i?ctx.lineTo(v.x*canvas.width,v.y*canvas.height):ctx.moveTo(v.x*canvas.width,v.y*canvas.height));ctx.closePath();ctx.stroke();}
function draw(){ctx.clearRect(0,0,canvas.width,canvas.height);
 const live=connected&&state?.camera_connection==='CONNECTED'&&bitmap&&performance.now()-bitmapAt<1500;
 $('empty').hidden=!!live;
 $('empty').textContent=state?.camera_connection==='CONNECTED'?'Ожидаем живой кадр':'Камера недоступна';
 if(!live)return;
 ctx.drawImage(bitmap,0,0,canvas.width,canvas.height);ctx.lineWidth=3;ctx.font='18px system-ui';
 const presentation=document.body.classList.contains('present');
 const editable=editing&&!presentation;
 ctx.setLineDash(editable?[8,6]:[]);
 const active=editable?geometry:(!presentation?liveGeometry(state,canvas.width,canvas.height):null);
 if(active&&(editable||$('geometry-toggle').checked))poly(active.detection_zone,'#c4b5fd');
 for(const a of active?.approaches??[]){if($('zones').checked||editable)poly(a.queue_zone,'#f8c558');if(editable||$('geometry-toggle').checked){poly(a.incoming,'#62d9e9');for(const lane of Object.values(a.lanes??{}))poly(lane,'#62d9e9');for(const name of ['entry_line','exit_line']){const l=a[name];if(l){ctx.strokeStyle='#62d9e9';ctx.beginPath();ctx.moveTo(l.start.x*canvas.width,l.start.y*canvas.height);ctx.lineTo(l.end.x*canvas.width,l.end.y*canvas.height);ctx.stroke();}}poly(a.waiting_zone,'#c4b5fd');poly(a.crosswalk_zone,'#ff8390');poly(a.exit_zone,'#d5a3ff');if(a.stop_line){ctx.strokeStyle='#ff596b';ctx.beginPath();ctx.moveTo(a.stop_line.start.x*canvas.width,a.stop_line.start.y*canvas.height);ctx.lineTo(a.stop_line.end.x*canvas.width,a.stop_line.end.y*canvas.height);ctx.stroke();}}}
 ctx.setLineDash([]);
 for(const t of frameTracks(state,frameContext)){const b=t.bbox;ctx.strokeStyle='#52ddb1';ctx.fillStyle='#52ddb1';if($('boxes').checked)ctx.strokeRect(b.x1,b.y1,b.x2-b.x1,b.y2-b.y1);if($('ids').checked)ctx.fillText('#'+t.track_id,b.x1,Math.max(20,b.y1-6));}
 if(editable){const pts=newPoints??selectedPoints();ctx.fillStyle='#fff';pts.forEach((p,i)=>{ctx.beginPath();ctx.arc(p.x*canvas.width,p.y*canvas.height,7,0,Math.PI*2);ctx.fill();ctx.fillText(String(i+1),p.x*canvas.width+10,p.y*canvas.height);});poly(selected(),'#fff');}
 $('empty').hidden=!!bitmap;
}
function showRoad(){if(!geometry||document.body.classList.contains('present'))return;geometry=completeDraft(geometry);editing=true;$('wizard').hidden=false;$('wizard-title').textContent='2 · Проверьте дороги и переходы';$('road-tools').hidden=false;$('continue').hidden=true;draw();}
async function choose(c){editing=false;newPoints=null;drag=null;if(setupBusy)return;setupBusy=true;try{config.camera.webcam_index=c.index;config.camera.camera_backend=c.backend??'AUTO';config.camera_selected=true;await api('config','PUT',config);config=await api('config');geometry=await api('calibration-draft');state=await api('state');$('camera-name').textContent=c.friendly_name??'Выбранная камера';$('candidates').replaceChildren();$('notice').textContent='Открываем камеру. Направьте её на макет.';render();}catch(e){error(e);}finally{setupBusy=false;}}
async function discover(){if(setupBusy)return;setupBusy=true;$('wizard').hidden=false;$('wizard-title').textContent='1 · Ищем камеру';$('notice').textContent='Ищем доступные камеры…';let found;try{found=await api('discovery');}catch(e){error(e);return;}finally{setupBusy=false;}const available=found.cameras.filter(c=>c.available);if(available.length===1){await choose(available[0]);return;}if(!available.length){$('notice').textContent='Камера недоступна. Разрешите доступ к камере в Windows и закройте Teams/Zoom.';$('retry').hidden=false;return;}$('wizard-title').textContent='1 · Выберите камеру';$('candidates').replaceChildren(...available.map((c,i)=>{const b=document.createElement('button');if(c.preview){const img=document.createElement('img');img.src='data:image/jpeg;base64,'+c.preview;img.alt='Предпросмотр камеры';b.append(img);}b.append(c.friendly_name??'Камера '+(i+1));b.onclick=()=>choose(c);return b;}));}
async function connect(){try{await connectLocal(api);config=await api(canConfigure()?'config':'view-config');geometry=await api('calibration-draft');state=await api('state');connected=true;$('retry').hidden=true;$('notice').textContent='Подключено. Физический запуск требует подтверждения и проверок.';$('camera-name').textContent=state.camera_metadata?.friendly_name??(config.camera_selected?'Выбранная камера':'Выберите камеру');render();if(!config.camera_selected&&canDiscoverAutomatically())await discover();}catch(e){error(e);}}
$('retry').onclick=async()=>{if(!connected)return connect();try{await authorizeSetup(api);config=await api('config');if(!config.camera_selected)return discover();await api('control','POST',{action:'reconnect_camera'});$('retry').hidden=true;}catch(e){error(e);}};
$('main-action').onclick=async()=>{const b=$('main-action');b.disabled=true;try{await authorizeSetup(api);const physical=state.output_mode==='PHYSICAL_OUTPUT',active=state.running||state.demo_running;if(physical&&!active&&!confirmPhysical())return;const body={action:active?'stop':physical?'start':'camera_only_start'};if(physical&&!active)body.confirm_physical=true;state=await api('control','POST',body);$('notice').textContent=state.running?'PHYSICAL MAKET · проверяйте подтверждённое состояние':state.demo_running?'REAL VISION · рекомендации без физического исполнения':'Контроллер остановлен';render();}catch(e){error(e);}finally{render();}};
$('presentation').onclick=async()=>{document.body.classList.toggle('present');const on=document.body.classList.contains('present');if(on){editing=false;$('road-tools').hidden=true;}draw();prefs.presentation=on;localStorage.setItem('rohai-ui',JSON.stringify(prefs));if(on){try{await document.documentElement.requestFullscreen();}catch{}}else if(document.fullscreenElement)await document.exitFullscreen();};
$('calibration-open').onclick=async()=>{try{await authorizeSetup(api);config=await api('config');showRoad();}catch(e){error(e);}};
$('suggest').onclick=async()=>{try{await authorizeSetup(api);const r=await api('calibration-suggestion','POST');if(!r.available){$('notice').textContent='Автоматическая разметка не определена. Нарисуйте зоны на изображении.';return;}geometry=completeDraft(r.geometry);anchorMode=false;showRoad();reviewed.clear();$('verified').checked=false;$('save-calibration').disabled=true;$('notice').textContent='Предложение по границам изображения. Проверьте каждый подъезд, пешеходные зоны и линии.';draw();}catch(e){error(e);}};
const resetReview=()=>{reviewed.delete($('direction').value+':'+$('field').value);$('verified').checked=false;$('save-calibration').disabled=true;};
for(const id of ['direction','field'])$(id).onchange=()=>{newPoints=null;draw();};
$('draw-new').onclick=()=>{newPoints=[];resetReview();draw();};
$('commit').onclick=()=>{if(!newPoints)return;const line=$('field').value.endsWith('_line');if(line?newPoints.length!==3:newPoints.length<3){$('notice').textContent=line?'Отметьте начало, конец и точку со стороны приближения.':'Отметьте минимум 3 вершины зоны.';return;}const value=line?{start:newPoints[0],end:newPoints[1],upstream:newPoints[2]}:{points:newPoints};const a=geometry.approaches.find(a=>a.direction===$('direction').value),f=$('field').value;if(f==='detection_zone')geometry[f]=value;else if(f==='lane')a.lanes['lane-1']=value;else a[f]=value;newPoints=null;resetReview();draw();};
$('review-region').onclick=()=>{reviewed.add($('direction').value+':'+$('field').value);$('review-count').textContent='Проверено разметок: '+reviewed.size;};
$('verified').onchange=()=>{$('save-calibration').disabled=!$('verified').checked||!bitmap;};
const pos=e=>{const r=canvas.getBoundingClientRect();return{x:Math.max(0,Math.min(1,(e.clientX-r.left)/r.width)),y:Math.max(0,Math.min(1,(e.clientY-r.top)/r.height))};};
canvas.onpointerdown=e=>{if(!editing)return;const p=pos(e);if(anchorMode){if(newPoints.length<4)newPoints.push(p);draw();return;}if(newPoints){newPoints.push(p);draw();return;}drag=selectedPoints().find(v=>Math.hypot((v.x-p.x)*canvas.clientWidth,(v.y-p.y)*canvas.clientHeight)<20)??null;if(drag){canvas.setPointerCapture(e.pointerId);resetReview();}};
canvas.onpointermove=e=>{if(drag){Object.assign(drag,pos(e));draw();}};canvas.onpointerup=()=>{drag=null;};canvas.onpointercancel=()=>{drag=null;};
$('save-calibration').onclick=async()=>{if(!bitmap||!$('verified').checked)return;const b=$('save-calibration');b.disabled=true;try{await authorizeSetup(api);geometry.reference_width=canvas.width;geometry.reference_height=canvas.height;geometry.calibration_validated_at=new Date().toISOString();await api('calibration','PUT',{intersection_id:'maket-1',version:Date.now(),created_at:new Date().toISOString(),orientation_degrees:0,camera_pose_verified:true,scale:{coordinate_unit:'relative'},geometry,reference_image_sha256:'0'.repeat(64)});editing=false;$('road-tools').hidden=true;$('wizard-title').textContent='3 · Калибровка сохранена';$('notice').textContent='Ожидаем проверку положения камеры и свежие измерения. Затем нажмите НАЧАТЬ.';}catch(e){error(e);}finally{b.disabled=!$('verified').checked;}};
setInterval(async()=>{if(!connected||pollBusy)return;pollBusy=true;try{state=await api('state');render();}catch(e){error(e);}finally{pollBusy=false;}},500);
setInterval(async()=>{if(!connected||frameBusy||state?.camera_connection!=='CONNECTED')return;frameBusy=true;try{const r=await fetch('/api/maket/frame?analyzed=true',{credentials:'same-origin'});if(!r.ok){bitmap?.close();bitmap=null;draw();return;}const next=await createImageBitmap(await r.blob());bitmap?.close();bitmap=next;frameContext={id:Number(r.headers.get('X-Frame-Id')),epoch:Number(r.headers.get('X-Stream-Epoch')),width:next.width,height:next.height};bitmapAt=performance.now();canvas.width=next.width;canvas.height=next.height;draw();}catch{bitmap?.close();bitmap=null;draw();}finally{frameBusy=false;}},150);
document.querySelector('a[href="/maket/engineer"]').onclick=()=>localStorage.setItem('rohai-ui-mode','engineer');
if(prefs.presentation)document.body.classList.add('present');
if(localStorage.getItem('rohai-ui-mode')==='engineer')location.replace('/maket/engineer');else connect();

const perspectiveButton=document.createElement('button');perspectiveButton.textContent='Перспектива: 4 угла';$('suggest').after(perspectiveButton);
const perspectiveApply=document.createElement('button');perspectiveApply.textContent='Применить 4 угла';perspectiveButton.after(perspectiveApply);
perspectiveButton.onclick=()=>{showRoad();anchorMode=true;newPoints=[];resetReview();$('notice').textContent='Отметьте внешний прямоугольник: верхний левый, верхний правый, нижний правый, нижний левый. Это предварительная геометрия.';draw();};
perspectiveApply.onclick=async()=>{try{if(!anchorMode||newPoints.length!==4)throw Error('Нужны 4 угла по часовой стрелке');await authorizeSetup(api);const r=await api('calibration-suggestion','POST',{corners:newPoints});geometry=completeDraft(r.geometry);anchorMode=false;newPoints=null;reviewed.clear();$('verified').checked=false;$('save-calibration').disabled=true;$('notice').textContent='Проверьте каждую зону и линию. Гипотеза не заменяет подтверждения.';draw();}catch(e){error(e);}};
setInterval(()=>{if(connected&&canConfigure()&&bitmap&&state?.overlay_calibration?.state==='NO_CALIBRATION'&&!state.running&&!state.demo_running&&!editing&&!setupBusy&&!document.body.classList.contains('present')){const key=JSON.stringify(config?.camera);if(autoSuggestedKey!==key){autoSuggestedKey=key;$('suggest').onclick();}}},1000);

function workspaceState(){
 $('mode-badge').textContent=state.output_mode==='PHYSICAL_OUTPUT'?'PHYSICAL MAKET':'REAL VISION';$('mode').value=state.output_mode;
 $('connection-badge').textContent=connected?'Backend подключён':'Нет подключения';
 $('safety-badge').textContent=state.connection_lost?'Состояние неизвестно · проверьте макет':state.failure?'SAFE STATE':state.running?'Физическое управление активно':'Физическое управление отключено';
 const h=state.hardware??{};$('phase-state').textContent=`ESP32: ${text(h.ack?.actual_phase)} · ожидает ACK: ${h.command_pending?'да':'нет'} · контроллер: ${state.running||state.demo_running?'активен':'остановлен'} · время фазы: ${state.phase_remaining_seconds==null?'—':state.phase_remaining_seconds.toFixed(1)+' с (оценка контроллера)'}`;
 const v=state.vision_state??{};$('confidence').textContent=`Измерения с камеры · достоверность: ${text(v.directions?.length?Math.min(...v.directions.map(d=>d.confidence)).toFixed(2):null)} · свежесть: ${v.fresh?'fresh':'stale/unavailable'} · калибровка: ${text(state.calibration_status)}`;
 const p=state.performance?.rates??{};$('performance').textContent=`Vision: ${text(state.pipeline?.state)} · FPS: ${text(p.inference_fps??state.camera_metadata?.fps)} · значения недоступны до живых измерений`;
 $('setup-state').textContent=`Автоподготовка: ${state.autosetup?.state??'—'} / ${state.autosetup?.step??'—'}`;
 $('device-state').textContent=`ESP32: ${h.connected?'подключён':'отключён'} · firmware: ${text(h.ack?.firmware_version??state.autosetup?.device?.ack?.firmware_version)} · последнее ACK: ${text(h.ack?.reason)}. ACK не доказывает свечение ламп.`;
 $('issues').replaceChildren(...[...(state.issues??[]),...(state.autosetup?.issues??[])].map(i=>{const li=document.createElement('li');li.textContent=publicError(i);return li;}));
 $('record').textContent=state.recording?.video?'Остановить запись видео':'Записать видео';$('record').disabled=state.recording?.available===false;
 $('mode').disabled=!!(state.running||state.demo_running);$('reset').disabled=!!(state.running||state.demo_running);
}
async function control(action){try{await authorizeSetup(api);state=await api('control','POST',{action});render();}catch(e){error(e);}}
$('connect').onclick=async()=>{if(!connected)return connect();try{await authorizeSetup(api);config=await api('config');await api('autosetup','POST');$('notice').textContent='Проверяем устройства. Управление остаётся отключённым.';}catch(e){error(e);}};
$('calibrate').onclick=async()=>{try{await authorizeSetup(api);config=await api('config');if(!config.camera_selected)await discover();else showRoad();}catch(e){error(e);}};
for(const id of ['pause','stop'])$(id).onclick=()=>control('stop');$('estop').onclick=()=>control('estop');$('reset').onclick=()=>control('reset');
$('mode').onchange=async()=>{const selectedMode=$('mode').value;try{await authorizeSetup(api);config=await api('config');config.output_mode=selectedMode;await api('config','PUT',config);state=await api('state');render();}catch(e){error(e);render();}};
$('record').onclick=()=>{if(!state.recording?.video&&!confirm('Сохранить локальное видео камеры? На нём могут быть люди и личные данные. Срок хранения задаётся в настройках; удаление выбранного закрытого сеанса требует подтверждения.'))return;control('record');};
$('replay').onclick=async()=>{try{const items=await api('sessions');$('history').hidden=false;$('sessions').replaceChildren(...items.map(s=>{const row=document.createElement('p'),link=document.createElement('a');link.href='/maket/replay/'+s.id;link.textContent=`${new Date(s.created_at*1000).toLocaleString()} · ${s.mode} · ${s.complete?'полная':'неполная'}${s.expired?' · срок хранения истёк':''}`;row.append(link);if(s.closed){const b=document.createElement('button');b.textContent='Удалить';b.onclick=async()=>{if(!confirm('Безвозвратно удалить выбранный сеанс '+s.id+' и его видео?'))return;try{await authorizeSetup(api);await api('sessions/'+s.id,'DELETE',{confirm_delete:true});$('replay').onclick();}catch(e){error(e);}};row.append(b);}return row;}));if(!items.length)$('sessions').textContent='Записей пока нет.';}catch(e){error(e);}};
$('revoke').onclick=async()=>{if(!confirm('Отозвать доступ браузера и остановить его активное управление?'))return;try{await api('pair-viewer','DELETE');connected=false;error(Error('Доступ отозван. Нажмите Подключить для нового разрешения.'));}catch(e){error(e);}};

// Keyboard alternative to pointer-only calibration: coordinates are normalized.
const coordinateTools=document.createElement('div');
for(const [id,caption,value] of [['point-number','Вершина',1],['point-x','X (0…1)',.5],['point-y','Y (0…1)',.5]]){const label=document.createElement('label'),input=document.createElement('input');input.id=id;input.type='number';input.value=value;input.min=id==='point-number'?1:0;input.max=id==='point-number'?64:1;input.step=id==='point-number'?1:.01;label.append(caption,input);coordinateTools.append(label);}
for(const [caption,add] of [['Добавить точку с клавиатуры',true],['Изменить вершину',false]]){const b=document.createElement('button');b.textContent=caption;b.onclick=()=>{if(!editing)return;const p={x:Number($('point-x').value),y:Number($('point-y').value)};if(!Number.isFinite(p.x)||!Number.isFinite(p.y)||p.x<0||p.x>1||p.y<0||p.y>1){$('notice').textContent='Координаты должны быть от 0 до 1.';return;}if(add){newPoints??=[];if(newPoints.length<64)newPoints.push(p);}else{const points=newPoints??selectedPoints(),index=Number($('point-number').value)-1;if(!Number.isInteger(index)||!points[index]){$('notice').textContent='Выберите существующую вершину.';return;}Object.assign(points[index],p);}resetReview();draw();};coordinateTools.append(b);}
$('draw-help').after(coordinateTools);
