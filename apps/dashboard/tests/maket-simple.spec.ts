// Browser UX contracts with synthetic camera/state fixtures, not hardware validation.
import { test, expect, type Page } from '@playwright/test';
import { readFileSync, mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';

const root=resolve(process.cwd(),'../..');
const evidence=process.env.ROHAI_BROWSER_ARTIFACTS
  ? resolve(process.env.ROHAI_BROWSER_ARTIFACTS, 'simple-ux')
  : resolve(root,'artifacts/maket-validation/simple-ux');
mkdirSync(evidence,{recursive:true});

async function fixture(page:Page, cameraCount=1){
  let config=JSON.parse(readFileSync(resolve(root,'configs/maket/maket.json'),'utf8'));
  config.camera_selected=false; // Independent of the user's persisted camera selection.
  let geometry=JSON.parse(readFileSync(resolve(root,'configs/maket/geometry-draft.json'),'utf8'));
  const calls:Record<string,unknown>[]=[];
  const state={state:'CAMERA_ONLY',output_mode:'CAMERA_ONLY',running:false,demo_running:false,session_id:'SYNTHETIC',camera_connection:'NOT_SELECTED',camera_revalidation_required:false,calibration_status:'NOT_CHECKED',vision_profile:'COCO_BASELINE',policy:'ADAPTIVE',policy_mode:'SHADOW',pipeline:{state:'starting'},hardware:{connected:false},recording:{dropped:0},readiness:{VISION_READY:false,CONTROL_READY:false,HARDWARE_READY:false},preflight:{model_compatible:true,calibration_valid:false,calibration_complete:false},traffic:{directions:Object.fromEntries(['north','south','east','west'].map((d,i)=>[d,{car_count:i+1,queue_count:i,max_wait_seconds:5+i,average_wait_seconds:2+i}]))},proposed:null as null|{phase:string,green_seconds:number},tracks:[],virtual_signal_state:null as null|string,maket_metrics:{stop_line_departures_per_minute:3}};
  await page.route('**/*',async route=>{
    const request=route.request(),path=new URL(request.url()).pathname;
    if(path==='/maket'||path==='/maket/engineer')return route.fulfill({contentType:'text/html',body:readFileSync(resolve(root,'apps/maket-ui',path.endsWith('engineer')?'index.html':'simple.html'))});
    if(path.startsWith('/maket-assets/'))return route.fulfill({contentType:path.endsWith('.js')?'text/javascript':'text/css',body:readFileSync(resolve(root,'apps/maket-ui',path.split('/').at(-1)!))});
    if(path.endsWith('/permissions'))return route.fulfill({json:{setup_authorized:true}});
    if(path.endsWith('/session'))return route.fulfill({status:204});
    expect(request.headers()['x-operator-token']??'').toBe('');
    if(path.endsWith('/frame'))return route.fulfill({contentType:'image/jpeg',body:readFileSync(resolve(root,'artifacts/maket-validation/simple-fixture.jpg'))});
    if(path.endsWith('/discovery'))return route.fulfill({json:{cameras:Array.from({length:cameraCount},(_,i)=>({index:i+3,backend:'MSMF',available:true,friendly_name:'Integrated Camera '+(i+1),preview:readFileSync(resolve(root,'artifacts/maket-validation/simple-fixture.jpg')).toString('base64')})),serial_ports:[]}});
    if(path.endsWith('/config')&&request.method()==='PUT'){
      config=request.postDataJSON();state.camera_connection='CONNECTED';state.pipeline.state='running';state.readiness.VISION_READY=true;
      return route.fulfill({json:{status:'SAVED'}});
    }
    if(path.endsWith('/calibration-suggestion'))return route.fulfill({json:{available:true,verified:false,geometry}});
    if(path.endsWith('/calibration')&&request.method()==='PUT'){
      const c=request.postDataJSON();calls.push({calibration:c});geometry=c.geometry;
      writeFileSync(resolve(evidence,'graphical-calibration-fixture.json'),JSON.stringify(c,null,2));
      state.preflight.calibration_complete=state.preflight.calibration_valid=true;
      state.readiness.CONTROL_READY=true;state.calibration_status='VALID';
      return route.fulfill({json:{status:'SAVED'}});
    }
    if(path.endsWith('/control')){
      const body=request.postDataJSON();calls.push(body);
      expect(['camera_only_start','stop','reconnect_camera']).toContain(body.action);
      state.demo_running=body.action==='camera_only_start';
      state.proposed=state.demo_running?{phase:'east_west',green_seconds:18}:null;
      state.virtual_signal_state=state.demo_running?'ALL_RED':null;
      return route.fulfill({json:state});
    }
    return route.fulfill({json:path.endsWith('/config')?config:path.endsWith('/calibration-draft')?geometry:state});
  });
  return {calls,state,getConfig:()=>config};
}

test('First run and second run use automatic session, camera and one shadow Start',async({page})=>{
  await page.setViewportSize({width:1366,height:768});
  const f=await fixture(page);
  await page.goto('/maket');
  await expect(page.locator('#camera-name')).toContainText('Integrated Camera');
  await expect(page.locator('#empty')).toBeHidden();
  expect(f.getConfig().camera_selected).toBe(true);
  expect(f.calls).toHaveLength(0);
  await expect(page.locator('#main-action')).toBeDisabled();
  await expect(page.locator('#token')).toHaveCount(0);
  await page.locator('#calibration-open').click();
  await page.locator('#suggest').click();
  await expect(page.locator('#notice')).toContainText('Проверьте');
  await expect(page.locator('#save-calibration')).toBeDisabled();
  await page.locator('#field').selectOption('detection_zone');
  await page.locator('#draw-new').click();
  const box=(await page.locator('#video').boundingBox())!;
  for(const [x,y] of [[.02,.02],[.98,.02],[.98,.98],[.02,.98]])await page.mouse.click(box.x+box.width*x,box.y+box.height*y);
  await page.locator('#commit').click();
  await page.locator('#verified').check();
  await page.locator('#save-calibration').click();
  await expect(page.locator('#main-action')).toBeEnabled();
  await page.locator('#main-action').click();
  await expect(page.locator('#main-action')).toHaveText('ОСТАНОВИТЬ');
  await expect(page.locator('#cars')).toHaveText('10');
  await expect(page.locator('#recommendation')).toHaveText('ВОСТОК ↔ ЗАПАД');
  await expect(page.locator('#physical')).toContainText('не подключены');
  await expect(page.locator('#lights')).toHaveAttribute('aria-label','Виртуальное состояние ALL_RED');
  await page.screenshot({path:resolve(evidence,'simple-1366.png'),fullPage:true});
  await page.setViewportSize({width:1920,height:1080});
  await page.locator('#presentation').click();
  await expect(page.locator('body')).toHaveClass(/present/);
  await expect(page.locator('#wizard')).toBeHidden();
  await expect(page.locator('a[href="/maket/engineer"]')).toBeHidden();
  await page.screenshot({path:resolve(evidence,'presentation-1920.png'),fullPage:true});
  await page.locator('#presentation').click();
  await page.locator('#main-action').click();
  await expect(page.locator('#main-action')).toHaveText('НАЧАТЬ');
  const count=f.calls.length;
  await page.reload();
  await expect(page.locator('#main-action')).toBeEnabled();
  await expect(page.locator('#wizard')).toBeHidden();
  expect(f.calls).toHaveLength(count); // No auto control or recalibration on second run.
  await page.locator('#main-action').click();
  await expect.poll(() => f.calls.at(-1)).toEqual({action:'camera_only_start'});
  expect(await page.evaluate(()=>Object.keys(localStorage))).not.toContain('token');
});

test('Multiple cameras require a thumbnail choice; absent camera has a useful retry',async({page})=>{
  const f=await fixture(page,2);
  await page.goto('/maket');
  await expect(page.locator('#candidates button')).toHaveCount(2);
  await expect(page.locator('#candidates img')).toHaveCount(2);
  expect(f.getConfig().camera_selected).toBe(false);
  await page.locator('#candidates button').nth(1).click();
  await expect(page.locator('#camera-name')).toContainText('Camera 2');
  await page.goto('/maket/engineer');
  await expect(page.locator('#connection')).toContainText('STOPPED');
  await page.locator('#presentation').click();
  await expect(page).toHaveURL(/\/maket$/);
  await expect(page.locator('body')).toHaveClass(/present/);
  await expect(page.locator('#token')).toHaveCount(0);
});

test('No camera and moved camera do not enable Start or physical outputs',async({page})=>{
  const f=await fixture(page,0);
  await page.goto('/maket');
  await expect(page.locator('#notice')).toContainText('Камера недоступна');
  await expect(page.locator('#retry')).toBeVisible();
  await expect(page.locator('#main-action')).toBeDisabled();
  await expect.poll(()=>page.locator('#video').evaluate((node:HTMLCanvasElement)=>
    node.getContext('2d')!.getImageData(0,0,node.width,node.height).data.some(v=>v!==0)
  )).toBe(false); // No draft zones, fake detections or stale tracks on an empty canvas.
  f.state.camera_connection='CONNECTED';f.state.preflight.calibration_complete=true;
  f.state.camera_revalidation_required=true;f.state.calibration_status='SUSPECT_SHIFT';
  await expect(page.locator('#ready')).toContainText('Положение камеры');
  await expect(page.locator('#calibration-open')).toBeVisible();
  await expect(page.locator('#main-action')).toBeDisabled();
  f.state.camera_revalidation_required=false;f.state.calibration_status='VALID';
  f.state.output_mode='PHYSICAL_OUTPUT';f.state.readiness.CONTROL_READY=true;f.state.readiness.VISION_READY=true;
  await expect(page.locator('#ready')).toContainText('аппаратные проверки не пройдены');
  await expect(page.locator('#main-action')).toBeDisabled();
  expect(f.calls).toHaveLength(0);
});


test('Live template geometry stays hidden until explicit calibration editor',async({page})=>{
  await fixture(page);
  await page.addInitScript(()=>{
    const calls:string[]=[];
    (window as unknown as {overlayCalls:string[]}).overlayCalls=calls;
    for(const name of ['stroke','strokeRect'] as const){
      const original=CanvasRenderingContext2D.prototype[name];
      CanvasRenderingContext2D.prototype[name]=function(...args:unknown[]){
        calls.push(name);
        return (original as (...args:unknown[])=>void).apply(this,args);
      };
    }
  });
  await page.goto('/maket');
  await expect(page.locator('#empty')).toBeHidden();
  await expect(page.locator('#zones')).toBeDisabled();
  await expect(page.locator('#geometry-toggle')).toBeDisabled();
  await expect(page.locator('#calibration-message')).toContainText('Калибровка макета не выполнена');
  expect(await page.evaluate(()=>(window as unknown as {overlayCalls:string[]}).overlayCalls)).toEqual([]);
  await page.locator('#calibration-open').click();
  await expect(page.locator('#road-tools')).toBeVisible();
  expect(await page.evaluate(()=>(window as unknown as {overlayCalls:string[]}).overlayCalls.length)).toBeGreaterThan(0);
  await page.locator('#presentation').click();
  await page.evaluate(()=>(window as unknown as {overlayCalls:string[]}).overlayCalls.length=0);
  await page.waitForTimeout(600);
  expect(await page.evaluate(()=>(window as unknown as {overlayCalls:string[]}).overlayCalls)).toEqual([]);
});
