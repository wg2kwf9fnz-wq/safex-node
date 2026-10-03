// Drives the dashboard in headless Chromium over CDP (no dependencies; Node 24 has global WebSocket).
const { spawn } = require('child_process');
const fs = require('fs');
const URL = process.argv[2], WALLET = process.argv[3], OUT = process.argv[4];
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
(async () => {
  const chrome = spawn('/usr/bin/chromium', ['--headless=new','--no-sandbox','--disable-gpu','--remote-debugging-port=9333','--user-data-dir=/tmp/cdp-profile','--window-size=1100,1500','about:blank'], { stdio: 'ignore' });
  let targets;
  for (let i = 0; i < 40; i++) { try { targets = await (await fetch('http://127.0.0.1:9333/json')).json(); if (targets.length) break; } catch (e) {} await sleep(500); }
  const page = targets.find(t => t.type === 'page');
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise(r => ws.addEventListener('open', r));
  let id = 0; const pending = new Map(); const logs = [];
  ws.addEventListener('message', (m) => { const d = JSON.parse(m.data); if (d.id && pending.has(d.id)) { pending.get(d.id)(d); pending.delete(d.id); } else if (d.method === 'Runtime.consoleAPICalled') logs.push(d.params.args.map(a => a.value).join(' ')); else if (d.method === 'Runtime.exceptionThrown') logs.push('EXC ' + d.params.exceptionDetails.text + ' ' + JSON.stringify(d.params.exceptionDetails.exception && d.params.exceptionDetails.exception.description)); });
  const send = (method, params = {}) => new Promise(r => { const i = ++id; pending.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
  const evalJs = async (expr) => (await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true })).result.result.value;
  const shot = async (name) => { const r = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true }); fs.writeFileSync(OUT + '/' + name + '.png', Buffer.from(r.result.data, 'base64')); };
  await send('Runtime.enable'); await send('Page.enable');
  await send('Page.navigate', { url: URL }); await sleep(4000);
  const text = (sel) => evalJs('document.querySelector(' + JSON.stringify(sel) + ').innerText');
  console.log('INITIAL status      :', await text('#status'));
  console.log('INITIAL height      :', await text('#height'));
  console.log('INITIAL miningStatus:', await text('#miningStatus'));
  console.log('INITIAL wallet note :', await text('#currentWallet'));
  await shot('1-before-wallet');
  // bad address first
  await evalJs("document.getElementById('wallet').value='not-an-address'; document.getElementById('saveWallet').click()"); await sleep(500);
  console.log('BAD ADDRESS msg     :', await text('#walletMsg'));
  // real flow: type into the field, click Save
  await evalJs("document.getElementById('wallet').value=" + JSON.stringify(WALLET) + "; document.getElementById('saveWallet').click()");
  await sleep(6000);
  console.log('AFTER SAVE msg      :', await text('#walletMsg'));
  console.log('AFTER SAVE wallet   :', await text('#currentWallet'));
  await sleep(10000);
  console.log('AFTER SAVE mining   :', await text('#miningStatus'));
  console.log('AFTER SAVE border   :', await evalJs("document.getElementById('walletBox').classList.contains('needs')"));
  await shot('2-after-wallet');
  // reload page: wallet must persist and be shown
  await send('Page.navigate', { url: URL }); await sleep(4000);
  console.log('RELOAD wallet note  :', await text('#currentWallet'));
  console.log('RELOAD input value  :', await evalJs("document.getElementById('wallet').value"));
  console.log('CONSOLE:', JSON.stringify(logs));
  ws.close(); chrome.kill(); process.exit(0);
})().catch(e => { console.error('driver error', e); process.exit(1); });
