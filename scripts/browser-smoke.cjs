// Uses the web app's locked Playwright dependency and installed Chrome by default.
const { createRequire } = require('node:module');
const webRequire = createRequire(require('node:path').resolve(__dirname, '../apps/web/package.json'));
const { chromium } = webRequire('playwright');
const fs = require('node:fs');

(async () => {
  const line = fs.readFileSync('.env', 'utf8').split(/\r?\n/).find(s => s.startsWith('FIATIUM_TOKENS='));
  const tokens = JSON.parse(line.slice('FIATIUM_TOKENS='.length).replace(/^'|'$/g, ''));
  const token = Object.keys(tokens).find(key => tokens[key].role === 'operator');
  const browser = await chromium.launch({ headless: true, channel: process.env.BROWSER_CHANNEL || 'chrome' });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.goto(process.env.FIATIUM_WEB_URL || 'http://127.0.0.1:8088');
    await page.getByLabel('Demo access token').fill(token);
    await page.getByRole('button', { name: 'Connect to Fiatium' }).click();
    await page.locator('#payments tbody tr').first().waitFor();
    await page.getByLabel('Display name').fill('Browser smoke (simulated)');
    await page.getByRole('button', { name: 'Create customer', exact: true }).click();
    await page.getByText('Simulated customer created.', { exact: true }).waitFor();
    await page.getByLabel('Amount in CAD cents').fill('2500');
    const invoiceResponse = page.waitForResponse(r => r.url().endsWith('/api/invoices') && r.request().method() === 'POST');
    await page.getByRole('button', { name: 'Issue invoice', exact: true }).click();
    const invoice = await (await invoiceResponse).json();
    await page.getByText('Invoice issued with a balanced receivable / revenue journal.', { exact: true }).waitFor();
    const paymentResponse = page.waitForResponse(r => r.url().endsWith('/api/payments') && r.request().method() === 'POST');
    await page.locator('#invoices tbody tr').filter({ hasText: invoice.id.slice(0, 8) }).getByRole('button').click();
    const payment = await (await paymentResponse).json();
    await page.getByText('Payment accepted. The worker will process it asynchronously.', { exact: true }).waitFor();
    await page.locator('#payments tbody tr').filter({ hasText: payment.id.slice(0, 8) }).getByRole('button', { name: 'Inspect' }).click();
    await page.getByRole('heading', { name: /Evidence/ }).waitFor();
    fs.mkdirSync('artifacts', { recursive: true });
    await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'instant' }));
    await page.screenshot({ path: 'artifacts/fiatium-preview.png' });
    await page.screenshot({ path: 'artifacts/fiatium-desktop.png', fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({ path: 'artifacts/fiatium-mobile.png', fullPage: true });
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
    if (overflow) throw new Error('Mobile page overflows viewport');
    if (errors.length) throw new Error(errors.join('\n'));
    console.log('Browser smoke passed: login, customer, invoice, payment, evidence; desktop/mobile screenshots saved.');
  } finally { await browser.close(); }
})().catch(e => { console.error(e.message); process.exit(1); });
