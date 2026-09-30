const { createRequire } = require('node:module');
const webRequire = createRequire(require('node:path').resolve(__dirname, '../apps/web/package.json'));
const { chromium } = webRequire('playwright');
const fs = require('node:fs');

(async () => {
  const fixture = JSON.parse(fs.readFileSync('artifacts/refund-fixture.json', 'utf8'));
  const line = fs.readFileSync('.env', 'utf8').split(/\r?\n/).find(s => s.startsWith('FIATIUM_TOKENS='));
  const tokens = JSON.parse(line.slice('FIATIUM_TOKENS='.length).replace(/^'|'$/g, ''));
  const token = Object.keys(tokens).find(key => tokens[key].role === 'operator');
  const base = process.env.FIATIUM_WEB_URL || 'http://127.0.0.1:8088';
  async function api(path, body, key) {
    const response = await fetch(`${base}/api/${path}`, {method: body ? 'POST' : 'GET',
      headers: {Authorization: `Bearer ${token}`, 'Content-Type': 'application/json', ...(key ? {'Idempotency-Key': key} : {})},
      body: body ? JSON.stringify(body) : undefined});
    if (!response.ok) throw new Error(`API failed: ${response.status}`);
    return response.json();
  }
  const original = (await api('journals')).filter(j => j.operation === `settlement:${fixture.payment_id}`);
  const browser = await chromium.launch({headless: true, channel: process.env.BROWSER_CHANNEL || 'chrome'});
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(base);
    await page.getByLabel('Demo access token').fill(token);
    await page.getByRole('button', {name: 'Connect to Fiatium'}).click();
    const row = page.locator('#payments tbody tr').filter({hasText: fixture.payment_id.slice(0, 8)});
    await row.getByRole('button', {name: 'Inspect'}).click();
    await page.getByLabel('Refund reason').fill('Browser verification: full simulated return');
    const refundResponse = page.waitForResponse(r => r.url().endsWith('/api/refunds') && r.request().method() === 'POST');
    await page.getByRole('button', {name: 'Refund $42.00 in full'}).click();
    const response = await refundResponse;
    if (response.status() !== 201) throw new Error(`Refund failed: ${response.status()}`);
    const refund = await response.json();
    await page.getByText('Snapshot at inspection · refunded', {exact: true}).waitFor();
    const replay = await api('refunds', {payment_id: fixture.payment_id, reason: 'Browser verification: full simulated return'}, response.request().headers()['idempotency-key']);
    if (replay.id !== refund.id) throw new Error('Refund replay changed result');
    const journals = await api('journals');
    const preserved = journals.filter(j => j.operation === `settlement:${fixture.payment_id}`);
    if (JSON.stringify(original) !== JSON.stringify(preserved)) throw new Error('Original settlement changed');
    const reversal = journals.filter(j => j.id === refund.journal_id);
    if (reversal.length !== 2 || reversal.reduce((sum, j) => sum + j.amount, 0) !== 0) throw new Error('Unbalanced reversal');
    const invoice = (await api('invoices')).find(i => i.id === fixture.invoice_id);
    if (invoice.status !== 'open') throw new Error('Invoice was not reopened');
    const run = await api('reconciliation-runs', {});
    if (run.findings.some(f => f.payment_id === fixture.payment_id)) throw new Error('Refund reconciliation mismatch');
    await page.locator('.evidence').screenshot({path: 'artifacts/refund-evidence.png'});
    await page.setViewportSize({width: 390, height: 844});
    if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)) throw new Error('Mobile overflow');
    if (errors.length) throw new Error(errors.join('\n'));
    console.log('Refund browser smoke passed: reversal, original preserved, replay, reopened invoice, reconciliation.');
  } finally { await browser.close(); }
})().catch(error => {console.error(error.message); process.exit(1);});
