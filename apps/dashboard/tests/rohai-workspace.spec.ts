// Real local HTTP services. Devices deliberately absent; replay imagery is a labelled fixture.
import {test,expect} from '@playwright/test';

const artifacts = process.env.ROHAI_BROWSER_ARTIFACTS ?? '../../artifacts/rohai-1.0';

test.describe('RohAI local workspace acceptance',()=>{
  test.skip(process.env.ROHAI_ACCEPTANCE_LIVE!=='1','Run scripts/run_workspace_acceptance.py first');
  test('DEMO is mock and cannot execute physical outputs',async({request})=>{
    const r=await request.get('/api/v1/hardware');expect(r.ok()).toBe(true);
    const h=await r.json();expect(h.physical_execution).toBe(false);expect(h.confirmation_kind).toBe('software_only');const system=await (await request.get('/api/v1/demo/system')).json();expect(system.demo).toBe(true);expect(system.hardware).toBe('PresentationMockController');
    await expect.poll(async()=> (await request.get('/health/launcher')).ok()).toBe(true);
  });
  test('pairing, missing-device guards, consent, stop, recording and causal replay',async({page})=>{
    const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
    page.on('dialog',dialog=>dialog.accept());
    await page.goto('http://127.0.0.1:8001/maket');
    await expect(page.locator('#connection-badge')).toContainText('Backend подключён');
    await expect(page.locator('#mode-badge')).toHaveText('REAL VISION');
    await expect(page.locator('#main-action')).toBeDisabled();
    await expect(page.locator('#cars')).toHaveText('—');
    const permissions=await page.request.get('http://127.0.0.1:8001/api/maket/permissions');
    expect((await permissions.json()).setup_authorized).toBe(false);
    const rejected=await page.request.post('http://127.0.0.1:8001/api/maket/control',{data:{action:'start',confirm_physical:true}});
    expect(rejected.status()).toBe(401);
    // A real per-launch consent dialog obtains setup authority, with no copied token.
    await page.locator('#stop').click();
    await expect.poll(async()=> (await (await page.request.get('http://127.0.0.1:8001/api/maket/permissions')).json()).setup_authorized).toBe(true);
    await page.locator('#estop').click();
    await page.locator('#record').click();
    await expect(page.locator('#record')).toHaveText('Остановить запись видео');
    await page.locator('#record').click();
    await expect(page.locator('#record')).toHaveText('Записать видео');
    await page.locator('#replay').click();
    await expect(page.locator('#history')).toBeVisible();
    const items=await (await page.request.get('http://127.0.0.1:8001/api/maket/sessions')).json();
    const closed=items.find((s:{closed:boolean})=>s.closed);
    expect(closed).toBeTruthy();
    const commands:string[]=[];page.on('request',r=>{if(r.method()!=='GET')commands.push(r.url());});
    await page.goto('http://127.0.0.1:8001/maket/replay/'+closed.id);
    await expect(page.getByRole('heading',{level:1})).toContainText('физическое исполнение отключено');
    await expect(page.locator('#play')).toBeEnabled();
    await page.locator('#play').click();
    await expect(page.locator('#position')).toContainText('Событие');
    await page.locator('#pause').click();
    expect(commands).toEqual([]);
    await page.goto('http://127.0.0.1:8001/maket');
    await expect(page.locator('#connection-badge')).toContainText('Backend подключён');
    await page.screenshot({path:`${artifacts}/workspace-desktop.png`,fullPage:true});
    await page.context().setOffline(true);
    await expect(page.locator('#connection-badge')).toContainText('Нет подключения');
    await expect(page.locator('#safety-badge')).toContainText('Состояние неизвестно');
    await expect(page.locator('#main-action')).toBeDisabled();
    await page.context().setOffline(false);
    await page.locator('#connect').click();
    await expect(page.locator('#connection-badge')).toContainText('Backend подключён');
    await page.locator('details summary').click();
    await page.locator('#revoke').click();
    await expect(page.locator('#notice')).toContainText('Доступ отозван');
    expect((await page.request.get('http://127.0.0.1:8001/api/maket/permissions')).status()).toBe(401);
    expect(errors).toEqual([]);
  });
  test('workspace keyboard focus and mobile layout',async({page})=>{
    page.on('dialog',dialog=>dialog.accept());
    await page.setViewportSize({width:390,height:844});
    await page.goto('http://127.0.0.1:8001/maket');
    await expect(page.locator('#connection-badge')).toContainText('Backend подключён');
    await page.keyboard.press('Tab');await expect(page.locator('.skip-link')).toBeFocused();
    await page.keyboard.press('Enter');await expect(page.locator('#main')).toBeInViewport();
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    await page.screenshot({path:`${artifacts}/workspace-mobile.png`,fullPage:true});
  });
});
