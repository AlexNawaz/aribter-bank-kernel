/**
 * 为文档与演示文稿截取网页界面 / Capture the web UI for the docs and the decks.
 *
 *   python -m arbiter.cli serve --port 8790        # 另开一个终端 / in another terminal
 *   NODE_PATH=$(npm root -g) node scripts/capture_ui.js
 *
 * 需要 Playwright 与 Chromium。输出到 docs/figures/ui/，每张中英各一版，2 倍像素。
 * Needs Playwright with Chromium. Writes docs/figures/ui/, Chinese and English, at 2x.
 */
const path = require("path");
const { chromium } = require("playwright");

const OUT = path.resolve(__dirname, "..", "docs", "figures", "ui");
const URL = process.argv[2] || "http://127.0.0.1:8790/";

(async () => {
  const browser = await chromium.launch();
  const errors = [];
  async function page() {
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 2 });
    const p = await ctx.newPage();
    p.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
    p.on("pageerror", (e) => errors.push(String(e)));
    await p.goto(URL);
    await p.waitForSelector(".bot");
    return p;
  }
  const idle = (p) => p.waitForFunction(() => !document.getElementById("box").disabled);
  async function say(p, text) { await p.fill("#box", text); await p.press("#box", "Enter"); await idle(p); }
  async function click(p, sel) { await p.click(sel); await idle(p); }

  for (const lang of ["zh", "en"]) {
    const ask = lang === "zh" ? "给房东转4500" : "transfer 4500 to my landlord";

    // 正常账户：信封 → 确认 → 已执行 / normal account: envelope, confirm, executed
    let p = await page();
    if (lang === "en") await click(p, "#l-en");
    await say(p, ask);
    await click(p, ".slip .btn.go");
    await p.waitForSelector(".seal.executed");
    await p.waitForTimeout(400);                       // 等盖章动画结束 / let the stamp settle
    const slip = await p.$(".slip");
    await slip.scrollIntoViewIfNeeded();
    await slip.screenshot({ path: path.join(OUT, `${lang}_slip_executed.png`) });

    // 被攻击的账户：同一句话 → 确认 → 内核要求再确认 / attacked account: same request, escalation
    p = await page();
    if (lang === "en") await click(p, "#l-en");
    await click(p, "#m-attacked");
    await p.waitForSelector(".bot");
    await say(p, ask);
    await click(p, ".slip .btn.go");
    await p.waitForSelector(".bot.escalation");
    await p.waitForTimeout(300);
    // 侧栏只取到「触发的发现」为止 / the side panel down to the findings
    const box = await p.$eval(".side", (side) => {
      const r = side.getBoundingClientRect();
      const heads = [...side.querySelectorAll("h3")];
      const next = heads.find((h) => /不可信|Untrusted/.test(h.textContent));
      const bottom = next ? next.getBoundingClientRect().top - 8 : r.bottom;
      return { x: r.left, y: r.top, width: r.width, height: bottom - r.top };
    });
    await p.screenshot({ path: path.join(OUT, `${lang}_kernel_panel.png`), clip: box });
    const esc = await p.$(".bot.escalation");
    await esc.scrollIntoViewIfNeeded();
    await esc.screenshot({ path: path.join(OUT, `${lang}_escalation.png`) });
  }
  if (errors.length) {
    console.error("页面错误 / page errors:", errors.join(" | "));
    process.exitCode = 1;
  }
  await browser.close();
  console.log("wrote", OUT);
})().catch((e) => { console.error(e); process.exit(1); });
