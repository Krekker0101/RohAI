# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: maket-simple.spec.ts >> First run and second run use automatic session, camera and one shadow Start
- Location: tests\maket-simple.spec.ts:48:1

# Error details

```
Error: expect(locator).toContainText(expected) failed

Locator: locator('#camera-name')
Expected substring: "Integrated Camera"
Received string:    "Выбранная камера"
Timeout: 5000ms

Call log:
  - Expect "toContainText" with timeout 5000ms
  - waiting for locator('#camera-name')
    14 × locator resolved to <span id="camera-name">Выбранная камера</span>
       - unexpected value "Выбранная камера"

```

```yaml
- text: Выбранная камера
```

# Test source

```ts
  1   | // Browser UX contracts with synthetic camera/state fixtures, not hardware validation.
  2   | import { test, expect, type Page } from '@playwright/test';
  3   | import { readFileSync, mkdirSync, writeFileSync } from 'node:fs';
  4   | import { resolve } from 'node:path';
  5   | 
  6   | const root=resolve(process.cwd(),'../..');
  7   | const evidence=resolve(root,'artifacts/maket-validation/simple-ux');
  8   | mkdirSync(evidence,{recursive:true});
  9   | 
  10  | async function fixture(page:Page, cameraCount=1){
  11  |   let config=JSON.parse(readFileSync(resolve(root,'configs/maket/maket.json'),'utf8'));
  12  |   let geometry=JSON.parse(readFileSync(resolve(root,'configs/maket/geometry-draft.json'),'utf8'));
  13  |   const calls:Record<string,unknown>[]=[];
  14  |   const state={state:'CAMERA_ONLY',output_mode:'CAMERA_ONLY',running:false,demo_running:false,session_id:'SYNTHETIC',camera_connection:'NOT_SELECTED',camera_revalidation_required:false,calibration_status:'NOT_CHECKED',vision_profile:'COCO_BASELINE',policy:'ADAPTIVE',policy_mode:'SHADOW',pipeline:{state:'starting'},hardware:{connected:false},recording:{dropped:0},readiness:{VISION_READY:false,CONTROL_READY:false,HARDWARE_READY:false},preflight:{model_compatible:true,calibration_valid:false,calibration_complete:false},traffic:{directions:Object.fromEntries(['north','south','east','west'].map((d,i)=>[d,{car_count:i+1,queue_count:i,max_wait_seconds:5+i,average_wait_seconds:2+i}]))},proposed:null as null|{phase:string,green_seconds:number},tracks:[],virtual_signal_state:null as null|string,maket_metrics:{stop_line_departures_per_minute:3}};
  15  |   await page.route('**/*',async route=>{
  16  |     const request=route.request(),path=new URL(request.url()).pathname;
  17  |     if(path==='/maket'||path==='/maket/engineer')return route.fulfill({contentType:'text/html',body:readFileSync(resolve(root,'apps/maket-ui',path.endsWith('engineer')?'index.html':'simple.html'))});
  18  |     if(path.startsWith('/maket-assets/'))return route.fulfill({contentType:path.endsWith('.js')?'text/javascript':'text/css',body:readFileSync(resolve(root,'apps/maket-ui',path.split('/').at(-1)!))});
  19  |     if(path.endsWith('/session'))return route.fulfill({status:204});
  20  |     expect(request.headers()['x-operator-token']??'').toBe('');
  21  |     if(path.endsWith('/frame'))return route.fulfill({contentType:'image/jpeg',body:readFileSync(resolve(root,'artifacts/maket-validation/simple-fixture.jpg'))});
  22  |     if(path.endsWith('/discovery'))return route.fulfill({json:{cameras:Array.from({length:cameraCount},(_,i)=>({index:i+3,backend:'MSMF',available:true,friendly_name:'Integrated Camera '+(i+1),preview:readFileSync(resolve(root,'artifacts/maket-validation/simple-fixture.jpg')).toString('base64')})),serial_ports:[]}});
  23  |     if(path.endsWith('/config')&&request.method()==='PUT'){
  24  |       config=request.postDataJSON();state.camera_connection='CONNECTED';state.pipeline.state='running';state.readiness.VISION_READY=true;
  25  |       return route.fulfill({json:{status:'SAVED'}});
  26  |     }
  27  |     if(path.endsWith('/calibration-suggestion'))return route.fulfill({json:{available:true,verified:false,geometry}});
  28  |     if(path.endsWith('/calibration')&&request.method()==='PUT'){
  29  |       const c=request.postDataJSON();calls.push({calibration:c});geometry=c.geometry;
  30  |       writeFileSync(resolve(evidence,'graphical-calibration-fixture.json'),JSON.stringify(c,null,2));
  31  |       state.preflight.calibration_complete=state.preflight.calibration_valid=true;
  32  |       state.readiness.CONTROL_READY=true;state.calibration_status='VALID';
  33  |       return route.fulfill({json:{status:'SAVED'}});
  34  |     }
  35  |     if(path.endsWith('/control')){
  36  |       const body=request.postDataJSON();calls.push(body);
  37  |       expect(['camera_only_start','stop','reconnect_camera']).toContain(body.action);
  38  |       state.demo_running=body.action==='camera_only_start';
  39  |       state.proposed=state.demo_running?{phase:'east_west',green_seconds:18}:null;
  40  |       state.virtual_signal_state=state.demo_running?'ALL_RED':null;
  41  |       return route.fulfill({json:state});
  42  |     }
  43  |     return route.fulfill({json:path.endsWith('/config')?config:path.endsWith('/geometry')?geometry:state});
  44  |   });
  45  |   return {calls,state,getConfig:()=>config};
  46  | }
  47  | 
  48  | test('First run and second run use automatic session, camera and one shadow Start',async({page})=>{
  49  |   await page.setViewportSize({width:1366,height:768});
  50  |   const f=await fixture(page);
  51  |   await page.goto('/maket');
> 52  |   await expect(page.locator('#camera-name')).toContainText('Integrated Camera');
      |                                              ^ Error: expect(locator).toContainText(expected) failed
  53  |   await expect(page.locator('#empty')).toBeHidden();
  54  |   expect(f.getConfig().camera_selected).toBe(true);
  55  |   expect(f.calls).toHaveLength(0);
  56  |   await expect(page.locator('#main-action')).toBeDisabled();
  57  |   await expect(page.locator('#token')).toHaveCount(0);
  58  |   await page.locator('#suggest').click();
  59  |   await expect(page.locator('#notice')).toContainText('Проверьте');
  60  |   await expect(page.locator('#save-calibration')).toBeDisabled();
  61  |   await page.locator('#field').selectOption('detection_zone');
  62  |   await page.locator('#draw-new').click();
  63  |   const box=(await page.locator('#video').boundingBox())!;
  64  |   for(const [x,y] of [[.02,.02],[.98,.02],[.98,.98],[.02,.98]])await page.mouse.click(box.x+box.width*x,box.y+box.height*y);
  65  |   await page.locator('#commit').click();
  66  |   await page.locator('#verified').check();
  67  |   await page.locator('#save-calibration').click();
  68  |   await expect(page.locator('#main-action')).toBeEnabled();
  69  |   await page.locator('#main-action').click();
  70  |   await expect(page.locator('#main-action')).toHaveText('ОСТАНОВИТЬ');
  71  |   await expect(page.locator('#cars')).toHaveText('10');
  72  |   await expect(page.locator('#recommendation')).toHaveText('ВОСТОК ↔ ЗАПАД');
  73  |   await expect(page.locator('#physical')).toContainText('не подключены');
  74  |   await expect(page.locator('#lights')).toHaveAttribute('aria-label','Виртуальное состояние ALL_RED');
  75  |   await page.screenshot({path:resolve(evidence,'simple-1366.png'),fullPage:true});
  76  |   await page.setViewportSize({width:1920,height:1080});
  77  |   await page.locator('#presentation').click();
  78  |   await expect(page.locator('body')).toHaveClass(/present/);
  79  |   await expect(page.locator('#wizard')).toBeHidden();
  80  |   await expect(page.locator('nav a')).toBeHidden();
  81  |   await page.screenshot({path:resolve(evidence,'presentation-1920.png'),fullPage:true});
  82  |   await page.locator('#presentation').click();
  83  |   await page.locator('#main-action').click();
  84  |   await expect(page.locator('#main-action')).toHaveText('НАЧАТЬ');
  85  |   const count=f.calls.length;
  86  |   await page.reload();
  87  |   await expect(page.locator('#main-action')).toBeEnabled();
  88  |   await expect(page.locator('#wizard')).toBeHidden();
  89  |   expect(f.calls).toHaveLength(count); // No auto control or recalibration on second run.
  90  |   await page.locator('#main-action').click();
  91  |   expect(f.calls.at(-1)).toEqual({action:'camera_only_start'});
  92  |   expect(await page.evaluate(()=>Object.keys(localStorage))).not.toContain('token');
  93  | });
  94  | 
  95  | test('Multiple cameras require a thumbnail choice; absent camera has a useful retry',async({page})=>{
  96  |   const f=await fixture(page,2);
  97  |   await page.goto('/maket');
  98  |   await expect(page.locator('#candidates button')).toHaveCount(2);
  99  |   await expect(page.locator('#candidates img')).toHaveCount(2);
  100 |   expect(f.getConfig().camera_selected).toBe(false);
  101 |   await page.locator('#candidates button').nth(1).click();
  102 |   await expect(page.locator('#camera-name')).toContainText('Camera 2');
  103 |   await page.goto('/maket/engineer');
  104 |   await expect(page.locator('#connection')).toContainText('STOPPED');
  105 |   await page.locator('#presentation').click();
  106 |   await expect(page).toHaveURL(/\/maket$/);
  107 |   await expect(page.locator('body')).toHaveClass(/present/);
  108 |   await expect(page.locator('#token')).toHaveCount(0);
  109 | });
  110 | 
  111 | test('No camera and moved camera do not enable Start or physical outputs',async({page})=>{
  112 |   const f=await fixture(page,0);
  113 |   await page.goto('/maket');
  114 |   await expect(page.locator('#notice')).toContainText('Камера недоступна');
  115 |   await expect(page.locator('#retry')).toBeVisible();
  116 |   await expect(page.locator('#main-action')).toBeDisabled();
  117 |   await expect.poll(()=>page.locator('#video').evaluate((node:HTMLCanvasElement)=>
  118 |     node.getContext('2d')!.getImageData(0,0,node.width,node.height).data.some(v=>v!==0)
  119 |   )).toBe(false); // No draft zones, fake detections or stale tracks on an empty canvas.
  120 |   f.state.camera_connection='CONNECTED';f.state.preflight.calibration_complete=true;
  121 |   f.state.camera_revalidation_required=true;f.state.calibration_status='SUSPECT_SHIFT';
  122 |   await expect(page.locator('#ready')).toContainText('Положение камеры');
  123 |   await expect(page.locator('#calibration-open')).toBeVisible();
  124 |   await expect(page.locator('#main-action')).toBeDisabled();
  125 |   expect(f.calls).toHaveLength(0);
  126 | });
  127 | 
```