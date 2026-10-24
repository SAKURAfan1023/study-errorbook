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
const profileData=path.join(clean,'acceptance-data'),profileInput=path.join(clean,'acceptance-input');
const setupResult=await run('cold-setup',process.execPath,[cli,'setup','--profile','acceptance','--data',profileData,'--input',profileInput,'--no-credentials']);
const setup=JSON.parse(setupResult.stdout);assert(setup.ready);assert.equal(setup.profile,'acceptance');assert.equal(setup.data,profileData);assert.equal(setup.exports,path.join(profileData,'exports'));assert(setup.credentials_disabled);assert(setup.backup);
const configResult=await run('mcp-config',process.execPath,[cli,'mcp-config'],{UV_OFFLINE:'1'});
const config=JSON.parse(configResult.stdout).mcpServers['study-errorbook'];
assert.equal(config.command,process.execPath);assert(!config.args[0].includes('/installed/'));
const receipt=JSON.parse(await fs.readFile(path.join(path.dirname(setup.plugin_directory),'ready.json'),'utf8'));
assert(receipt.python_realpath.startsWith(await fs.realpath(path.join(clean,'client/cache/python'))));
const profileProbe=path.join(output,'profile-probe.py');
await fs.writeFile(profileProbe,`import asyncio,json,sys
from pathlib import Path
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
async def main():
 c=json.loads(Path(sys.argv[1]).read_text())["mcpServers"]["study-errorbook"]
 async with asyncio.timeout(30):
  async with stdio_client(StdioServerParameters(**c)) as (r,w):
   async with ClientSession(r,w) as session:
    await session.initialize()
    result=await session.call_tool("workspace_status",{})
    assert not result.isError
    print(json.dumps(result.structuredContent))
asyncio.run(main())
`);
const managedVenvPython=path.join(path.dirname(setup.plugin_directory),'runtime/bin/python');
const generatedConfig=path.join(setup.plugin_directory,'.mcp.json');
const profileStatus=JSON.parse((await run('profile-stdio',managedVenvPython,['-I',profileProbe,generatedConfig],{UV_OFFLINE:'1'})).stdout);
assert.equal(profileStatus.client_profile,'acceptance');assert.equal(profileStatus.data_dir,await fs.realpath(profileData));assert.equal(profileStatus.input_root,await fs.realpath(profileInput));assert.equal(profileStatus.exports_dir,path.join(await fs.realpath(profileData),'exports'));assert.equal(profileStatus.textin_configured,false);assert.equal(profileStatus.runtime_version,setup.runtime_version);

const testResult=await run('stdio',process.execPath,[cli,'self-test'],{UV_OFFLINE:'1'});
const test=JSON.parse(testResult.stdout.trim());assert.equal(test.tools,35);assert.equal(test.ocr_calls,0);
await fs.rename(installed,path.join(clean,'npm-installation-moved'));
const data=profileData;await fs.mkdir(data,{recursive:true});await fs.writeFile(path.join(data,'keep.txt'),'synthetic data must survive');
const after=await run('offline-after-npm-move',config.command,[config.args[0],'self-test'],{UV_OFFLINE:'1'});
assert.equal(JSON.parse(after.stdout.trim()).tools,35);assert.equal(await fs.readFile(path.join(data,'keep.txt'),'utf8'),'synthetic data must survive');
const doctor=await run('doctor',config.command,[config.args[0],'doctor'],{UV_OFFLINE:'1'});const status=JSON.parse(doctor.stdout);assert(status.installed);assert.equal(status.profile,'acceptance');assert.equal(status.data,profileData);
await run('switch-profile',config.command,[config.args[0],'setup','--profile','default','--offline']);
await run('restore-profile',config.command,[config.args[0],'restore-config']);
const restored=JSON.parse((await run('doctor-restored',config.command,[config.args[0],'doctor'])).stdout);assert.equal(restored.profile,'acceptance');assert.equal(restored.data,profileData);assert.equal(await fs.readFile(path.join(data,'keep.txt'),'utf8'),'synthetic data must survive');
const restoredStatus=JSON.parse((await run('profile-stdio-restored',managedVenvPython,['-I',profileProbe,generatedConfig],{UV_OFFLINE:'1'})).stdout);assert.equal(restoredStatus.client_profile,'acceptance');assert.equal(restoredStatus.data_dir,profileStatus.data_dir);
const tar=spawnSync('/usr/bin/tar',['-tzf',tarball],{encoding:'utf8'});assert.equal(tar.status,0);
const entries=tar.stdout.trim().split('\n');assert(entries.every(n=>!/(^|\/)(?:\.env(?:\..*)?|\.npmrc|\.mcp\.json|node_modules|data|output|private|\.git)(\/|$)/.test(n)));assert(entries.every(n=>!n.endsWith('.db')&&!n.endsWith('.sqlite')));
const digest=createHash('sha256').update(await fs.readFile(tarball)).digest('hex');
const report={tarball,sha256:digest,bytes:(await fs.stat(tarball)).size,platform:process.platform,arch:process.arch,node:process.version,isolated_root:clean,path_entries:await fs.readdir(bin),global_python_uv_not_on_path:true,managed_python:receipt.python_realpath,runtime_version:setup.runtime_version,tools:test.tools,stdio_protocol_passed:true,cold_setup_ms:setupResult.milliseconds,offline_after_npm_installation_moved:true,data_sentinel_preserved:true,public_profile_persisted:true,profile_runtime_status:profileStatus,profile_backup_restore_passed:true,student_export_coverage_guard_and_completion:test.student_export_coverage_guard_and_completion,native_default_plugin_tested:false,npm_file_count:entries.length,files:entries,ocr_calls:0,host_registered:false,npm_published:false};
await fs.writeFile(path.join(output,'report.json'),JSON.stringify(report,null,2)+'\n');
console.log(JSON.stringify(report));
