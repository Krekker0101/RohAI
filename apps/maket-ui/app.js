import {liveGeometry, overlayState, frameTracks} from './overlay.js';
import {connectLocal,authorizeSetup,confirmPhysical,publicError} from './workspace.js';
const $ = id => document.getElementById(id);
let cameras=[], config, geometry, state, frame, points=[], enabled=false, frameBusy=false, stateBusy=false, editing=false;
const canvas=$('video'), ctx=canvas.getContext('2d');
let frameContext=null;
const toggles={Detections:true,'Track IDs':true,Geometry:false,'Queue Zones':false,Queues:true,Debug:false,'AI Decision':true};
const escape = text => String(text).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function api(path,method='GET',body) {
  const response=await request('/api/maket/'+path,{method,headers:{'X-Operator-Token':$('token').value.trim(),'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});
  if(!response.ok){const raw=await response.text();let detail;try{detail=JSON.parse(raw).error??JSON.parse(raw).detail;}catch{}const e=Error(publicError(detail)??(response.status===401?'Operator token rejected. Reconnect the local console.':raw));e.status=response.status;throw e;}
  return response.status===204?null:response.json();
}
async function request(url,options) {
  let response;
  try {response=await fetch(url,options);}catch {throw new Error('Backend connection lost. Check the running maket server and reconnect the console.');}
  if(response.status===401)enabled=false;
  return response;
}
async function action(body) {try {if(!$('token').value.trim())await authorizeSetup(api);if(['start','hardware_test_start'].includes(body.action)){if(!confirmPhysical())return;body.confirm_physical=true;}if(body.action==='record'&&!state.recording?.video&&!confirm('Сохранить локальное видео камеры? Удаление выбранной записи требует подтверждения.'))return;state=await api('control','POST',body);$('error').textContent='';render();}catch(e){$('error').textContent=e.message;}}
for(const [name,value] of Object.entries(toggles)) {
  const label=document.createElement('label'),input=document.createElement('input');input.type='checkbox';input.checked=value;
  input.dataset.overlay=name;input.onchange=()=>{toggles[name]=input.checked;draw();};label.append(input,document.createTextNode(name));$('toggles').append(label);
}
function render() {
  if(!state)return;
  $('connection').textContent=`${state.state} · ${state.running?'CONTROL RUNNING':'STOPPED'} · ${state.session_id}`;
  $('camera-status').textContent=`${state.camera_connection??state.pipeline.state} · ${state.calibration_status}`;
  $('subsystem-status').textContent=`CAMERA: ${state.camera_connection??'?'} | VISION: ${state.readiness?.VISION_READY?'RUNNING':state.pipeline.state} | TRACKING: ${state.readiness?.VISION_READY?'RUNNING':'WAITING'} | CONTROL: ${state.demo_running?'SHADOW':state.running?'PHYSICAL':'STOPPED'} | ESP32: ${state.hardware.connected?'CONNECTED':'NOT CONNECTED'}`;
  $('model-status').textContent=`VISION MODEL: ${state.vision_profile==='COCO_BASELINE'?'COCO BASELINE':state.vision_profile??'?'} | TOY ACCURACY: NOT VALIDATED`;
  $('start').disabled=state.output_mode==='CAMERA_ONLY';
  $('hardware-test-start').disabled=state.output_mode==='CAMERA_ONLY';
  $('reconnect').disabled=state.output_mode==='CAMERA_ONLY';
  if(state.camera_error||state.failure)$('error').textContent=state.camera_error??state.failure;
  if(state.camera_connection==='NOT_SELECTED'&&!cameras.length&&!$('discover').disabled)$('camera-test-result').textContent='Console connected. Discover cameras, then Select + Save camera to open the live feed. Calibration is required before starting shadow control.';
  const valid=!!liveGeometry(state,canvas.width,canvas.height);
  for(const input of $('toggles').querySelectorAll('input'))if(['Geometry','Queue Zones'].includes(input.dataset.overlay))input.disabled=!valid;
  const status=overlayState(state,canvas.width,canvas.height,editing);
  $('calibration-message').textContent=status==='CALIBRATING'?'HYPOTHESIS ? dashed configuration, not detected road':status==='NO_CALIBRATION'?'Калибровка макета не выполнена':status==='CALIBRATION_INVALID'?'Recalibration required':'';
  const p=state.proposed;
  $('decision').textContent=toggles['AI Decision']?`AI recommendation: ${p?.phase??'unavailable'} / ${p?.green_seconds??'—'} s (${state.policy_mode})`:'';
  const metrics=state.traffic?.directions??{};
  $('directions').innerHTML=Object.entries(metrics).map(([d,m])=>`<div class="metric"><b>${escape(d.toUpperCase())}</b><br>Cars: ${m.car_count??'unknown'}<br>Queue: ${m.queue_count??'unknown'}<br>Oldest wait: ${m.max_wait_seconds?.toFixed(1)??'unknown'} s<br>Mean wait: ${m.average_wait_seconds?.toFixed(1)??'unknown'} s<br>Confidence: ${m.observation?.measurement_confidence?.toFixed(2)??'unknown'}</div>`).join('');
  $('reason').textContent=`Inputs: ${Object.entries(metrics).map(([d,m])=>`${d} queue=${m.queue_count??'?'} wait=${m.max_wait_seconds?.toFixed(1)??'?'}s`).join(' · ')}. Fallback: ${state.policy_error??'none'}. ${state.policy==='LEARNED_AI'?'Model output; calibrated policy uncertainty unavailable.':'Measured baseline inputs; no generated narrative.'} Throughput: ${state.maket_metrics?.stop_line_departures_per_minute??'?'} stop crossings/min.`;
  $('preflight').innerHTML=Object.entries({...state.readiness,...state.preflight}).map(([name,ok])=>`<div class="${ok?'ok':'fail'}">${ok?'PASS':'FAIL'} · ${escape(name)}</div>`).join('');
  $('hardware').textContent=JSON.stringify({requested:state.proposed,approved:state.approved_state,ESP32:state.hardware,physical_lamp_optical_confirmation:state.physical_lamp_optical_confirmation},null,2);
  const rates=state.performance?.rates??{};
  const interval=state.latency_ms?.control_interval?.p50;
  $('perf').textContent=`Capture ${rates.capture_fps?.toFixed(1)??'?'} FPS · Inference ${rates.inference_fps?.toFixed(1)??'?'} FPS · Analytics ${rates.analytics_fps?.toFixed(1)??'?'} FPS · Control ${interval?(1000/interval).toFixed(1):'?'} Hz · camera→ACK p50/p95 ${state.latency_ms?.camera_to_ack?.p50?.toFixed(1)??'?'} / ${state.latency_ms?.camera_to_ack?.p95?.toFixed(1)??'?'} ms. ACK ≠ optical lamp proof. Vision latency p95 ${state.performance?.latency_ms?.camera_to_analytics?.p95?.toFixed(1)??"?"} ms. Dropped capture ${state.pipeline.dropped_capture??0} / inference ${state.pipeline.dropped_inference??0}. Recording drops ${state.recording.dropped}.`;
  draw();
}
function polygon(poly,color) {
  if(!poly?.points?.length)return;
  ctx.strokeStyle=color;ctx.beginPath();poly.points.forEach((p,i)=>i?ctx.lineTo(p.x*canvas.width,p.y*canvas.height):ctx.moveTo(p.x*canvas.width,p.y*canvas.height));ctx.closePath();ctx.stroke();
}
function line(l,color) {if(!l)return;ctx.strokeStyle=color;ctx.beginPath();ctx.moveTo(l.start.x*canvas.width,l.start.y*canvas.height);ctx.lineTo(l.end.x*canvas.width,l.end.y*canvas.height);ctx.stroke();}
function draw() {
  ctx.clearRect(0,0,canvas.width,canvas.height);if(frame)ctx.drawImage(frame,0,0,canvas.width,canvas.height);
  ctx.lineWidth=2;ctx.font='16px system-ui';
  const active=editing?geometry:liveGeometry(state,canvas.width,canvas.height);
  ctx.setLineDash(editing?[8,6]:[]);
  if((toggles.Geometry||editing)&&active) {polygon(active.detection_zone,'#c4b5fd');for(const a of active.approaches){polygon(a.incoming,'#67e8f9');polygon(a.queue_zone,'#fde68a');if(editing||toggles.Debug){line(a.stop_line,'#ef4444');line(a.entry_line,'#6ee7b7');line(a.exit_line,'#fb923c');}}}
  if(toggles['Queue Zones']&&active)for(const a of active.approaches)polygon(a.queue_zone,'#fde68a');
  ctx.setLineDash([]);
  for(const t of frameTracks(state,frameContext)) {
    const b=t.bbox;ctx.strokeStyle=t.queued?'#fbbf24':'#6ee7b7';ctx.fillStyle=ctx.strokeStyle;
    if(toggles.Detections)ctx.strokeRect(b.x1,b.y1,b.x2-b.x1,b.y2-b.y1);
    if(toggles['Track IDs']) {ctx.fillText(`#${t.track_id} ${state?.vision_state?.calibration_confirmed?t.lane:'UNASSIGNED'} ${t.queued?'QUEUE':'MOVE?'} ${t.current_wait_time.toFixed(1)}s`,b.x1,Math.max(18,b.y1-6));if(toggles.Debug){ctx.beginPath();t.trajectory.forEach((p,i)=>i?ctx.lineTo(p.point.x*canvas.width,p.point.y*canvas.height):ctx.moveTo(p.point.x*canvas.width,p.point.y*canvas.height));ctx.stroke();}}
  }
  if(toggles.Queues){ctx.fillStyle='#fff';let y=25;for(const [d,m] of Object.entries(state?.traffic?.directions??{})){ctx.fillText(`${d.toUpperCase()} Cars ${m.car_count??'?'} Queue ${m.queue_count??'?'} Oldest ${m.max_wait_seconds?.toFixed(1)??'?'}s`,16,y);y+=23;}}
  ctx.fillStyle='#f0abfc';for(const p of points){ctx.beginPath();ctx.arc(p.x*canvas.width,p.y*canvas.height,5,0,Math.PI*2);ctx.fill();}
}
async function getFrame() {
  if(!enabled||frameBusy||state?.camera_connection!=='CONNECTED')return;frameBusy=true;
  try {const response=await request('/api/maket/frame?analyzed=true',{headers:{'X-Operator-Token':$('token').value.trim()}});if(!response.ok){if(response.status===503){frame?.close();frame=null;draw();}return;}const bitmap=await createImageBitmap(await response.blob());frame?.close();frame=bitmap;canvas.width=bitmap.width;canvas.height=bitmap.height;frameContext={id:Number(response.headers.get('X-Frame-Id')),epoch:Number(response.headers.get('X-Stream-Epoch')),width:bitmap.width,height:bitmap.height};draw();}finally{frameBusy=false;}
}
setInterval(async()=>{if(!enabled||stateBusy)return;stateBusy=true;try{state=await api('state');render();}catch(e){$('error').textContent=e.message;}finally{stateBusy=false;}},500);
setInterval(()=>{getFrame().catch(e=>$('error').textContent=e.message);},100);
$('connect').onclick=async()=>{enabled=false;try{if(!$('token').value.trim()){await connectLocal(api);await authorizeSetup(api);}config=await api('config');$('protection-verified').checked=config.hardware.independent_protection_verified===true;$('retention-days').value=config.retention_days??7;for(const [id,key] of [['capture-width','camera_width'],['capture-height','camera_height'],['capture-fps','camera_fps']])$(id).value=config.camera[key]??30;geometry=await api('calibration-draft');$('vision-profile').value=config.vision_profile??'MAKET_MODEL';$('camera-index').value=config.camera.webcam_index;$('source').value=config.camera.source;$('camera-uri').value=config.camera.uri;$('model-package').value=config.camera.model_package??'';$('serial-port').value=config.hardware.port;$('camera-backend').value=config.camera.camera_backend??'AUTO';$('preset').value=config.performance_preset??'BALANCED';$('rotation').value=config.camera.camera_rotation??0;$('mirror').checked=config.camera.camera_mirror??false;$('crop').value=JSON.stringify(config.camera.camera_crop??[0,0,1,1]);$('output-mode').value=config.output_mode??'CAMERA_ONLY';$('geometry').textContent=JSON.stringify(geometry,null,2);state=await api('state');enabled=true;$('error').textContent='';render();}catch(e){$('error').textContent=e.message;}};
$('camera-only-start').onclick=()=>action({action:'camera_only_start'});
$('stop').onclick=()=>action({action:'estop'});$('pause').onclick=()=>action({action:'stop'});$('start').onclick=()=>action({action:'start'});$('reconnect').onclick=()=>action({action:'reconnect'});$('restart-camera').onclick=()=>action({action:'reconnect_camera'});$('revalidate-camera').onclick=()=>action({action:'revalidate_camera'});$('record').onclick=()=>action({action:'record'});
$('apply-policy').onclick=()=>action({action:'policy',policy:$('policy').value,policy_mode:$('policy-mode').value});
$('presentation').onclick=()=>{
  let prefs={};try{prefs=JSON.parse(localStorage.getItem('rohai-ui')??'{}');}catch{}
  prefs.presentation=true;localStorage.setItem('rohai-ui',JSON.stringify(prefs));
  localStorage.setItem('rohai-ui-mode','simple');location.href='/maket';
};
for(const [label,phase] of [['NS Green','north_south'],['EW Green','east_west'],['Pedestrian Green','pedestrian']]){const button=document.createElement('button');button.textContent=label;button.onclick=()=>action({action:'manual',phase,duration_s:20});$('hardware-buttons').append(button);}
for(const label of ['All Red','Safe Reset']){const b=document.createElement('button');b.textContent=label;b.onclick=()=>action({action:label==='All Red'?'estop':'reset'});$('hardware-buttons').append(b);}
for(const [label,phase] of [['NS Yellow','north_south'],['EW Yellow','east_west']]){const b=document.createElement('button');b.textContent=label+' (request clearance)';b.onclick=()=>action({action:'manual',phase:phase==='north_south'?'east_west':'north_south',duration_s:20});$('hardware-buttons').append(b);}
canvas.onclick=e=>{if(!editing)return;const rect=canvas.getBoundingClientRect();points.push({x:(e.clientX-rect.left)/rect.width,y:(e.clientY-rect.top)/rect.height});draw();};
$('calibration-open').onclick=()=>{editing=!editing;points=[];draw();};
$('clear').onclick=()=>{points=[];draw();};
const suggestionButton=document.createElement('button');suggestionButton.textContent='Auto calibration — preview hypothesis';
const anchorsButton=document.createElement('button');anchorsButton.textContent='Perspective — select 4 outer corners';
$('calibration-open').after(suggestionButton,anchorsButton);
let anchoring=false;
async function suggest(corners) {
  try {const r=await api('calibration-suggestion','POST',corners?{corners}:undefined);
    if(!r.available){$('calibration-message').textContent=r.reason;return;}
    geometry=r.geometry;editing=true;points=[];anchoring=false;$('verified').checked=false;
    $('geometry').textContent=JSON.stringify(geometry,null,2);
    $('calibration-message').textContent='HYPOTHESIS — dashed zones require correction and confirmation. '+r.reason;draw();
  } catch(e){$('error').textContent=e.message;}
}
suggestionButton.onclick=()=>suggest();
anchorsButton.onclick=()=>{editing=true;anchoring=true;points=[];$('calibration-message').textContent='Select outer view corners: top-left, top-right, bottom-right, bottom-left. Then Apply perspective.';draw();};
const applyAnchors=document.createElement('button');applyAnchors.textContent='Apply perspective';anchorsButton.after(applyAnchors);applyAnchors.onclick=()=>{if(anchoring&&points.length===4)suggest(points);else $('error').textContent='Four clockwise corners required';};
$('commit').onclick=()=>{try{if(!geometry)throw Error('Connect console first');const field=$('field').value,a=geometry.approaches.find(a=>a.direction===$('direction').value);if(field.endsWith('_line')){if(points.length!==3)throw Error('Line requires start/end/upstream anchor');a[field]={start:points[0],end:points[1],upstream:points[2]};}else{if(points.length<3)throw Error('Polygon requires ≥3 points');if(field==='detection_zone')geometry[field]={points:[...points]};else if(field==='lane'){a.lanes??={};a.lanes['lane-1']={points:[...points]};}else a[field]={points:[...points]};}points=[];$('geometry').textContent=JSON.stringify(geometry,null,2);draw();}catch(e){$('error').textContent=e.message;}};
$('save-calibration').onclick=async()=>{try{if(!frame||!geometry||!$('verified').checked)throw Error('Live frame and explicit pose/geometry verification required');geometry.reference_width=canvas.width;geometry.reference_height=canvas.height;geometry.calibration_validated_at=new Date().toISOString();const c={intersection_id:$('intersection').value,version:Date.now(),created_at:new Date().toISOString(),orientation_degrees:Number($('orientation').value),camera_pose_verified:true,scale:{coordinate_unit:$('unit').value,model_width_cm:$('width-cm').value?Number($('width-cm').value):null,model_height_cm:$('height-cm').value?Number($('height-cm').value):null,normalized_to_model_homography:$('homography').value?JSON.parse($('homography').value):null,road_equivalent_ratio:$('ratio').value?Number($('ratio').value):null},geometry,reference_image_sha256:'0'.repeat(64)};$('error').textContent=JSON.stringify(await api('calibration','PUT',c));editing=false;points=[];state=await api('state');render();}catch(e){$('error').textContent=e.message;}};
$('save-config').onclick=async()=>{editing=false;points=[];try{config.camera.source=$('source').value;config.camera.webcam_index=Number($('camera-index').value);config.camera.uri=$('camera-uri').value;config.camera_selected=true;config.camera.camera_backend=$('camera-backend').value;config.performance_preset=$('preset').value;for(const [id,key] of [['capture-width','camera_width'],['capture-height','camera_height'],['capture-fps','camera_fps']])config.camera[key]=Number($(id).value);config.camera.camera_rotation=Number($('rotation').value);config.camera.camera_mirror=$('mirror').checked;config.camera.camera_crop=JSON.parse($('crop').value);config.output_mode=$('output-mode').value;config.vision_profile=$('vision-profile').value;config.camera.deployment_domain=config.vision_profile==='COCO_BASELINE'?'UNSPECIFIED':'PHYSICAL_MAKET';config.camera.manifest_path=config.vision_profile==='COCO_BASELINE'?'configs/models/yolo26n.json':'configs/maket/model.json';config.camera.model_path=config.vision_profile==='COCO_BASELINE'?'datasets/models/yolo26n.pt':'datasets/models/maket.pt';config.camera.model_package=config.vision_profile==='COCO_BASELINE'?null:($('model-package').value||null);config.camera.model_registry=null;config.hardware.port=$('serial-port').value;config.hardware.independent_protection_verified=$('protection-verified').checked;config.retention_days=Number($('retention-days').value);const result=await api('config','PUT',config);config=await api('config');geometry=await api('calibration-draft');$('geometry').textContent=JSON.stringify(geometry,null,2);$('error').textContent=JSON.stringify(result);}catch(e){$('error').textContent=e.message;}};

$('hardware-test-start').onclick=()=>action({action:'hardware_test_start'});

function candidate() {return cameras.find(c=>String(c.index)===$('camera-choice').value);}
function selectCandidate() {const c=candidate();if(!c?.available)throw Error('Select an available camera first');$('camera-index').value=c.index;$('camera-backend').value=c.backend??'AUTO';}
function showProbe(c) {$('candidate-preview').hidden=!c.preview;if(c.preview)$('candidate-preview').src='data:image/jpeg;base64,'+c.preview;$('camera-test-result').textContent=c.available?`Camera ${c.index}: AVAILABLE ${c.width??'?'}x${c.height??'?'} @ ${c.fps??'?'} FPS / ${c.backend??'?'}. Discovery snapshot; Select + Save opens live feed.`:`${c.error??'CAMERA_OPEN_FAILED'}. ${c.help??'Check Windows Privacy camera access and close Teams/Zoom/browser.'}`;}
$('discover').onclick=async()=>{const b=$('discover');b.disabled=true;$('camera-test-result').textContent='Scanning indices 0-10; bounded DSHOW/MSMF probes. Wait for results.';try{const d=await api('discovery');cameras=d.cameras;$('serial-choice').replaceChildren(...['AUTO',...d.serial_ports.map(p=>p.port)].map(p=>{const o=document.createElement('option');o.value=p;o.textContent=p;return o;}));const placeholder=document.createElement('option');placeholder.value='';placeholder.textContent='Select Camera';$('camera-choice').replaceChildren(placeholder,...cameras.map(c=>{const o=document.createElement('option');o.value=c.index;o.disabled=!c.available;o.textContent=`Camera ${c.index} - ${c.friendly_name??'name unavailable'} - ${c.width??'?'}x${c.height??'?'} - ${c.fps??'?'} FPS - ${c.backend??'?'} - ${c.available?'AVAILABLE':c.error??'UNAVAILABLE'}`;return o;}));if(d.suggested_index!==null&&d.suggested_index!==undefined){$('camera-choice').value=d.suggested_index;selectCandidate();showProbe(candidate());}else $('camera-test-result').textContent=d.error??'Multiple cameras: choose and preview one.';$('error').textContent=d.error??'';}catch(e){$('error').textContent=e.message;}finally{b.disabled=false;}};
$('camera-preview').onclick=()=>{try{showProbe(candidate()??{error:'CAMERA_NOT_FOUND'});}catch(e){$('error').textContent=e.message;}};
$('camera-test').onclick=async()=>{try{const c=await api('camera-test','POST',{index:Number($('camera-index').value),backend:$('camera-backend').value});showProbe(c);if(c.available)$('camera-backend').value=c.backend;}catch(e){$('error').textContent=e.message;}};
$('camera-select').onclick=()=>{try{selectCandidate();$('save-config').click();}catch(e){$('error').textContent=e.message;}};
$('serial-choice').onchange=()=>{$('serial-port').value=$('serial-choice').value;};
// The CLI enables a process-local HttpOnly session; explicit tokens still work.
if(location.pathname==='/maket/engineer'){
  document.body.dataset.uiMode='engineer';
  localStorage.setItem('rohai-ui-mode','engineer');
  connectLocal(api).then(()=>authorizeSetup(api)).then(()=>$('connect').click()).catch(e=>$('error').textContent=e.message);
}
$('camera-choice').onchange=()=>{try{selectCandidate();showProbe(candidate());}catch(e){$('error').textContent=e.message;}};

$('simple-mode').onclick=()=>localStorage.setItem('rohai-ui-mode','simple');

for(const [id,label,value,min,max] of [['capture-width','Capture width',1280,160,3840],['capture-height','Capture height',720,120,2160],['capture-fps','Requested FPS',30,1,120]]){const node=document.createElement('label'),input=document.createElement('input');input.type='number';input.id=id;input.value=value;input.min=min;input.max=max;node.append(document.createTextNode(label),input);$('preset').parentElement.after(node);}

const customPreset=document.createElement('option');customPreset.textContent='CUSTOM';$('preset').append(customPreset);for(const id of ['capture-width','capture-height','capture-fps'])$(id).onchange=()=>{$('preset').value='CUSTOM';};

for(const [id,caption,type] of [['protection-verified','Independent protection physically verified by commissioning specialist','checkbox'],['retention-days','Local retention days (expiry label; no automatic deletion)','number']]){const label=document.createElement('label'),input=document.createElement('input');input.id=id;input.type=type;if(type==='number'){input.min=1;input.max=365;input.value=7;}label.append(caption,input);$('serial-port').parentElement.after(label);}
