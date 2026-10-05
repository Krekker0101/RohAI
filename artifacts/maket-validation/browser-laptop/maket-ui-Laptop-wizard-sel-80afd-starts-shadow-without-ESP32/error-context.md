# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: maket-ui.spec.ts >> Laptop wizard selects the sole camera and starts shadow without ESP32
- Location: tests\maket-ui.spec.ts:34:1

# Error details

```
Error: expect(locator).toContainText(expected) failed

Locator: locator('#subsystem-status')
Expected substring: "CONTROL: SHADOW"
Received string:    ""
Timeout: 5000ms

Call log:
  - Expect "toContainText" with timeout 5000ms
  - waiting for locator('#subsystem-status')
    14 × locator resolved to <div id="subsystem-status"></div>
       - unexpected value ""

```

```yaml
- banner:
  - text: RohAI / PHYSICAL MAKET CAMERA_ONLY · STOPPED · LAPTOP_FIXTURE
  - button "Presentation"
- main:
  - text: Operator token
  - textbox "Operator token": fixture-operator
  - button "Connect console"
  - button "EMERGENCY ALL RED"
  - button "Start Camera Only / Shadow"
  - button "Start / Preflight + local rearm"
  - button "Stop control"
  - button "Reconnect ESP32"
  - button "Restart camera (stopped)"
  - button "Revalidate camera pose (stopped)"
  - button "Record video"
  - text: "VISION MODEL: COCO BASELINE ? TOY ACCURACY: NOT VALIDATED"
  - alert
  - text: LIVE CAMERA running · VALID
  - checkbox "Detections" [checked]
  - text: Detections
  - checkbox "Tracking" [checked]
  - text: Tracking
  - checkbox "Geometry"
  - text: Geometry
  - checkbox "Queues" [checked]
  - text: Queues
  - checkbox "Debug"
  - text: Debug
  - checkbox "AI Decision" [checked]
  - text: "AI Decision AI recommendation: unavailable / — s (SHADOW) Inputs: . Fallback: none. Measured baseline inputs; no generated narrative. Throughput: ? stop crossings/min. Capture ? FPS · Inference ? FPS · Analytics ? FPS · Control ? Hz · camera→ACK p50/p95 ? / ? ms. ACK ≠ optical lamp proof. Vision latency p95 ? ms. Dropped capture 0 / inference 0. Recording drops 0."
  - complementary:
    - heading "Control" [level=2]
    - text: Policy
    - combobox "Policy":
      - option "ADAPTIVE" [selected]
      - option "FIXED"
      - option "LEARNED_AI"
    - text: Mode
    - combobox "Mode":
      - option "SHADOW" [selected]
      - option "ACTIVE"
    - button "Apply while stopped"
    - heading "Preflight" [level=2]
    - text: PASS · VISION_READY PASS · CONTROL_READY FAIL · HARDWARE_READY FAIL · esp32_connected
    - heading "Hardware Test" [level=2]
    - paragraph: Phase requests use the supervisor. Yellow is an observed transition, not a GPIO bypass.
    - button "Arm Hardware Test (local button required)"
    - button "NS Green"
    - button "EW Green"
    - button "Pedestrian Green"
    - button "All Red"
    - button "Safe Reset"
    - button "NS Yellow (request clearance)"
    - button "EW Yellow (request clearance)"
    - text: "{ \"requested\": null, \"ESP32\": { \"connected\": false } }"
  - heading "Setup wizard" [level=2]
  - list:
    - listitem: "Discover and Select Camera: built-in laptop, optional USB or RTSP."
    - listitem: Confirm the live frame and camera identity.
    - listitem: Select COCO baseline (toy accuracy unmeasured) or a PHYSICAL_MAKET model/package.
    - listitem: Calibrate all zones and oriented lines below.
    - listitem: "Start Camera Only / Shadow without ESP32. Later, optional physical output: wire outputs and independent electrical readback."
    - listitem: Connect ESP32 and verify firmware timing.
    - listitem: Arm with local button; test each phase through supervisor.
    - listitem: Test E-stop and unplug watchdog; record HIL evidence.
    - listitem: Start SHADOW; review recorded observations/actions.
    - listitem: ACTIVE learned policy requires offline/shadow/stand approval.
  - text: Camera
  - combobox "Camera":
    - option "BUILTIN_CAMERA ? Integrated laptop camera" [selected]
    - option "USB_CAMERA"
    - option "RTSP"
    - option "VIDEO_FILE ? separate Replay" [disabled]
  - button "Discover cameras / Serial (stopped)"
  - text: Detected camera
  - combobox "Detected camera":
    - option "Select Camera"
    - option "Camera 3 - Integrated Camera (fixture) - 1280x720 - 30 FPS - MSMF - AVAILABLE" [selected]
  - text: Camera index
  - spinbutton "Camera index": "3"
  - button "Preview selected candidate"
  - button "Test selected camera"
  - button "Select + Save camera"
  - text: "Camera 3: AVAILABLE 1280x720 @ 30 FPS / MSMF. Discovery snapshot; Select + Save opens live feed. Backend"
  - combobox "Backend":
    - option "AUTO"
    - option "DSHOW"
    - option "MSMF" [selected]
    - option "ANY"
  - text: Preset
  - combobox "Preset":
    - option "LOW"
    - option "BALANCED" [selected]
    - option "HIGH"
  - text: Rotate
  - combobox "Rotate":
    - option "0" [selected]
    - option "90"
    - option "180"
    - option "270"
  - checkbox "Mirror"
  - text: Mirror Crop [x,y,width,height]
  - textbox "Crop [x,y,width,height]": "[0,0,1,1]"
  - text: Output
  - combobox "Output":
    - option "CAMERA_ONLY" [selected]
    - option "PHYSICAL_OUTPUT"
  - text: RTSP URI
  - textbox "RTSP URI": undefined
  - text: Vision profile
  - combobox "Vision profile":
    - option "COCO_BASELINE" [selected]
    - option "MAKET_MODEL"
  - text: Model package
  - textbox "Model package":
    - /placeholder: trusted local immutable package
  - text: Detected Serial
  - combobox "Detected Serial":
    - option "AUTO" [selected]
  - text: Serial port
  - textbox "Serial port":
    - /placeholder: AUTO
    - text: AUTO
  - button "Save configuration + reload stopped"
  - heading "Live calibration" [level=3]
  - paragraph: "Click normalized points on the camera. Polygon: ≥3 points then Commit. Line: start, end, upstream anchor. Coordinates are a draft until you draw and verify every region. Stop control first."
  - text: Direction
  - combobox "Direction":
    - option "north" [selected]
    - option "south"
    - option "east"
    - option "west"
  - text: Field
  - combobox "Field":
    - option "detection_zone" [selected]
    - option "incoming"
    - option "queue_zone"
    - option "lane"
    - option "stop_line"
    - option "entry_line"
    - option "exit_line"
    - option "waiting_zone"
    - option "crosswalk_zone"
    - option "exit_zone"
  - button "Commit points"
  - button "Clear points"
  - text: Intersection ID
  - textbox "Intersection ID": maket-1
  - text: Orientation °
  - spinbutton "Orientation °": "0"
  - text: Scale
  - combobox "Scale":
    - option "relative (no meters)" [selected]
    - option "model centimeters"
  - text: Width cm
  - spinbutton "Width cm"
  - text: Height cm
  - spinbutton "Height cm"
  - text: Normalized?model cm homography (optional JSON 3?3)
  - textbox "Normalized?model cm homography (optional JSON 3?3)":
    - /placeholder: "[[...],[...],[...]]"
  - text: Road equivalent ratio (optional)
  - spinbutton "Road equivalent ratio (optional)"
  - checkbox "I verified source, all geometry and fixed camera pose"
  - text: I verified source, all geometry and fixed camera pose
  - button "Save calibration + reference + restart camera"
  - text: "{ \"schema_version\": 1, \"source_id\": \"maket-camera\", \"reference_width\": 1280, \"reference_height\": 720, \"detection_zone\": { \"points\": [ { \"x\": 0, \"y\": 0 }, { \"x\": 1, \"y\": 0 }, { \"x\": 1, \"y\": 1 }, { \"x\": 0, \"y\": 1 } ] }, \"approaches\": [ { \"direction\": \"north\", \"incoming\": { \"points\": [ { \"x\": 0.36, \"y\": 0 }, { \"x\": 0.5, \"y\": 0 }, { \"x\": 0.5, \"y\": 0.36 }, { \"x\": 0.36, \"y\": 0.36 } ] }, \"queue_zone\": { \"points\": [ { \"x\": 0.37, \"y\": 0.03 }, { \"x\": 0.49, \"y\": 0.03 }, { \"x\": 0.49, \"y\": 0.35 }, { \"x\": 0.37, \"y\": 0.35 } ] }, \"stop_line\": { \"start\": { \"x\": 0.36, \"y\": 0.35 }, \"end\": { \"x\": 0.5, \"y\": 0.35 }, \"upstream\": { \"x\": 0.43, \"y\": 0.1 } }, \"pedestrian_crossing\": { \"points\": [ { \"x\": 0.36, \"y\": 0.36 }, { \"x\": 0.5, \"y\": 0.36 }, { \"x\": 0.5, \"y\": 0.4 }, { \"x\": 0.36, \"y\": 0.4 } ] } }, { \"direction\": \"south\", \"incoming\": { \"points\": [ { \"x\": 0.5, \"y\": 0.65 }, { \"x\": 0.65, \"y\": 0.65 }, { \"x\": 0.65, \"y\": 1 }, { \"x\": 0.5, \"y\": 1 } ] }, \"queue_zone\": { \"points\": [ { \"x\": 0.51, \"y\": 0.66 }, { \"x\": 0.64, \"y\": 0.66 }, { \"x\": 0.64, \"y\": 0.97 }, { \"x\": 0.51, \"y\": 0.97 } ] }, \"stop_line\": { \"start\": { \"x\": 0.5, \"y\": 0.65 }, \"end\": { \"x\": 0.65, \"y\": 0.65 }, \"upstream\": { \"x\": 0.57, \"y\": 0.9 } }, \"pedestrian_crossing\": { \"points\": [ { \"x\": 0.5, \"y\": 0.61 }, { \"x\": 0.65, \"y\": 0.61 }, { \"x\": 0.65, \"y\": 0.65 }, { \"x\": 0.5, \"y\": 0.65 } ] } }, { \"direction\": \"east\", \"incoming\": { \"points\": [ { \"x\": 0.65, \"y\": 0.35 }, { \"x\": 1, \"y\": 0.35 }, { \"x\": 1, \"y\": 0.5 }, { \"x\": 0.65, \"y\": 0.5 } ] }, \"queue_zone\": { \"points\": [ { \"x\": 0.66, \"y\": 0.36 }, { \"x\": 0.97, \"y\": 0.36 }, { \"x\": 0.97, \"y\": 0.49 }, { \"x\": 0.66, \"y\": 0.49 } ] }, \"stop_line\": { \"start\": { \"x\": 0.65, \"y\": 0.35 }, \"end\": { \"x\": 0.65, \"y\": 0.5 }, \"upstream\": { \"x\": 0.9, \"y\": 0.42 } }, \"pedestrian_crossing\": { \"points\": [ { \"x\": 0.61, \"y\": 0.35 }, { \"x\": 0.65, \"y\": 0.35 }, { \"x\": 0.65, \"y\": 0.5 }, { \"x\": 0.61, \"y\": 0.5 } ] } }, { \"direction\": \"west\", \"incoming\": { \"points\": [ { \"x\": 0, \"y\": 0.5 }, { \"x\": 0.36, \"y\": 0.5 }, { \"x\": 0.36, \"y\": 0.65 }, { \"x\": 0, \"y\": 0.65 } ] }, \"queue_zone\": { \"points\": [ { \"x\": 0.03, \"y\": 0.51 }, { \"x\": 0.35, \"y\": 0.51 }, { \"x\": 0.35, \"y\": 0.64 }, { \"x\": 0.03, \"y\": 0.64 } ] }, \"stop_line\": { \"start\": { \"x\": 0.36, \"y\": 0.5 }, \"end\": { \"x\": 0.36, \"y\": 0.65 }, \"upstream\": { \"x\": 0.1, \"y\": 0.57 } }, \"pedestrian_crossing\": { \"points\": [ { \"x\": 0.36, \"y\": 0.5 }, { \"x\": 0.4, \"y\": 0.5 }, { \"x\": 0.4, \"y\": 0.65 }, { \"x\": 0.36, \"y\": 0.65 } ] } } ] }"
```

