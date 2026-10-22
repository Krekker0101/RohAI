const { chromium } = require('../apps/dashboard/node_modules/playwright');
const path = require('path');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:'C:/Users/hp/AppData/Local/ms-playwright/chromium-1243/chrome-win64/chrome.exe',args:['--no-sandbox']});
 try { const page=await browser.newPage({viewport:{width:1920,height:1080},deviceScaleFactor:1});
 const url='file:///'+path.resolve(__dirname,'RohAI.html').replace(/\\/g,'/'); await page.goto(url,{waitUntil:'load'});
 const count=await page.locator('.slide').count(); await page.pdf({path:path.join(__dirname,'RohAI.pdf'),printBackground:true,preferCSSPageSize:true});
 console.log('PDF created; slides='+count);
 } finally {await browser.close()}
})().catch(e=>{console.error(e);process.exit(1)});
