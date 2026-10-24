#!/usr/bin/env node
// Thin stdio launcher; the learning and safety rules remain in the Python core.
import {createHash} from 'node:crypto';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';

const packageRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const sha = bytes => createHash('sha256').update(bytes).digest('hex');
const exists = async p => { try { return (await fs.stat(p)).isFile(); } catch (e) { if(e.code==='ENOENT') return false; throw e; } };
const say = text => process.stderr.write(`[study-errorbook] ${text}\n`);
const args = process.argv.slice(2);
const command = args.shift() || 'serve';
const customHome = process.env.STUDY_CLIENT_HOME ? path.resolve(process.env.STUDY_CLIENT_HOME) : null;
const configRoot = customHome ? path.join(customHome,'config') : path.join(os.homedir(),'.config/study-errorbook');
const cacheRoot = customHome ? path.join(customHome,'cache') : path.join(process.platform==='darwin' ? path.join(os.homedir(),'Library/Caches') : process.env.XDG_CACHE_HOME || path.join(os.homedir(),'.cache'),'study-errorbook/client');
const inboxDefault = customHome ? path.join(customHome,'inbox') : path.join(os.homedir(),'Documents/StudyErrorbook/inbox');
const offline = args.includes('--offline') || process.env.UV_OFFLINE==='1';
let activeChild;

