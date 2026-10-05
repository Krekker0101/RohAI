// Browser contracts with explicit fixtures. No camera/ESP32 validation claim.
import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

test('Expired token and backend loss produce actionable connection errors', async ({page})=>{
  const root=resolve(process.cwd(),'../..');
  let offline=false;
  await page.route('**/*',async route=>{
    const path=new URL(route.request().url()).pathname;
    if(path==='/maket')return route.fulfill({contentType:'text/html',body:readFileSync(resolve(root,'apps/maket-ui/index.html'))});
    if(path.startsWith('/maket-assets/'))return route.fulfill({contentType:path.endsWith('.js')?'text/javascript':'text/css',body:readFileSync(resolve(root,'apps/maket-ui',path.split('/').at(-1)!))});
    expect(route.request().headers()['x-operator-token']).toBe('fixture-token');
    if(offline)return route.abort('connectionrefused');
    return route.fulfill({status:401,body:'Unauthorized'});
  });
  await page.goto('http://127.0.0.1:8000/maket');
  await page.locator('#token').fill(' fixture-token ');
  await page.locator('#connect').click();
  await expect(page.locator('#error')).toContainText('token rejected');
  offline=true;
  await page.locator('#connect').click();
  await expect(page.locator('#error')).toContainText('Backend connection lost');
});

test('Maket console shows unknown measurements, preflight failures and shadow requests', async ({ page }) => {
  const root=resolve(process.cwd(),'../..');
  const calls:unknown[]=[];
  const config=JSON.parse(readFileSync(resolve(root,'configs/maket/maket.json'),'utf8'));
  const geometry=JSON.parse(readFileSync(resolve(root,'configs/maket/geometry-draft.json'),'utf8'));
  const state={state:'SAFE',running:false,session_id:'BROWSER_FIXTURE',failure:null,policy:'ADAPTIVE',policy_mode:'SHADOW',pipeline:{state:'running'},calibration_status:'NOT_CHECKED',traffic:{directions:{north:{car_count:null,queue_count:null,max_wait_seconds:null,average_wait_seconds:null,observation:{measurement_confidence:0}}}},preflight:{model_compatible:false,esp32_connected:false},hardware:{connected:false},recording:{dropped:0},tracks:[],proposed:null,approved_state:'ALL_RED'};
  await page.route('**/*', async route=>{
    const path=new URL(route.request().url()).pathname;
    if(path==='/maket')return route.fulfill({contentType:'text/html',body:readFileSync(resolve(root,'apps/maket-ui/index.html'))});
    if(path.startsWith('/maket-assets/'))return route.fulfill({contentType:path.endsWith('.js')?'text/javascript':'text/css',body:readFileSync(resolve(root,'apps/maket-ui',path.split('/').at(-1)!))});
    if(path==='/api/maket/frame')return route.fulfill({status:503,body:'fixture: no live frame'});
    if(path==='/api/maket/control'){calls.push(route.request().postDataJSON());return route.fulfill({json:state});}
    return route.fulfill({json:path.endsWith('/config')?config:path.endsWith('/calibration-draft')?geometry:state});
  });
  await page.goto('http://127.0.0.1:8000/maket');
  await page.locator('#token').fill('unit-fixture-operator');
  await page.locator('#connect').click();
  await expect(page.locator('#directions')).toContainText('Queue: unknown');
  await expect(page.locator('#preflight')).toContainText('FAIL · esp32_connected');
  await expect(page.locator('#decision')).toContainText('unavailable');
  await page.locator('#apply-policy').click();
  await expect.poll(()=>calls.length).toBe(1);
  expect(calls[0]).toEqual({action:'policy',policy:'ADAPTIVE',policy_mode:'SHADOW'});
  await page.locator('#stop').click();
  await expect.poll(()=>calls.length).toBe(2);
  expect(calls[1]).toEqual({action:'estop'});
});

test('Laptop wizard selects the sole camera and starts shadow without ESP32', async ({page})=>{
  const root=resolve(process.cwd(),'../..');
  let config=JSON.parse(readFileSync(resolve(root,'configs/maket/maket.json'),'utf8'));
  const geometry=JSON.parse(readFileSync(resolve(root,'configs/maket/geometry-draft.json'),'utf8'));
  const calls:Record<string,unknown>[]=[];
  const state={state:'CAMERA_ONLY',output_mode:'CAMERA_ONLY',demo_running:false,running:false,session_id:'LAPTOP_FIXTURE',failure:null,camera_error:null,camera_connection:'CONNECTED',vision_profile:'COCO_BASELINE',policy:'ADAPTIVE',policy_mode:'SHADOW',pipeline:{state:'running'},calibration_status:'VALID',readiness:{VISION_READY:true,CONTROL_READY:true,HARDWARE_READY:false},traffic:{directions:{}},preflight:{esp32_connected:false},hardware:{connected:false},recording:{dropped:0},tracks:[],proposed:null};
  const camera={index:3,available:true,friendly_name:'Integrated Camera (fixture)',backend:'MSMF',width:1280,height:720,fps:30,preview:null};
  await page.route('**/*',async route=>{
    const path=new URL(route.request().url()).pathname;
    if(path==='/maket')return route.fulfill({contentType:'text/html',body:readFileSync(resolve(root,'apps/maket-ui/index.html'))});
    if(path.startsWith('/maket-assets/'))return route.fulfill({contentType:path.endsWith('.js')?'text/javascript':'text/css',body:readFileSync(resolve(root,'apps/maket-ui',path.split('/').at(-1)!))});
    if(path==='/api/maket/frame')return route.fulfill({status:503,body:'fixture no live frame'});
    if(path.endsWith('/discovery'))return route.fulfill({json:{cameras:[camera],serial_ports:[],suggested_index:3,error:null}});
    if(path.endsWith('/camera-test'))return route.fulfill({json:camera});
    if(path.endsWith('/control')){const body=route.request().postDataJSON();calls.push(body);if(body.action==='camera_only_start')state.demo_running=true;return route.fulfill({json:state});}
    if(path.endsWith('/config')&&route.request().method()==='PUT'){config=route.request().postDataJSON();return route.fulfill({json:{status:'RELOADED'}});}
    return route.fulfill({json:path.endsWith('/config')?config:path.endsWith('/calibration-draft')?geometry:state});
  });
  await page.goto('http://127.0.0.1:8000/maket');
  await page.locator('#token').fill('fixture-operator');
  await page.locator('#connect').click();
  await page.locator('#discover').click();
  await expect(page.locator('#camera-choice')).toHaveValue('3');
  await expect(page.locator('#camera-choice')).toContainText('Integrated Camera');
  await page.locator('#camera-test').click();
  await expect(page.locator('#camera-test-result')).toContainText('AVAILABLE');
  await page.locator('#camera-select').click();
  await expect.poll(()=>config.camera_selected).toBe(true);
  expect(config.camera.webcam_index).toBe(3);
  expect(config.camera.camera_backend).toBe('MSMF');
  expect(config.output_mode).toBe('CAMERA_ONLY');
  expect(calls).toHaveLength(0); // Saving/discovery never arms control.
  await page.locator('#camera-only-start').click();
  await expect.poll(()=>calls.length).toBe(1);
  expect(calls[0]).toEqual({action:'camera_only_start'});
  await expect(page.locator('#subsystem-status')).toContainText('CONTROL: SHADOW');
  await expect(page.locator('#subsystem-status')).toContainText('ESP32: NOT CONNECTED');
  await expect(page.locator('#model-status')).toContainText('TOY ACCURACY: NOT VALIDATED');
});
