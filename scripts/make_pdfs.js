/**
 * 把文档排成 PDF / Typeset the documents as PDFs.
 *
 *   NODE_PATH=$(npm root -g) node scripts/make_pdfs.js            # 输出到 dist/pdf/
 *
 * 需要 marked 与 Playwright（Chromium）。字体用 Noto Sans/Serif CJK SC、Inter、Lora，
 * 嵌入 PDF，读者电脑上没有这些字体也能正确显示。图片按 docs/ 下的相对路径解析。
 *
 * Needs marked and Playwright (Chromium). Fonts are embedded, so the PDFs look the
 * same on any machine. Images resolve relative to docs/.
 */

const fs = require("fs");
const path = require("path");
const { pathToFileURL } = require("url");
const { marked } = require("marked");
const { chromium } = require("playwright");

const ROOT = path.resolve(__dirname, "..");
const DOCS = path.join(ROOT, "docs");
const OUT = path.join(ROOT, "dist", "pdf");

const JOBS = [
  ["技术文档.md", "zh", "ARBITER 技术文档"],
  ["TECHNICAL_DOCUMENT.md", "en", "ARBITER Technical Document"],
  ["安全自评估.md", "zh", "ARBITER 安全自评估"],
  ["SECURITY_SELF_ASSESSMENT.md", "en", "ARBITER Security Self-Assessment"],
  ["需求追溯.md", "zh", "ARBITER 需求追溯"],
  ["REQUIREMENTS_TRACE.md", "en", "ARBITER Requirements Trace"],
  ["演示脚本.md", "zh", "ARBITER 演示脚本"],
  ["DEMO_SCRIPT.md", "en", "ARBITER Demo Script"],
  ["部署说明.md", "zh", "ARBITER 部署说明"],
  ["DEPLOYMENT.md", "en", "ARBITER Deployment"],
];

const CSS = (lang) => `
@page { size: A4; margin: 18mm 17mm 20mm 17mm; }
:root { --ink:#1C2A33; --ink2:#4B5B64; --ink3:#7A8890; --rule:#C9D5CF; --paper:#EEF2EE;
        --print:#2F5D7C; --seal:#C23B22; --amber:#A86A12; --teal:#1F6F5F; }
html { background: #fff; }
body { margin: 0; color: var(--ink); background: #fff;
  font-family: ${lang === "zh" ? '"Noto Sans CJK SC"' : '"Inter", "Noto Sans CJK SC"'}, sans-serif;
  font-size: ${lang === "zh" ? "10.3pt" : "10pt"}; line-height: ${lang === "zh" ? 1.78 : 1.58};
  font-kerning: normal; text-rendering: optimizeLegibility; }
h1, h2, h3, h4 { font-family: ${lang === "zh" ? '"Noto Serif CJK SC"' : '"Lora", "Noto Serif CJK SC"'}, serif;
  color: var(--ink); line-height: 1.3; break-after: avoid; page-break-after: avoid; }
h1 { font-size: 24pt; margin: 0 0 6pt; font-weight: 700; }
h2 { font-size: 15pt; margin: 22pt 0 8pt; padding-bottom: 4pt; border-bottom: 0.6pt solid var(--rule); }
h3 { font-size: 12pt; margin: 16pt 0 6pt; color: var(--print); }
h4 { font-size: 10.5pt; margin: 12pt 0 4pt; }
p { margin: 0 0 7pt; orphans: 3; widows: 3; }
strong { font-weight: 700; }
a { color: var(--print); text-decoration: none; }
hr { border: 0; border-top: 0.6pt solid var(--rule); margin: 16pt 0; }
blockquote { margin: 8pt 0; padding: 6pt 12pt; background: var(--paper); border-radius: 4pt; color: var(--ink2); }
blockquote p { margin: 0; }
code { font-family: "DejaVu Sans Mono", "Noto Sans Mono CJK SC", monospace; font-size: 0.86em;
  background: #F2F5F2; padding: 0.5pt 3pt; border-radius: 2pt; }
pre { background: #F6F8F6; border: 0.6pt solid var(--rule); border-radius: 4pt; padding: 8pt 10pt;
  font-size: 8.4pt; line-height: 1.5; overflow: hidden; white-space: pre-wrap; break-inside: avoid; }
pre code { background: none; padding: 0; font-size: inherit; }
table { border-collapse: collapse; width: 100%; margin: 6pt 0 12pt; font-size: 0.92em; break-inside: auto; }
thead { display: table-header-group; }
tr { break-inside: avoid; page-break-inside: avoid; }
th { text-align: left; font-weight: 700; color: var(--ink2); border-bottom: 0.9pt solid var(--ink3);
  padding: 4pt 6pt; vertical-align: bottom; }
td { border-bottom: 0.5pt solid var(--rule); padding: 4pt 6pt; vertical-align: top; }
ul, ol { margin: 0 0 8pt; padding-left: 18pt; }
li { margin: 2pt 0; }
img { max-width: 100%; break-inside: avoid; }
p:has(> img:only-child) { text-align: center; margin: 10pt 0 4pt; }
p.figpair { display: flex; gap: 10pt; justify-content: center; align-items: flex-start; margin: 10pt 0 4pt; }
p.figpair img { border: 0.6pt solid var(--rule); border-radius: 3pt; }
.doc-foot { margin-top: 24pt; padding-top: 6pt; border-top: 0.6pt solid var(--rule); color: var(--ink3); font-size: 8.5pt; }
`;

