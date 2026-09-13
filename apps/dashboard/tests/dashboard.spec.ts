import { test, expect } from '@playwright/test';

test('live dashboard, policy, emergency, comparison and ROI',async({page})=>{
  const errors:string[]=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto('/dashboard/');
  await expect(page.getByText('Система онлайн',{exact:true})).toBeVisible();
  await expect(page.getByRole('heading',{name:'Город в движении.'})).toBeVisible();
  await page.getByRole('button',{name:'Fixed-Time',exact:true}).click();
  await expect.poll(async()=> (await (await page.request.get('/api/v1/system')).json()).policy).toBe('fixed');
  await page.getByRole('button',{name:'Smart AI',exact:true}).click();
  await expect.poll(async()=> (await (await page.request.get('/api/v1/system')).json()).policy).toBe('adaptive');
  await page.getByRole('button',{name:'Экстренный приоритет'}).click();
  await page.getByLabel('Направление',{exact:true}).selectOption('east');
  await page.getByRole('button',{name:'Запросить приоритет',exact:true}).click();
  await expect.poll(async()=> (await (await page.request.get('/api/v1/state')).json()).emergency_phase).toBe('east_west');
  await page.getByRole('button',{name:'Отменить активный приоритет'}).click();
  await page.getByRole('button',{name:'Закрыть',exact:true}).click();
  await page.getByRole('link',{name:'AI vs Fixed-Time'}).click();
  await page.getByRole('button',{name:'Запустить сравнение'}).click();
  await expect(page.getByText('меньше суммарного ожидания',{exact:true})).toBeVisible({timeout:30000});
  await expect(page.getByRole('heading',{name:'Результат эксперимента'})).toBeVisible();
  await page.screenshot({path:'../../.runtime/dashboard-comparison.png',fullPage:true});
  await page.getByRole('link',{name:'Калибровка ROI'}).click();
  await page.getByRole('button',{name:'Загрузить ROI'}).click();
  await expect(page.getByLabel('Конфигурация JSON')).toHaveValue(/"source_id": "demo"/);
  await expect(page.getByRole('img',{name:'Предпросмотр геометрии камеры'})).toBeVisible();
  await page.getByRole('link',{name:'Видеоаналитика'}).click();
  await expect(page.getByRole('heading',{name:'KPI текущей симуляции'})).toBeVisible();
  await page.getByRole('link',{name:'Обзор перекрёстка'}).click();
  await page.screenshot({path:'../../.runtime/dashboard-desktop.png',fullPage:true});
  expect(errors).toEqual([]);
});

test('responsive tablet/mobile with no horizontal overflow',async({page})=>{
  for(const width of [1024,768,390]){
    await page.setViewportSize({width,height:900});await page.goto('/dashboard/');
    await expect(page.getByText('Система онлайн',{exact:true})).toBeVisible();
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    await page.screenshot({path:`../../.runtime/dashboard-${width}.png`,fullPage:true});
  }
});

test('connection loss hides signal confirmation and disables control',async({page})=>{
  await page.goto('/dashboard/');await expect(page.getByText('Система онлайн',{exact:true})).toBeVisible();
  await page.context().setOffline(true);
  await expect(page.getByText('Сигнал неизвестен',{exact:true})).toBeVisible({timeout:12000});
  await expect(page.getByRole('button',{name:'Fixed-Time',exact:true})).toBeDisabled();
  await page.context().setOffline(false);
  await expect(page.getByText('Система онлайн',{exact:true})).toBeVisible({timeout:20000});
});
