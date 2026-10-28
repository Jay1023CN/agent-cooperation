import { readFileSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const staticDir = resolve(root, '../arena/static');
const output = resolve(process.argv[2] || join(root, '../pal-arena-preview.html'));
let html = readFileSync(join(staticDir, 'index.html'), 'utf8');
html = html.replace(/<link\s+rel="stylesheet"[^>]*href="\/static\/([^"?]+)"[^>]*>/g,
  (_, file) => `<style>${readFileSync(join(staticDir, file), 'utf8')}</style>`);
html = html.replace(/<script\s+type="module"[^>]*src="\/static\/([^"?]+)"[^>]*><\/script>/g,
  (_, file) => `<script>window.__PAL_ARENA_DEMO__=true;</script><script type="module">${readFileSync(join(staticDir, file), 'utf8').replace(/<\/script/gi, '<\\/script')}</script>`);
if (/src="\/static\/|href="\/static\//.test(html)) throw new Error('Uninlined assets remain.');
writeFileSync(output, html);
console.log(`Read-only offline preview: ${output}`);
