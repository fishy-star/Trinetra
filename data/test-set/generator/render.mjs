// Renders every HTML page to a PNG (phone-sized screenshot) and a PDF.
// Usage: node render.mjs <html_dir> <out_dir>
import { chromium } from 'playwright';
import { readdirSync, mkdirSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const [htmlDir, outDir] = process.argv.slice(2).map((p) => resolve(p));
mkdirSync(join(outDir, 'png'), { recursive: true });
mkdirSync(join(outDir, 'pdf'), { recursive: true });

const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium-1194/chrome-linux/chrome' });
const page = await browser.newPage({ viewport: { width: 794, height: 1123 }, deviceScaleFactor: 1.5 });
for (const f of readdirSync(htmlDir).filter((f) => f.endsWith('.html')).sort()) {
  const id = f.replace(/\.html$/, '');
  await page.goto(pathToFileURL(join(htmlDir, f)).href);
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({ path: join(outDir, 'png', `${id}.png`), fullPage: false });
  await page.pdf({ path: join(outDir, 'pdf', `${id}.pdf`), width: '794px', height: '1123px', printBackground: true, pageRanges: '1' });
  console.log('rendered', id);
}
await browser.close();
