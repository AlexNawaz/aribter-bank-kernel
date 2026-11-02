// 把 docs/figures/*.svg 渲染成同名 PNG（2 倍分辨率）。
// Render docs/figures/*.svg to PNG at 2x. Needs Node and Playwright with Chromium.
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');
(async () => {
  const dir = path.resolve(__dirname, '..', 'docs', 'figures');
  const only = process.argv.slice(2);
  const files = fs.readdirSync(dir).filter(f => f.endsWith('.svg') && (!only.length || only.some(o => f.includes(o))));
  const browser = await chromium.launch();
  const page = await browser.newPage({ deviceScaleFactor: 2 });
  for (const f of files) {
    const svg = fs.readFileSync(path.join(dir, f), 'utf8');
    const w = +svg.match(/width="(\d+)"/)[1], h = +svg.match(/height="(\d+)"/)[1];
    await page.setViewportSize({ width: w, height: h });
    await page.setContent(`<html><body style="margin:0">${svg}</body></html>`);
    await page.screenshot({ path: path.join(dir, f.replace(/\.svg$/, '.png')), clip: { x: 0, y: 0, width: w, height: h } });
    console.log('rendered', f);
  }
  await browser.close();
})();