function run(executable, argv, env, capture=false) {
  return new Promise((resolve,reject) => {
    const child=spawn(executable,argv,{env,stdio:['ignore',capture?'pipe':2,2]});
    let output='';
    if(capture) child.stdout.on('data',chunk=>{output+=chunk;if(output.length>100000)child.kill();});
    child.on('error',()=>reject(new Error('无法启动隔离运行时；请重新运行 setup。')));
    child.on('exit',code=>code===0?resolve(output):reject(new Error(`准备运行时失败（退出码 ${code}）；未上传材料。检查网络和上方错误后重试 setup，原数据不受影响。`)));
  });
}
async function options() {
  if(args.some((a,i)=>a!=='--offline' && !(command==='setup' && (a==='--input' || args[i-1]==='--input')))) throw new Error('不支持的参数；使用 --help 查看用法。');
  const file=path.join(configRoot,'client.json');
  let saved={};
  if(await exists(file)) saved=JSON.parse(await fs.readFile(file,'utf8'));
  if(saved.inputRoot && (!path.isAbsolute(saved.inputRoot)||typeof saved.inputRoot!=='string')) throw new Error('client.json 的 inputRoot 应为绝对目录。');
  return {file,saved};
}
async function payload() {
  const manifest=JSON.parse(await fs.readFile(path.join(packageRoot,'runtime/manifest.json'),'utf8'));
  for(const [relative,digest] of Object.entries(manifest.files)) {
    if(path.isAbsolute(relative)||relative.split('/').some(p=>p==='..')||!['bin/','runtime/','skills/','.codex-plugin/','package.json','plugin.json','mcp.json'].some(p=>relative.startsWith(p))) throw new Error('运行包文件清单无效。');
    const file=path.join(packageRoot,relative);
    if(!(await exists(file))||(await fs.lstat(file)).isSymbolicLink()||sha(await fs.readFile(file))!==digest) throw new Error(`运行包校验失败：${relative}。请重新安装同一版本。`);
  }
  const metadata=JSON.parse(await fs.readFile(path.join(packageRoot,'package.json'),'utf8'));
  const id=sha(Buffer.from(JSON.stringify(manifest))).slice(0,24);
  return {manifest,metadata,folder:path.join(cacheRoot,'releases',id)};
}
async function acquireLock() {
  const directory=path.join(cacheRoot,'.setup-lock');
  const started=Date.now();
  while(true) {
    try {
      await fs.mkdir(directory,{mode:0o700});
      await fs.writeFile(path.join(directory,'pid'),String(process.pid),{mode:0o600});
      return async()=>fs.rm(directory,{recursive:true,force:true});
    } catch(error) {
      if(error.code!=='EEXIST') throw error;
      try {
        const pid=Number(await fs.readFile(path.join(directory,'pid'),'utf8'));
        if(Number.isInteger(pid)&&pid>0) {
          try {process.kill(pid,0);} catch(e) {if(e.code==='ESRCH'){await fs.rm(directory,{recursive:true,force:true});continue;}}
        }
      } catch(e) {if(e.code!=='ENOENT')throw e;}
      if(Date.now()-started>300000) throw new Error('另一个 client 正在准备运行时；请稍后重试。');
      await new Promise(resolve=>setTimeout(resolve,500));
    }
  }
}
async function installUv(manifest) {
  const target=process.platform==='darwin'?(process.arch==='arm64'?'aarch64-apple-darwin':'x86_64-apple-darwin'):(process.arch==='arm64'?'aarch64-unknown-linux-gnu':'x86_64-unknown-linux-gnu');
  const asset=manifest.uv.assets[target];
  if(!asset)throw new Error('暂不支持此平台；仅支持macOS及Linux glibc的arm64/x64。');
  const folder=path.join(cacheRoot,'uv',manifest.uv.version,target);
  const binary=path.join(folder,'uv');
  const receipt=path.join(folder,'sha256');
  if(await exists(binary) && await exists(receipt) && sha(await fs.readFile(binary))===(await fs.readFile(receipt,'utf8')).trim())return binary;
  if(offline)throw new Error('离线缓存缺少固定版本uv。请联网运行一次 setup；不会改用全局uv。');
  say('首次准备：下载固定版本uv、隔离Python和锁定依赖；仅下载程序，不上传材料。');
  const response=await fetch(`https://github.com/astral-sh/uv/releases/download/${manifest.uv.version}/${asset.name}`,{signal:AbortSignal.timeout(120000)});
  if(!response.ok)throw new Error(`uv下载失败（HTTP ${response.status}）；未上传材料。`);
  if(Number(response.headers.get('content-length'))>40000000)throw new Error('uv下载超出40MB限制。');
  const chunks=[];let size=0;
  for await (const chunk of response.body){size+=chunk.length;if(size>40000000)throw new Error('uv下载超出40MB限制。');chunks.push(chunk);}
  const bytes=Buffer.concat(chunks);
  if(sha(bytes)!==asset.sha256)throw new Error('uv官方发布资产SHA256不匹配；停止安装。');
  const temporary=await fs.mkdtemp(path.join(cacheRoot,'.uv-'));
  try {
    const archive=path.join(temporary,'uv.tar.gz');await fs.writeFile(archive,bytes,{mode:0o600});
    await run('/usr/bin/tar',['-xzf',archive,'-C',temporary,`uv-${target}/uv`],process.env);
    const extracted=path.join(temporary,`uv-${target}/uv`);
    if(!(await fs.lstat(extracted)).isFile())throw new Error('uv发布资产不含预期可执行文件。');
    await fs.mkdir(folder,{recursive:true,mode:0o700});
    await fs.copyFile(extracted,binary);await fs.chmod(binary,0o700);
    await fs.writeFile(receipt,sha(await fs.readFile(binary)),{mode:0o600});
  } finally {await fs.rm(temporary,{recursive:true,force:true});}
  return binary;
}
function uvEnvironment() {
  const env={...process.env,UV_NO_CONFIG:'1',UV_PYTHON_PREFERENCE:'only-managed',UV_PYTHON_INSTALL_DIR:path.join(cacheRoot,'python'),UV_CACHE_DIR:path.join(cacheRoot,'uv-cache'),UV_NO_PROGRESS:'1',UV_KEYRING_PROVIDER:'disabled',UV_PYTHON_DOWNLOADS:'automatic'};
  for(const key of ['UV_INDEX','UV_INDEX_URL','UV_EXTRA_INDEX_URL','UV_DEFAULT_INDEX','VIRTUAL_ENV','CONDA_PREFIX','PYTHONPATH','PYTHONHOME'])delete env[key];
  if(offline)env.UV_OFFLINE='1';
  return env;
}
async function ready(state) {
  const receipt=path.join(state.folder,'ready.json');
  if(!(await exists(receipt)))return false;
  const saved=JSON.parse(await fs.readFile(receipt,'utf8'));
  return saved.runtime_version===state.manifest.runtime_version && await exists(path.join(state.folder,'runtime/bin/python'));
}
async function prepare(state) {
  await fs.mkdir(cacheRoot,{recursive:true,mode:0o700});
  const release=await acquireLock();
  try {
    if(await ready(state))return;
    const uv=await installUv(state.manifest);
    const env=uvEnvironment();
    const runtime=path.join(state.folder,'runtime');
    await fs.mkdir(state.folder,{recursive:true,mode:0o700});
    // Only a partial managed environment for this exact payload may be replaced.
    await fs.rm(runtime,{recursive:true,force:true});
    await run(uv,['venv','--python',state.manifest.python_version,runtime],env);
    const python=path.join(runtime,'bin/python');
    const actual=await fs.realpath(python);
    if(!actual.startsWith((await fs.realpath(path.join(cacheRoot,'python')))+path.sep))throw new Error('拒绝使用全局Python；隔离运行时未准备成功。');
    await run(uv,['pip','install','--python',python,'--require-hashes','--only-binary',':all:','--default-index','https://pypi.org/simple','-r',path.join(packageRoot,'runtime/requirements.txt')],env);
    await run(uv,['pip','install','--python',python,'--no-deps',path.join(packageRoot,'runtime',state.manifest.wheel)],env);
    const version=(await run(python,['-I','-c','import importlib.metadata; print(importlib.metadata.version("study-errorbook"))'],env,true)).trim();
    if(version!==state.manifest.runtime_version)throw new Error('安装版本与发布包不一致。');
    const plugin=path.join(state.folder,'plugin');
    await fs.rm(plugin,{recursive:true,force:true});
    for(const relative of Object.keys(state.manifest.files)) {
      const destination=path.join(plugin,relative);await fs.mkdir(path.dirname(destination),{recursive:true,mode:0o700});
      await fs.copyFile(path.join(packageRoot,relative),destination);
    }
    await fs.copyFile(path.join(packageRoot,'runtime/manifest.json'),path.join(plugin,'runtime/manifest.json'));
    const config={mcpServers:{'study-errorbook':{command:process.execPath,args:[path.join(plugin,'bin/client.mjs'),'serve'],env:customHome?{STUDY_CLIENT_HOME:customHome}:{}}}};
    await fs.writeFile(path.join(plugin,'.mcp.json'),JSON.stringify(config,null,2)+'\n',{mode:0o600});
    await fs.writeFile(path.join(state.folder,'ready.json'),JSON.stringify({runtime_version:version,python_realpath:actual,uv_version:state.manifest.uv.version})+'\n',{mode:0o600});
  } finally {await release();}
}
async function main() {
  if(['--help','help'].includes(command)) {
    console.log('study-errorbook [serve | setup [--input DIRECTORY] [--offline] | doctor | mcp-config | self-test]\n首次运行自动准备隔离Python。serve的stdout仅为MCP协议；日志写stderr。无需手工安装Python/uv。');return;
  }
  if(!['serve','setup','doctor','mcp-config','self-test'].includes(command))throw new Error('未知命令；使用 --help 查看用法。');
  if(!['darwin','linux'].includes(process.platform)||!['arm64','x64'].includes(process.arch))throw new Error('Python核心目前仅支持macOS/Linux glibc arm64/x64；不支持Windows。');
  const configuration=await options();const state=await payload();
  const inputIndex=args.indexOf('--input');
  if(inputIndex!==-1) {
    if(!args[inputIndex+1]||args[inputIndex+1].startsWith('--'))throw new Error('--input 需要目录。');
    configuration.saved.inputRoot=path.resolve(args[inputIndex+1]);
  }
  if(command==='doctor') {
    console.log(JSON.stringify({installed:await ready(state),runtime_version:state.manifest.runtime_version,cache:cacheRoot,config:configRoot,input:configuration.saved.inputRoot||inboxDefault,transport:'local stdio',credentials:'只从现有环境/核心配置读取；client不保存令牌'},null,2));return;
  }
  await prepare(state);
  const inbox=configuration.saved.inputRoot||inboxDefault;
  await fs.mkdir(inbox,{recursive:true,mode:0o700});
  if(command==='setup') {
    if(inputIndex!==-1){await fs.mkdir(configRoot,{recursive:true,mode:0o700});await fs.writeFile(configuration.file,JSON.stringify(configuration.saved,null,2)+'\n',{mode:0o600});}
    console.log(JSON.stringify({ready:true,runtime_version:state.manifest.runtime_version,input:inbox,plugin_directory:path.join(state.folder,'plugin'),note:'不注册宿主、不存储凭据、不上传材料；配置见mcp-config。'},null,2));return;
  }
  const configFile=path.join(state.folder,'plugin/.mcp.json');
  if(command==='mcp-config'){console.log(await fs.readFile(configFile,'utf8'));return;}
  const env={...process.env,STUDY_CLIENT_INPUT_ROOT:inbox};
  if(configuration.saved.inputRoot)env.STUDY_INPUT_ROOT=configuration.saved.inputRoot;
  if(customHome){env.STUDY_DATA_DIR ??= path.join(customHome,'data');env.STUDY_CONFIG_FILE ??= path.join(configRoot,'config.env');}
  const python=path.join(state.folder,'runtime/bin/python');
  const argv=command==='self-test'?['-I',path.join(packageRoot,'runtime/verify_release.py'),configFile]:['-I','-m','study_errorbook.server'];
  activeChild=spawn(python,argv,{env,stdio:'inherit'});
  for(const signal of ['SIGINT','SIGTERM'])process.on(signal,()=>activeChild?.kill(signal));
  activeChild.on('error',()=>{say('MCP启动失败；请运行doctor或setup排查。');process.exitCode=1;});
  activeChild.on('exit',(code,signal)=>{process.exitCode=code??(signal==='SIGINT'?130:1);});
}
main().catch(error=>{say(error instanceof SyntaxError ? '运行包或client.json格式错误；请检查配置，不要填写凭据到client.json。' : error.message || 'client启动失败，请运行setup检查网络和缓存。');process.exitCode=1;});