function render(md, lang) {
  // 指向其他 .md 的链接在 PDF 里点不开：保留文字，去掉链接
  // links to other .md files cannot open inside a PDF: keep the text, drop the link
  const html = marked.parse(md, { gfm: true, breaks: false })
    .replace(/<a href="(?!https?:)[^"]*\.md(#[^"]*)?">(.*?)<\/a>/g, "$2");
  // 图片按 docs/ 解析 / resolve images against docs/
  const withImages = html.replace(/src="(?!https?:|data:|file:)([^"]+)"/g,
    (_, rel) => `src="${pathToFileURL(path.join(DOCS, decodeURI(rel))).href}"`);
  // 英文源文件用「 -- 」代替破折号，排版时换成真正的破折号；代码里的不动
  // the English sources write " -- " for a dash; typeset it as one, leaving code untouched
  return withImages.split(/(<pre[\s\S]*?<\/pre>|<code>[\s\S]*?<\/code>)/g)
    .map((part, i) => (i % 2 ? part : part.replace(/ -- /g, " \u2014 ")))
    .join("");
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch();
  const page = await browser.newPage();
  for (const [file, lang, title] of JOBS) {
    const md = fs.readFileSync(path.join(DOCS, file), "utf8");
    const body = render(md, lang);
    const html = `<!doctype html><html lang="${lang === "zh" ? "zh-CN" : "en"}"><head><meta charset="utf-8">
      <title>${title}</title><style>${CSS(lang)}</style></head><body>${body}</body></html>`;
    const tmp = path.join(OUT, ".render.html");
    fs.writeFileSync(tmp, html, "utf8");
    await page.goto(pathToFileURL(tmp).href, { waitUntil: "load" });
    await page.evaluate(() => document.fonts.ready);
    const missing = await page.evaluate(() => [...document.images].filter((i) => !i.naturalWidth).map((i) => i.src));
    if (missing.length) throw new Error(`${file}: images not found: ${missing.join(", ")}`);
    const out = path.join(OUT, file.replace(/\.md$/, ".pdf"));
    const footer = lang === "zh" ? "第 <span class=\"pageNumber\"></span> 页，共 <span class=\"totalPages\"></span> 页"
                                 : "Page <span class=\"pageNumber\"></span> of <span class=\"totalPages\"></span>";
    await page.pdf({
      path: out, format: "A4", printBackground: true, displayHeaderFooter: true,
      headerTemplate: `<div style="font-size:7.5pt;width:100%;padding:0 17mm;color:#7A8890;font-family:'Noto Sans CJK SC',sans-serif;">${title}</div>`,
      footerTemplate: `<div style="font-size:7.5pt;width:100%;padding:0 17mm;text-align:right;color:#7A8890;font-family:'Noto Sans CJK SC',sans-serif;">${footer}</div>`,
      margin: { top: "18mm", bottom: "20mm", left: "17mm", right: "17mm" },
    });
    fs.unlinkSync(tmp);
    console.log("wrote", path.relative(ROOT, out));
  }
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