# Test source

```ts
  1  | // Browser contracts with explicit fixtures. No camera/ESP32 validation claim.
  2  | import { test, expect } from '@playwright/test';
  3  | import { readFileSync } from 'node:fs';
  4  | import { resolve } from 'node:path';
  5  | 
  6  | test('Maket console shows unknown measurements, preflight failures and shadow requests', async ({ page }) => {
  7  |   const root=resolve(process.cwd(),'../..');
  8  |   const calls:unknown[]=[];
  9  |   const config=JSON.parse(readFileSync(resolve(root,'configs/maket/maket.json'),'utf8'));
  10 |   const geometry=JSON.parse(readFileSync(resolve(root,'configs/maket/geometry-draft.json'),'utf8'));
  11 |   const state={state:'SAFE',running:false,session_id:'BROWSER_FIXTURE',failure:null,policy:'ADAPTIVE',policy_mode:'SHADOW',pipeline:{state:'running'},calibration_status:'NOT_CHECKED',traffic:{directions:{north:{car_count:null,queue_count:null,max_wait_seconds:null,average_wait_seconds:null,observation:{measurement_confidence:0}}}},preflight:{model_compatible:false,esp32_connected:false},hardware:{connected:false},recording:{dropped:0},tracks:[],proposed:null,approved_state:'ALL_RED'};
  12 |   await page.route('**/*', async route=>{
  13 |     const path=new URL(route.request().url()).pathname;
  14 |     if(path==='/maket')return route.fulfill({contentType:'text/html',body:readFileSync(resolve(root,'apps/maket-ui/index.html'))});
  15 |     if(path.startsWith('/maket-assets/'))return route.fulfill({contentType:path.endsWith('.js')?'text/javascript':'text/css',body:readFileSync(resolve(root,'apps/maket-ui',path.split('/').at(-1)!))});
  16 |     if(path==='/api/maket/frame')return route.fulfill({status:503,body:'fixture: no live frame'});
  17 |     if(path==='/api/maket/control'){calls.push(route.request().postDataJSON());return route.fulfill({json:state});}
  18 |     return route.fulfill({json:path.endsWith('/config')?config:path.endsWith('/geometry')?geometry:state});
  19 |   });
  20 |   await page.goto('http://127.0.0.1:8000/maket');
  21 |   await page.locator('#token').fill('unit-fixture-operator');
  22 |   await page.locator('#connect').click();
  23 |   await expect(page.locator('#directions')).toContainText('Queue: unknown');
  24 |   await expect(page.locator('#preflight')).toContainText('FAIL · esp32_connected');
  25 |   await expect(page.locator('#decision')).toContainText('unavailable');
  26 |   await page.locator('#apply-policy').click();
  27 |   await expect.poll(()=>calls.length).toBe(1);
  28 |   expect(calls[0]).toEqual({action:'policy',policy:'ADAPTIVE',policy_mode:'SHADOW'});
  29 |   await page.locator('#stop').click();
  30 |   await expect.poll(()=>calls.length).toBe(2);
  31 |   expect(calls[1]).toEqual({action:'estop'});
  32 | });
  33 | 
  34 | test('Laptop wizard selects the sole camera and starts shadow without ESP32', async ({page})=>{
  35 |   const root=resolve(process.cwd(),'../..');
  36 |   let config=JSON.parse(readFileSync(resolve(root,'configs/maket/maket.json'),'utf8'));
  37 |   const geometry=JSON.parse(readFileSync(resolve(root,'configs/maket/geometry-draft.json'),'utf8'));
  38 |   const calls:Record<string,unknown>[]=[];
  39 |   const state={state:'CAMERA_ONLY',output_mode:'CAMERA_ONLY',demo_running:false,running:false,session_id:'LAPTOP_FIXTURE',failure:null,camera_error:null,camera_connection:'CONNECTED',vision_profile:'COCO_BASELINE',policy:'ADAPTIVE',policy_mode:'SHADOW',pipeline:{state:'running'},calibration_status:'VALID',readiness:{VISION_READY:true,CONTROL_READY:true,HARDWARE_READY:false},traffic:{directions:{}},preflight:{esp32_connected:false},hardware:{connected:false},recording:{dropped:0},tracks:[],proposed:null};
  40 |   const camera={index:3,available:true,friendly_name:'Integrated Camera (fixture)',backend:'MSMF',width:1280,height:720,fps:30,preview:null};
  41 |   await page.route('**/*',async route=>{
  42 |     const path=new URL(route.request().url()).pathname;
  43 |     if(path==='/maket')return route.fulfill({contentType:'text/html',body:readFileSync(resolve(root,'apps/maket-ui/index.html'))});
  44 |     if(path.startsWith('/maket-assets/'))return route.fulfill({contentType:path.endsWith('.js')?'text/javascript':'text/css',body:readFileSync(resolve(root,'apps/maket-ui',path.split('/').at(-1)!))});
  45 |     if(path==='/api/maket/frame')return route.fulfill({status:503,body:'fixture no live frame'});
  46 |     if(path.endsWith('/discovery'))return route.fulfill({json:{cameras:[camera],serial_ports:[],suggested_index:3,error:null}});
  47 |     if(path.endsWith('/camera-test'))return route.fulfill({json:camera});
  48 |     if(path.endsWith('/control')){const body=route.request().postDataJSON();calls.push(body);if(body.action==='camera_only_start')state.demo_running=true;return route.fulfill({json:state});}
  49 |     if(path.endsWith('/config')&&route.request().method()==='PUT'){config=route.request().postDataJSON();return route.fulfill({json:{status:'RELOADED'}});}
  50 |     return route.fulfill({json:path.endsWith('/config')?config:path.endsWith('/geometry')?geometry:state});
  51 |   });
  52 |   await page.goto('http://127.0.0.1:8000/maket');
  53 |   await page.locator('#token').fill('fixture-operator');
  54 |   await page.locator('#connect').click();
  55 |   await page.locator('#discover').click();
  56 |   await expect(page.locator('#camera-choice')).toHaveValue('3');
  57 |   await expect(page.locator('#camera-choice')).toContainText('Integrated Camera');
  58 |   await page.locator('#camera-test').click();
  59 |   await expect(page.locator('#camera-test-result')).toContainText('AVAILABLE');
  60 |   await page.locator('#camera-select').click();
  61 |   await expect.poll(()=>config.camera_selected).toBe(true);
  62 |   expect(config.camera.webcam_index).toBe(3);
  63 |   expect(config.camera.camera_backend).toBe('MSMF');
  64 |   expect(config.output_mode).toBe('CAMERA_ONLY');
  65 |   expect(calls).toHaveLength(0); // Saving/discovery never arms control.
  66 |   await page.locator('#camera-only-start').click();
  67 |   await expect.poll(()=>calls.length).toBe(1);
  68 |   expect(calls[0]).toEqual({action:'camera_only_start'});
> 69 |   await expect(page.locator('#subsystem-status')).toContainText('CONTROL: SHADOW');
     |                                                   ^ Error: expect(locator).toContainText(expected) failed
  70 |   await expect(page.locator('#subsystem-status')).toContainText('ESP32: NOT CONNECTED');
  71 |   await expect(page.locator('#model-status')).toContainText('TOY ACCURACY: NOT VALIDATED');
  72 | });
  73 | 
```