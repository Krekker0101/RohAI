import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
const source=readFileSync(new URL('./overlay.js',import.meta.url),'utf8');
const {overlayState,liveGeometry,realTracks,frameTracks}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
const valid=()=>({camera_connection:'CONNECTED',calibration_status:'VALID',preflight:{calibration_valid:true,calibration_complete:true},overlay_calibration:{state:'CALIBRATED',source_id:'live',geometry:{source_id:'live',reference_width:1280,reference_height:720}}});
test('tracks require the exact analyzed frame and current epoch',()=>{
 const track={bbox:{x1:1,y1:1,x2:5,y2:5},visible:true};
 const s={camera_connection:'CONNECTED',tracks:[track],vision_state:{fresh:true,frame_id:7,stream_epoch:2,frame_width:640,frame_height:480}};
 const f={id:7,epoch:2,width:640,height:480};
 assert.deepEqual(frameTracks(s,f),[track]);
 for(const change of [{id:8},{epoch:1},{width:1280}])assert.deepEqual(frameTracks(s,{...f,...change}),[]);
 assert.deepEqual(frameTracks({...s,vision_state:{...s.vision_state,fresh:false}},f),[]);
});
test('no calibration and template config never supply active geometry',()=>{
  assert.equal(liveGeometry({},1280,720),null);
  assert.equal(liveGeometry({geometry:valid().overlay_calibration.geometry},1280,720),null);
  assert.equal(overlayState({},1280,720),'NO_CALIBRATION');
});
for(const [name,change] of [
  ['wrong camera',s=>s.overlay_calibration.geometry.source_id='other'],
  ['wrong resolution',s=>s.overlay_calibration.geometry.reference_width=640],
  ['invalid pose',s=>s.calibration_status='SUSPECT_CAMERA_MOVED'],
  ['incomplete',s=>s.preflight.calibration_complete=false],
  ['revalidation pending',s=>s.camera_revalidation_required=true],
  ['disconnected',s=>s.camera_connection='DISCONNECTED'],
])test(name+' hides geometry',()=>{const s=valid();change(s);assert.equal(liveGeometry(s,1280,720),null);assert.equal(overlayState(s,1280,720),'CALIBRATION_INVALID');});
test('verified saved calibration can render; editor is explicit',()=>{
  const s=valid();assert.equal(liveGeometry(s,1280,720),s.overlay_calibration.geometry);
  assert.equal(overlayState({},1280,720,true),'CALIBRATING');
  assert.equal(overlayState({},1280,720,true,true),'NO_CALIBRATION');
});
test('real detections are independent and never generated from geometry',()=>{
  const t={bbox:{x1:1,y1:2,x2:30,y2:40},track_id:7};
  assert.deepEqual(realTracks({camera_connection:'CONNECTED',tracks:[t]}),[t]);
  assert.deepEqual(realTracks(valid()),[]);
  assert.deepEqual(realTracks({camera_connection:'CONNECTED',tracks:[{bbox:{}}]}),[]);
});
test('simple and presentation default to clean geometry, with no automatic editor',()=>{
  const html=readFileSync(new URL('./simple.html',import.meta.url),'utf8');
  const js=readFileSync(new URL('./simple.js',import.meta.url),'utf8');
  assert.doesNotMatch(html,/id="zones"[^>]*checked/);
  assert.doesNotMatch(html,/id="geometry-toggle"[^>]*checked/);
  assert.doesNotMatch(js,/calibration_complete===false[^\n]*showRoad/);
  assert.match(js,/!presentation\?liveGeometry/);
});
