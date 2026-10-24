// Print a real local marketplace detail link without opening or configuring a host.
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const file=path.join(root,'.agents/plugins/marketplace.json');
const market=JSON.parse(await fs.readFile(file,'utf8'));
const entry=market.plugins.find(p=>p.name==='study-errorbook');
if(!entry)throw new Error('study-errorbook is missing from the local marketplace');
console.log('codex://plugins/'+encodeURIComponent(entry.name)+'?marketplacePath='+encodeURIComponent(file));
