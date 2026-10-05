import {overlayState, liveGeometry, realTracks} from './overlay.js';
const $=id=>document.getElementById(id);
const canvas=$('video'),ctx=canvas.getContext('2d'),directions=['north','south','east','west'];
let config,geometry,state,bitmap,bitmapAt=0,connected=false,pollBusy=false,frameBusy=false,editing=false,newPoints=null,drag=null,setupBusy=false;
const reviewed=new Set();
const copy=x=>structuredClone(x);
const text=x=>x===null||x===undefined?'—':String(x);
let prefs={};try{prefs=JSON.parse(localStorage.getItem('rohai-ui')??'{}');}catch{}
for(const id of ['boxes','ids','zones','geometry-toggle','ai-decision']){if(!['zones','geometry-toggle'].includes(id)&&typeof prefs[id]==='boolean')$(id).checked=prefs[id];$(id).onchange=()=>{prefs[id]=$(id).checked;localStorage.setItem('rohai-ui',JSON.stringify(prefs));draw();};}
// Store display preferences only. Authentication stays in the HttpOnly cookie.
async function api(path,method='GET',body){
 let r;try{r=await fetch('/api/maket/'+path,{method,credentials:'same-origin',headers:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});}catch{const e=Error('Сервер недоступен. Проверьте, что RohAI запущен.');e.offline=true;throw e;}
 if(!r.ok){if(r.status===401){connected=false;throw Error('Сессия завершена. Перезапустите RohAI или откройте инженерный вход.');}const raw=await r.text();const e=Error('Операция не выполнена. Откройте подробности в настройках.');e.detail=raw;e.status=r.status;throw e;}
 return r.status===204?null:r.json();
}
function error(e){if(e.offline||!connected){connected=false;bitmap?.close();bitmap=null;if(state){state={...state,camera_connection:'DISCONNECTED',readiness:{VISION_READY:false,CONTROL_READY:false,HARDWARE_READY:false},tracks:[],proposed:null,virtual_signal_state:null};render();}}$('notice').textContent=e.message;$('retry').hidden=false;}
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
 const proposal=$('ai-decision').checked&&state.demo_running&&state.readiness?.CONTROL_READY?state.proposed:null;const phase=proposal?.phase;$('recommendation').textContent={north_south:'СЕВЕР ↔ ЮГ',east_west:'ВОСТОК ↔ ЗАПАД',pedestrian:'ПЕШЕХОДЫ'}[phase]??'Ожидание';
 $('duration').textContent=proposal?.green_seconds==null?'—':proposal.green_seconds+' сек';
 const group=(a,b)=>[m[a],m[b]].every(v=>typeof v?.queue_count==='number')?m[a].queue_count+m[b].queue_count:null;
 const oldest=(a,b)=>[m[a],m[b]].every(v=>typeof v?.max_wait_seconds==='number')?Math.max(m[a].max_wait_seconds,m[b].max_wait_seconds).toFixed(0):'—';
 $('factors').textContent=`Измерения: очередь EW ${text(group('east','west'))}, NS ${text(group('north','south'))}. Старейшее ожидание EW ${oldest('east','west')}s, NS ${oldest('north','south')}s.`;
 const ready=state.readiness??{};const checks=[['Камера',state.camera_connection==='CONNECTED'],['AI Vision',ready.VISION_READY],['Контроллер',state.demo_running]];
 $('system').replaceChildren(...checks.map(([label,ok])=>{const span=document.createElement('span'),dot=document.createElement('i');dot.className='dot'+(ok?' ok':'');span.append(dot,label);return span;}));
 let message=ready.CONTROL_READY?'SYSTEM READY ✓':!state.preflight?.model_compatible?'Модель AI недоступна':state.camera_connection!=='CONNECTED'?'Камера недоступна':!state.preflight?.calibration_valid||!state.preflight?.calibration_complete?'Требуется калибровка':'Ожидаем свежие измерения';
 if(state.camera_revalidation_required||String(state.calibration_status).startsWith('INVALID')||String(state.calibration_status).startsWith('SUSPECT'))message='Положение камеры не подтверждено — проверьте калибровку.';
 $('ready').textContent=message;$('calibration-open').hidden=state.camera_connection!=='CONNECTED'||state.demo_running;
 $('main-action').textContent=state.demo_running?'ОСТАНОВИТЬ':'НАЧАТЬ';$('main-action').classList.toggle('stopping',!!state.demo_running);
 $('main-action').disabled=setupBusy||(!state.demo_running&&(!ready.CONTROL_READY||state.output_mode!=='CAMERA_ONLY'));
 const virtual=state.virtual_signal_state;const lights=$('lights');lights.replaceChildren(...directions.map((d,i)=>{const el=document.createElement('div');el.className='signal';el.append(['N','S','E','W'][i]);const prefix=i<2?'NS':'EW';for(const c of ['red','yellow','green']){const lamp=document.createElement('i');const on=virtual&&(c==='red'?!virtual.startsWith(prefix):virtual===prefix+'_'+c.toUpperCase());lamp.className='lamp '+c+(on?' on':'');el.append(lamp);}return el;}));
 lights.setAttribute('aria-label',virtual?'Виртуальное состояние '+virtual:'Виртуальное состояние недоступно');
 $('physical').textContent=state.hardware?.connected?'PHYSICAL CONNECTED · ACK не доказывает свечение ламп':'Физические лампы не подключены';
 if(!editing&&connected){$('wizard').hidden=!!ready.CONTROL_READY||state.demo_running;}
 const valid=!!liveGeometry(state,canvas.width,canvas.height);
 for(const id of ['zones','geometry-toggle'])$(id).disabled=!valid;
 const status=overlayState(state,canvas.width,canvas.height,editing,document.body.classList.contains('present'));
 $('calibration-message').textContent=status==='NO_CALIBRATION'?'Калибровка макета не выполнена':status==='CALIBRATION_INVALID'?'Recalibration required':'';
 $('calibration-open').textContent='Настроить';
 draw();
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
 const active=editable?geometry:(!presentation?liveGeometry(state,canvas.width,canvas.height):null);
 if(active&&(editable||$('geometry-toggle').checked))poly(active.detection_zone,'#c4b5fd');
 for(const a of active?.approaches??[]){if($('zones').checked||editable)poly(a.queue_zone,'#f8c558');if(editable||$('geometry-toggle').checked){poly(a.incoming,'#62d9e9');for(const lane of Object.values(a.lanes??{}))poly(lane,'#62d9e9');for(const name of ['entry_line','exit_line']){const l=a[name];if(l){ctx.strokeStyle='#62d9e9';ctx.beginPath();ctx.moveTo(l.start.x*canvas.width,l.start.y*canvas.height);ctx.lineTo(l.end.x*canvas.width,l.end.y*canvas.height);ctx.stroke();}}poly(a.waiting_zone,'#c4b5fd');poly(a.crosswalk_zone,'#ff8390');poly(a.exit_zone,'#d5a3ff');if(a.stop_line){ctx.strokeStyle='#ff596b';ctx.beginPath();ctx.moveTo(a.stop_line.start.x*canvas.width,a.stop_line.start.y*canvas.height);ctx.lineTo(a.stop_line.end.x*canvas.width,a.stop_line.end.y*canvas.height);ctx.stroke();}}}
 for(const t of realTracks(state)){const b=t.bbox;ctx.strokeStyle='#52ddb1';ctx.fillStyle='#52ddb1';if($('boxes').checked)ctx.strokeRect(b.x1,b.y1,b.x2-b.x1,b.y2-b.y1);if($('ids').checked)ctx.fillText('#'+t.track_id,b.x1,Math.max(20,b.y1-6));}
 if(editable){const pts=newPoints??selectedPoints();ctx.fillStyle='#fff';pts.forEach((p,i)=>{ctx.beginPath();ctx.arc(p.x*canvas.width,p.y*canvas.height,7,0,Math.PI*2);ctx.fill();ctx.fillText(String(i+1),p.x*canvas.width+10,p.y*canvas.height);});poly(selected(),'#fff');}
 $('empty').hidden=!!bitmap;
}
function showRoad(){if(!geometry||document.body.classList.contains('present'))return;geometry=completeDraft(geometry);editing=true;$('wizard').hidden=false;$('wizard-title').textContent='2 · Проверьте дороги и переходы';$('road-tools').hidden=false;$('continue').hidden=true;draw();}
async function choose(c){editing=false;newPoints=null;drag=null;if(setupBusy)return;setupBusy=true;try{config.camera.webcam_index=c.index;config.camera.camera_backend=c.backend??'AUTO';config.camera_selected=true;await api('config','PUT',config);config=await api('config');geometry=await api('calibration-draft');state=await api('state');$('camera-name').textContent=c.friendly_name??'Выбранная камера';$('candidates').replaceChildren();$('notice').textContent='Открываем камеру. Направьте её на макет.';render();}catch(e){error(e);}finally{setupBusy=false;}}
async function discover(){if(setupBusy)return;setupBusy=true;$('wizard').hidden=false;$('wizard-title').textContent='1 · Ищем камеру';$('notice').textContent='Ищем доступные камеры…';let found;try{found=await api('discovery');}catch(e){error(e);return;}finally{setupBusy=false;}const available=found.cameras.filter(c=>c.available);if(available.length===1){await choose(available[0]);return;}if(!available.length){$('notice').textContent='Камера недоступна. Разрешите доступ к камере в Windows и закройте Teams/Zoom.';$('retry').hidden=false;return;}$('wizard-title').textContent='1 · Выберите камеру';$('candidates').replaceChildren(...available.map((c,i)=>{const b=document.createElement('button');if(c.preview){const img=document.createElement('img');img.src='data:image/jpeg;base64,'+c.preview;img.alt='Предпросмотр камеры';b.append(img);}b.append(c.friendly_name??'Камера '+(i+1));b.onclick=()=>choose(c);return b;}));}
async function connect(){try{await api('session','POST');config=await api('config');geometry=await api('calibration-draft');state=await api('state');connected=true;$('retry').hidden=true;$('notice').textContent='Shadow: рекомендации и виртуальные сигналы. Физические команды не отправляются.';$('camera-name').textContent=state.camera_metadata?.friendly_name??(config.camera_selected?'Выбранная камера':'Выберите камеру');render();if(!config.camera_selected)await discover();}catch(e){error(e);}}
$('retry').onclick=async()=>{if(!connected)return connect();if(!config.camera_selected)return discover();try{await api('control','POST',{action:'reconnect_camera'});$('retry').hidden=true;}catch(e){error(e);}};
$('main-action').onclick=async()=>{const b=$('main-action');b.disabled=true;try{state=await api('control','POST',{action:state.demo_running?'stop':'camera_only_start'});$('notice').textContent=state.demo_running?'SHADOW · рекомендации без физических команд':'Контроллер остановлен';render();}catch(e){error(e);}finally{render();}};
$('presentation').onclick=async()=>{document.body.classList.toggle('present');const on=document.body.classList.contains('present');if(on){editing=false;$('road-tools').hidden=true;}draw();prefs.presentation=on;localStorage.setItem('rohai-ui',JSON.stringify(prefs));if(on){try{await document.documentElement.requestFullscreen();}catch{}}else if(document.fullscreenElement)await document.exitFullscreen();};
$('calibration-open').onclick=showRoad;
$('suggest').onclick=async()=>{try{const r=await api('calibration-suggestion','POST');if(!r.available){$('notice').textContent='Автоматическая разметка не определена. Нарисуйте зоны на изображении.';return;}geometry=completeDraft(r.geometry);reviewed.clear();$('verified').checked=false;$('save-calibration').disabled=true;$('notice').textContent='Предложение по границам изображения. Проверьте каждый подъезд, пешеходные зоны и линии.';draw();}catch(e){error(e);}};
const resetReview=()=>{reviewed.delete($('direction').value+':'+$('field').value);$('verified').checked=false;$('save-calibration').disabled=true;};
for(const id of ['direction','field'])$(id).onchange=()=>{newPoints=null;draw();};
$('draw-new').onclick=()=>{newPoints=[];resetReview();draw();};
$('commit').onclick=()=>{if(!newPoints)return;const line=$('field').value.endsWith('_line');if(line?newPoints.length!==3:newPoints.length<3){$('notice').textContent=line?'Отметьте начало, конец и точку со стороны приближения.':'Отметьте минимум 3 вершины зоны.';return;}const value=line?{start:newPoints[0],end:newPoints[1],upstream:newPoints[2]}:{points:newPoints};const a=geometry.approaches.find(a=>a.direction===$('direction').value),f=$('field').value;if(f==='detection_zone')geometry[f]=value;else if(f==='lane')a.lanes['lane-1']=value;else a[f]=value;newPoints=null;resetReview();draw();};
$('review-region').onclick=()=>{reviewed.add($('direction').value+':'+$('field').value);$('review-count').textContent='Проверено разметок: '+reviewed.size;};
$('verified').onchange=()=>{$('save-calibration').disabled=!$('verified').checked||!bitmap;};
const pos=e=>{const r=canvas.getBoundingClientRect();return{x:Math.max(0,Math.min(1,(e.clientX-r.left)/r.width)),y:Math.max(0,Math.min(1,(e.clientY-r.top)/r.height))};};
canvas.onpointerdown=e=>{if(!editing)return;const p=pos(e);if(newPoints){newPoints.push(p);draw();return;}drag=selectedPoints().find(v=>Math.hypot((v.x-p.x)*canvas.clientWidth,(v.y-p.y)*canvas.clientHeight)<20)??null;if(drag){canvas.setPointerCapture(e.pointerId);resetReview();}};
canvas.onpointermove=e=>{if(drag){Object.assign(drag,pos(e));draw();}};canvas.onpointerup=()=>{drag=null;};canvas.onpointercancel=()=>{drag=null;};
$('save-calibration').onclick=async()=>{if(!bitmap||!$('verified').checked)return;const b=$('save-calibration');b.disabled=true;try{geometry.reference_width=canvas.width;geometry.reference_height=canvas.height;geometry.calibration_validated_at=new Date().toISOString();await api('calibration','PUT',{intersection_id:'maket-1',version:Date.now(),created_at:new Date().toISOString(),orientation_degrees:0,camera_pose_verified:true,scale:{coordinate_unit:'relative'},geometry,reference_image_sha256:'0'.repeat(64)});editing=false;$('road-tools').hidden=true;$('wizard-title').textContent='3 · Калибровка сохранена';$('notice').textContent='Ожидаем проверку положения камеры и свежие измерения. Затем нажмите НАЧАТЬ.';}catch(e){error(e);}finally{b.disabled=!$('verified').checked;}};
setInterval(async()=>{if(!connected||pollBusy)return;pollBusy=true;try{state=await api('state');render();}catch(e){error(e);}finally{pollBusy=false;}},500);
setInterval(async()=>{if(!connected||frameBusy||state?.camera_connection!=='CONNECTED')return;frameBusy=true;try{const r=await fetch('/api/maket/frame',{credentials:'same-origin'});if(!r.ok){bitmap?.close();bitmap=null;draw();return;}const next=await createImageBitmap(await r.blob());bitmap?.close();bitmap=next;bitmapAt=performance.now();canvas.width=next.width;canvas.height=next.height;draw();}catch{bitmap?.close();bitmap=null;draw();}finally{frameBusy=false;}},150);
document.querySelector('nav a').onclick=()=>localStorage.setItem('rohai-ui-mode','engineer');
if(prefs.presentation)document.body.classList.add('present');
if(localStorage.getItem('rohai-ui-mode')==='engineer')location.replace('/maket/engineer');else connect();
