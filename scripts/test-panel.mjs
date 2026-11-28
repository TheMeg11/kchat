// Run against the seeded local preview ONLY, never a production panel.
import assert from 'node:assert/strict';
import {chromium as playwright} from 'playwright-core';
import chromium from '@sparticuz/chromium';
import {mkdir} from 'node:fs/promises';
const base=process.env.E2E_BASE_URL || 'http://127.0.0.1:8000';
assert(['127.0.0.1','localhost'].includes(new URL(base).hostname),'E2E is restricted to a local test service');
const args=chromium.args.filter(a=>!a.includes('disable-web-security')&&!a.includes('allow-running-insecure-content'));
const browser=await playwright.launch({headless:true,args,executablePath:await chromium.executablePath()});
const page=await browser.newPage({viewport:{width:1440,height:960}});
const errors=[];
page.on('pageerror',e=>errors.push(e.message));
await mkdir('artifacts',{recursive:true});
try {
  await page.goto(base+'/auth/login');
  await page.locator('input[name="username"]').fill('preview');
  await page.locator('input[name="password"]').fill('Preview-Local-Only-Password123');
  await page.getByRole('button',{name:'ورود به پنل هم‌چت',exact:true}).click();
  await page.waitForTimeout(400);
  if(await page.locator('input[name="currentPassword"]').isVisible()) {
    for(const name of ['currentPassword','newPassword','confirmPassword']) await page.locator(`input[name="${name}"]`).fill('Preview-Local-Only-Password123');
    await page.getByRole('button',{name:'ثبت رمز عبور و ادامه',exact:true}).click();
  }
  await page.waitForURL(base+'/');
  // The browser reload has no in-memory CSRF token; /me must rehydrate it.
  await page.reload();
  await page.waitForTimeout(300);
  const result=await page.evaluate(async()=>{
    const me=await fetch('/api/admin/v1/auth/me');
    return (await fetch('/api/admin/v1/notifications/read-all',{method:'POST',headers:{'X-CSRF-Token':me.headers.get('x-csrf-token')}})).status;
  });
  assert.equal(result,204);
  await page.goto(base+'/users');
  const search=page.getByRole('searchbox',{name:'جستجوی کاربران'});
  await search.waitFor();
  await search.fill('@OLD_test_USER');
  await page.getByText('علی آزمایشی',{exact:true}).waitFor();
  assert.equal(await page.locator('tbody tr').count(),1);
  await search.fill('۱۰۰۰۰۰');
  await page.getByText('علی آزمایشی',{exact:true}).waitFor();
  await page.waitForTimeout(450);
  assert.equal(await page.locator('tbody tr').count(),1);
  await page.screenshot({path:'artifacts/user-search-desktop.png',fullPage:true});
  await page.getByText('علی آزمایشی',{exact:true}).click();
  await page.getByRole('dialog').waitFor();
  await page.keyboard.press('Escape');
  assert.equal(await page.getByRole('dialog').count(),0);
  // Offline errors must be dismissible, not a fatal overlay or forced logout.
  await page.context().setOffline(true); await search.fill('offline'); await page.waitForTimeout(600);
  assert.equal(await page.locator('#eb-overlay').count(),0);
  await page.context().setOffline(false); await search.fill('@Old_Test_User');
  await page.waitForTimeout(600);
  await page.setViewportSize({width:390,height:844});
  await page.screenshot({path:'artifacts/user-search-mobile.png',fullPage:true});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),'mobile horizontal overflow');
  // Real commerce screen: persisted settings, bounded promo creation and revocation.
  await page.goto(base+'/monetization');
  await page.getByRole('heading',{name:'قیمت‌گذاری و توقف فروش'}).waitFor();
  const reconnectPrice=page.getByLabel('قیمت پایه اتصال مجدد (تومان)',{exact:true});
  const originalPrice=await reconnectPrice.inputValue();
  await reconnectPrice.fill('1200');
  const saved=page.waitForResponse(r=>r.url().endsWith('/monetization') && r.request().method()==='PUT');
  await page.getByRole('button',{name:'ذخیره قیمت‌ها',exact:true}).click();
  assert.equal((await saved).status(),200);
  await page.reload();
  await reconnectPrice.waitFor();
  assert.equal(await reconnectPrice.inputValue(),'1200');
  await reconnectPrice.fill(originalPrice);
  const restored=page.waitForResponse(r=>r.url().endsWith('/monetization') && r.request().method()==='PUT');
  await page.getByRole('button',{name:'ذخیره قیمت‌ها',exact:true}).click();
  assert.equal((await restored).status(),200);
  const code='E2E-'+Date.now();
  await page.getByLabel('کد (حروف لاتین، عدد و خط تیره)',{exact:true}).fill(code);
  await page.getByLabel('انقضا (به وقت دستگاه شما)',{exact:true}).fill(new Date(Date.now()+86400000).toISOString().slice(0,16));
  const created=page.waitForResponse(r=>r.url().endsWith('/monetization/promos') && r.request().method()==='POST');
  await page.getByRole('button',{name:'ساخت کد',exact:true}).click();
  assert.equal((await created).status(),200);
  await page.getByText(code+' · ',{exact:false}).waitFor();
  const promoRow=page.locator('div').filter({has:page.locator('p').filter({hasText:code})}).filter({has:page.getByRole('button',{name:'غیرفعال کردن',exact:true})}).last();
  const revoked=page.waitForResponse(r=>r.url().endsWith('/promos/'+code) && r.request().method()==='DELETE');
  await promoRow.getByRole('button',{name:'غیرفعال کردن',exact:true}).click();
  assert.equal((await revoked).status(),200);
  await page.screenshot({path:'artifacts/commerce-admin-mobile.png',fullPage:true});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),'commerce mobile overflow');
  await page.setViewportSize({width:1440,height:960});
  await page.screenshot({path:'artifacts/commerce-admin-desktop.png',fullPage:true});
  for(const path of ['/monitoring','/backups','/security']) {
    await page.goto(base+path); await page.waitForTimeout(500);
  }
  // Exercise the extracted password component and session rotation via the UI.
  await page.getByLabel('رمز فعلی',{exact:true}).fill('Preview-Local-Only-Password123');
  await page.getByLabel('رمز جدید',{exact:true}).fill('Preview-Local-Only-Password123');
  await page.getByLabel('تکرار رمز جدید',{exact:true}).fill('Preview-Local-Only-Password123');
  const rotation=page.waitForResponse(r=>r.url().endsWith('/auth/change-password') && r.request().method()==='POST');
  await page.getByRole('button',{name:'ذخیره رمز جدید',exact:true}).click();
  assert.equal((await rotation).status(),200);
  await page.reload();
  assert.equal(await page.evaluate(async()=>(await fetch('/api/admin/v1/auth/me')).status),200);
  assert.deepEqual(errors,[]);
  console.log('PASS: login, refresh+mutation, old-user server search, Persian digits, dialog Escape, offline recovery, mobile width, monitoring/backups/security rendering, commerce prices+promos+mobile, password+session rotation');
} catch(e) {console.error('browser errors',errors);console.error((await page.locator('body').innerText()).slice(-5000));await page.screenshot({path:'artifacts/panel-failure.png',fullPage:true});throw e;} finally {await browser.close();}
