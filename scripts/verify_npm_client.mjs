// Developer QA only. Executes a local tarball with no Python or uv on PATH.
import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {spawn,spawnSync} from 'node:child_process';
import {createHash} from 'node:crypto';

const [tarballArg,outputArg,npmCliArg]=process.argv.slice(2);
if(!tarballArg||!outputArg||!npmCliArg)throw new Error('Pass local tarball, output directory, and npm CLI JavaScript path.');
const tarball=path.resolve(tarballArg), output=path.resolve(outputArg), npmCli=path.resolve(npmCliArg);
await fs.mkdir(output,{recursive:true});
const clean=await fs.mkdtemp('/tmp/study-npm-acceptance-');
const bin=path.join(clean,'bin');await fs.mkdir(bin);await fs.symlink(process.execPath,path.join(bin,'node'));
if(process.platform==='darwin')await fs.symlink('/usr/bin/install_name_tool',path.join(bin,'install_name_tool'));
const env={PATH:bin,HOME:process.env.HOME,LANG:'en_US.UTF-8',STUDY_CLIENT_HOME:path.join(clean,'client'),TEXTIN_APP_ID:'',TEXTIN_SECRET_CODE:'',npm_config_cache:path.join(clean,'npm-cache'),npm_config_userconfig:path.join(clean,'absent.npmrc'),npm_config_globalconfig:path.join(clean,'absent-global.npmrc')};
async function run(label,executable,args,extra={}){
  console.log(JSON.stringify({phase:label}));
  const started=Date.now();
  const result=await new Promise((resolve,reject)=>{const child=spawn(executable,args,{env:{...env,...extra},stdio:['ignore','pipe','pipe']});let stdout='',stderr='';child.stdout.on('data',b=>stdout+=b);child.stderr.on('data',b=>stderr+=b);child.on('error',reject);child.on('exit',code=>resolve({code,stdout,stderr,milliseconds:Date.now()-started}));});
  await fs.writeFile(path.join(output,label+'.stdout'),result.stdout);await fs.writeFile(path.join(output,label+'.stderr'),result.stderr);
  assert.equal(result.code,0,label+': '+result.stderr.slice(-2000));return result;
}
const installed=path.join(clean,'installed');
const install=await run('npm-install',process.execPath,[npmCli,'install','--prefix',installed,'--offline','--ignore-scripts','--no-audit','--no-fund','--package-lock=false',tarball]);
const cli=path.join(installed,'node_modules/study-errorbook/bin/client.mjs');
const setupResult=await run('cold-setup',process.execPath,[cli,'setup']);
const setup=JSON.parse(setupResult.stdout);assert(setup.ready);
const configResult=await run('mcp-config',process.execPath,[cli,'mcp-config'],{UV_OFFLINE:'1'});
const config=JSON.parse(configResult.stdout).mcpServers['study-errorbook'];
assert.equal(config.command,process.execPath);assert(!config.args[0].includes('/installed/'));
const receipt=JSON.parse(await fs.readFile(path.join(path.dirname(setup.plugin_directory),'ready.json'),'utf8'));
assert(receipt.python_realpath.startsWith(await fs.realpath(path.join(clean,'client/cache/python'))));
const testResult=await run('stdio',process.execPath,[cli,'self-test'],{UV_OFFLINE:'1'});
const test=JSON.parse(testResult.stdout.trim());assert.equal(test.tools,35);assert.equal(test.ocr_calls,0);
await fs.rename(installed,path.join(clean,'npm-installation-moved'));
const data=path.join(clean,'client/data');await fs.mkdir(data,{recursive:true});await fs.writeFile(path.join(data,'keep.txt'),'synthetic data must survive');
const after=await run('offline-after-npm-move',config.command,[config.args[0],'self-test'],{UV_OFFLINE:'1'});
assert.equal(JSON.parse(after.stdout.trim()).tools,35);assert.equal(await fs.readFile(path.join(data,'keep.txt'),'utf8'),'synthetic data must survive');
const doctor=await run('doctor',config.command,[config.args[0],'doctor'],{UV_OFFLINE:'1'});assert(JSON.parse(doctor.stdout).installed);
const tar=spawnSync('/usr/bin/tar',['-tzf',tarball],{encoding:'utf8'});assert.equal(tar.status,0);
const entries=tar.stdout.trim().split('\n');assert(entries.every(n=>!/(^|\/)(?:\.env(?:\..*)?|\.npmrc|\.mcp\.json|node_modules|data|output|private|\.git)(\/|$)/.test(n)));assert(entries.every(n=>!n.endsWith('.db')&&!n.endsWith('.sqlite')));
const digest=createHash('sha256').update(await fs.readFile(tarball)).digest('hex');
const report={tarball,sha256:digest,bytes:(await fs.stat(tarball)).size,platform:process.platform,arch:process.arch,node:process.version,isolated_root:clean,path_entries:await fs.readdir(bin),global_python_uv_not_on_path:true,managed_python:receipt.python_realpath,runtime_version:setup.runtime_version,tools:test.tools,stdio_protocol_passed:true,cold_setup_ms:setupResult.milliseconds,offline_after_npm_installation_moved:true,data_sentinel_preserved:true,npm_file_count:entries.length,files:entries,ocr_calls:0,host_registered:false,npm_published:false};
await fs.writeFile(path.join(output,'report.json'),JSON.stringify(report,null,2)+'\n');
console.log(JSON.stringify(report));
