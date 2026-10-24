import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
async function fixture(){const temporary=await fs.mkdtemp(path.join(os.tmpdir(),'study-client-test-'));return {temporary,env:{...process.env,STUDY_CLIENT_HOME:path.join(temporary,'home'),TEXTIN_APP_ID:'',TEXTIN_SECRET_CODE:''}};}
function run(args,env,cli=path.join(root,'bin/client.mjs')){return spawnSync(process.execPath,[cli,...args],{env,encoding:'utf8'});}
test('doctor does not install, store credentials or create a home',async()=>{const {temporary,env}=await fixture();try{const result=run(['doctor'],env);assert.equal(result.status,0,result.stderr);const status=JSON.parse(result.stdout);assert.equal(status.installed,false);assert.equal(status.transport,'local stdio');await assert.rejects(fs.stat(env.STUDY_CLIENT_HOME),{code:'ENOENT'});}finally{await fs.rm(temporary,{recursive:true,force:true});}});
test('cold offline serve gives actionable stderr and empty protocol stdout',async()=>{const {temporary,env}=await fixture();try{const result=run(['serve','--offline'],env);assert.equal(result.status,1);assert.equal(result.stdout,'');assert.match(result.stderr,/离线缓存缺少固定版本uv/);await assert.rejects(fs.stat(path.join(env.STUDY_CLIENT_HOME,'data')),{code:'ENOENT'});}finally{await fs.rm(temporary,{recursive:true,force:true});}});
test('tampered package is refused before download or state changes',async()=>{const {temporary,env}=await fixture();try{const copy=path.join(temporary,'package');await fs.cp(root,copy,{recursive:true});const cli=path.join(copy,'bin/client.mjs');await fs.appendFile(cli,'\n// changed\n');const result=run(['serve'],env,cli);assert.equal(result.status,1);assert.equal(result.stdout,'');assert.match(result.stderr,/运行包校验失败/);await assert.rejects(fs.stat(env.STUDY_CLIENT_HOME),{code:'ENOENT'});}finally{await fs.rm(temporary,{recursive:true,force:true});}});
test('bad official binary checksum never executes or writes uv',async()=>{const {temporary,env}=await fixture();try{const interceptor=path.join(temporary,'fetch.mjs');await fs.writeFile(interceptor,"globalThis.fetch=async()=>new Response('invalid binary',{status:200});");const result=spawnSync(process.execPath,['--import',interceptor,path.join(root,'bin/client.mjs'),'serve'],{env,encoding:'utf8'});assert.equal(result.status,1);assert.equal(result.stdout,'');assert.match(result.stderr,/SHA256不匹配/);const entries=await fs.readdir(path.join(env.STUDY_CLIENT_HOME,'cache'));assert(!entries.includes('uv'));assert(!entries.includes('.setup-lock'));}finally{await fs.rm(temporary,{recursive:true,force:true});}});
test('download failure preserves existing data and releases install lock',async()=>{const {temporary,env}=await fixture();try{await fs.mkdir(path.join(env.STUDY_CLIENT_HOME,'data'),{recursive:true});const kept=path.join(env.STUDY_CLIENT_HOME,'data','keep.txt');await fs.writeFile(kept,'existing synthetic data');const interceptor=path.join(temporary,'fetch.mjs');await fs.writeFile(interceptor,"globalThis.fetch=async()=>new Response('unavailable',{status:503});");const result=spawnSync(process.execPath,['--import',interceptor,path.join(root,'bin/client.mjs'),'serve'],{env,encoding:'utf8'});assert.equal(result.status,1);assert.match(result.stderr,/HTTP 503/);assert.equal(await fs.readFile(kept,'utf8'),'existing synthetic data');assert.equal(result.stdout,'');await assert.rejects(fs.stat(path.join(env.STUDY_CLIENT_HOME,'cache','.setup-lock')),{code:'ENOENT'});}finally{await fs.rm(temporary,{recursive:true,force:true});}});

test('marketplace and portable plugin pin the same approved npm client',async()=>{
 const metadata=JSON.parse(await fs.readFile(path.join(root,'package.json'),'utf8'));
 const plugin=JSON.parse(await fs.readFile(path.join(root,'plugin.json'),'utf8'));
 const mcp=JSON.parse(await fs.readFile(path.join(root,'mcp.json'),'utf8'));
 const market=JSON.parse(await fs.readFile(path.join(root,'../.agents/plugins/marketplace.json'),'utf8'));
 assert.equal(plugin.name,metadata.name);assert.equal(plugin.version,metadata.version);
 assert(plugin.extensions['com.openai'].interface.defaultPrompt.length===3);
 assert.deepEqual(mcp.mcpServers[metadata.name],{type:'stdio',command:'npx',args:['--yes',metadata.name+'@'+metadata.version,'serve']});
 assert.deepEqual(market.plugins[0].source,{source:'npm',package:metadata.name,version:metadata.version,registry:'https://registry.npmjs.org'});
 assert(metadata.files.includes('plugin.json')&&metadata.files.includes('mcp.json'));
 assert(await fs.stat(path.join(root,'skills/study-errorbook/SKILL.md')));
});
