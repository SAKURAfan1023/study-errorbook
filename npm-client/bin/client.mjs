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
function validateConfig(saved) {
  if(!saved || typeof saved!=='object' || Array.isArray(saved) || Object.keys(saved).some(k=>!['schema','inputRoot','activeProfile','profiles'].includes(k))) throw new Error('client.json只接受公开profile配置，不接受凭据或未知字段。');
  const validName=name=>typeof name==='string' && /^[a-z][a-z0-9-]{0,39}$/.test(name);
  const validPath=value=>typeof value==='string' && path.isAbsolute(value);
  if(saved.schema!==undefined && saved.schema!==1)throw new Error('不支持此client配置版本，请先恢复备份。');
  if(saved.inputRoot!==undefined&&!validPath(saved.inputRoot))throw new Error('inputRoot应为绝对目录。');
  if(saved.activeProfile!==undefined&&!validName(saved.activeProfile))throw new Error('profile名称须为1–40位小写字母、数字或连字符，以字母开头。');
  if(saved.profiles!==undefined && (!saved.profiles||typeof saved.profiles!=='object'||Array.isArray(saved.profiles)))throw new Error('profiles应为公开路径配置对象。');
  for(const [name,profile] of Object.entries(saved.profiles||{})) {
    if(!validName(name)||!profile||typeof profile!=='object'||Array.isArray(profile)||Object.keys(profile).some(k=>!['inputRoot','dataRoot','credentialsDisabled'].includes(k)))throw new Error('profile只接受inputRoot/dataRoot/credentialsDisabled，不接受凭据。');
    if(profile.inputRoot!==undefined&&!validPath(profile.inputRoot)||profile.dataRoot!==undefined&&!validPath(profile.dataRoot)||profile.credentialsDisabled!==undefined&&typeof profile.credentialsDisabled!=='boolean')throw new Error('profile路径须为绝对目录，credentialsDisabled须为布尔值。');
  }
  return saved;
}
function profileSettings(saved) {
  const name=saved.activeProfile||'default';
  const selected=saved.profiles&&Object.hasOwn(saved.profiles,name)?saved.profiles[name]:undefined;
  if(name!=='default'&&!selected)throw new Error('activeProfile不存在；请setup选择已有profile或restore-config恢复。');
  return {name,profile:selected||{},input:selected?.inputRoot||saved.inputRoot||inboxDefault};
}
async function options() {
  const changes={};
  for(let i=0;i<args.length;i++) {
    const key=args[i];
    if(key==='--offline')continue;
    if(command==='setup'&&key==='--no-credentials'){changes.credentialsDisabled=true;continue;}
    if(command==='setup'&&['--profile','--input','--data'].includes(key)) {
      const value=args[++i];if(!value||value.startsWith('--'))throw new Error(key+'需要值。');
      changes[key]=value;continue;
    }
    throw new Error('不支持的参数；使用 --help 查看用法。');
  }
  const file=path.join(configRoot,'client.json');
  const saved=validateConfig(await exists(file)?JSON.parse(await fs.readFile(file,'utf8')):{});
  if(Object.keys(changes).length) {
    const name=changes['--profile']||saved.activeProfile||'default';
    const home=customHome?path.join(customHome,'profiles',name):path.join(os.homedir(),'.local/share/study-errorbook/profiles',name);
    const previous=(saved.profiles&&Object.hasOwn(saved.profiles,name)?saved.profiles[name]:undefined)||(name==='default'?{...(saved.inputRoot?{inputRoot:saved.inputRoot}:{})}: {inputRoot:path.join(home,'inbox'),dataRoot:path.join(home,'data')});
    const profile={...previous};
    if(changes['--input'])profile.inputRoot=path.resolve(changes['--input']);
    if(changes['--data'])profile.dataRoot=path.resolve(changes['--data']);
    if(changes.credentialsDisabled)profile.credentialsDisabled=true;
    saved.schema=1;saved.activeProfile=name;saved.profiles={...saved.profiles,[name]:profile};validateConfig(saved);
  }
  return {file,saved,changed:Object.keys(changes).length>0,...profileSettings(saved)};
}
async function saveConfig(configuration) {
  await fs.mkdir(configRoot,{recursive:true,mode:0o700});
  if(await exists(configuration.file)){await fs.copyFile(configuration.file,configuration.file+'.bak');await fs.chmod(configuration.file+'.bak',0o600);}
  else await fs.writeFile(configuration.file+'.bak','{}\n',{mode:0o600});
  const temporary=configuration.file+'.'+process.pid+'.tmp';
  await fs.writeFile(temporary,JSON.stringify(configuration.saved,null,2)+'\n',{mode:0o600});
  await fs.rename(temporary,configuration.file);
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
    console.log('study-errorbook [serve | setup [--profile NAME] [--input DIRECTORY] [--data DIRECTORY] [--no-credentials] [--offline] | doctor | restore-config | mcp-config | self-test]\n默认serve在启动前加载所选profile；data下的exports为导出目录。配置不含密钥，变更自动备份，restore-config恢复。serve stdout仅MCP协议。');return;
  }
  if(!['serve','setup','doctor','restore-config','mcp-config','self-test'].includes(command))throw new Error('未知命令；使用 --help 查看用法。');
  if(!['darwin','linux'].includes(process.platform)||!['arm64','x64'].includes(process.arch))throw new Error('Python核心目前仅支持macOS/Linux glibc arm64/x64；不支持Windows。');
  const state=await payload();
  if(command==='restore-config') {
    if(args.length)throw new Error('restore-config不接受参数。');
    const file=path.join(configRoot,'client.json'),backup=file+'.bak';
    if(!await exists(backup))throw new Error('没有client.json.bak；未改当前配置。');
    const saved=validateConfig(JSON.parse(await fs.readFile(backup,'utf8')));
    if(await exists(file)){await fs.copyFile(file,file+'.before-restore');await fs.chmod(file+'.before-restore',0o600);}
    const temporary=file+'.'+process.pid+'.tmp';await fs.writeFile(temporary,JSON.stringify(saved,null,2)+'\n',{mode:0o600});await fs.rename(temporary,file);
    console.log(JSON.stringify({restored:true,profile:profileSettings(saved).name,config:file,note:'仅恢复公开client配置，未改凭据或宿主；新启动的serve生效。'},null,2));return;
  }
  const configuration=await options();
  const profile=configuration.profile;
  const data=process.env.STUDY_DATA_DIR||profile.dataRoot||(customHome?path.join(customHome,'data'):null);
  const inbox=process.env.STUDY_INPUT_ROOT||configuration.input;
  const locations={profile:configuration.name,input:inbox,data,exports:data?path.join(data,'exports'):null,credentials_disabled:!!profile.credentialsDisabled};
  if(command==='doctor') {
    console.log(JSON.stringify({installed:await ready(state),runtime_version:state.manifest.runtime_version,cache:cacheRoot,config:configRoot,...locations,transport:'local stdio',note:data?'配置路径由下次serve加载；workspace_status核对实际路径。':'默认data沿用runtime现有核心配置；workspace_status核对实际路径。'},null,2));return;
  }
  await prepare(state);
  await fs.mkdir(inbox,{recursive:true,mode:0o700});
  if(command==='setup') {
    if(configuration.changed)await saveConfig(configuration);
    console.log(JSON.stringify({ready:true,runtime_version:state.manifest.runtime_version,...locations,config:configuration.file,backup:await exists(configuration.file+'.bak')?configuration.file+'.bak':null,plugin_directory:path.join(state.folder,'plugin'),note:'默认插件serve会读取该profile；已运行进程须重启才生效。不注册宿主、不保存凭据、不上传材料。'},null,2));return;
  }
  const configFile=path.join(state.folder,'plugin/.mcp.json');
  if(command==='mcp-config'){console.log(await fs.readFile(configFile,'utf8'));return;}
  const env={...process.env,STUDY_CLIENT_INPUT_ROOT:inbox,STUDY_CLIENT_PROFILE:configuration.name};
  if((profile.inputRoot||configuration.saved.inputRoot)&&!env.STUDY_INPUT_ROOT)env.STUDY_INPUT_ROOT=inbox;
  if(data&&!env.STUDY_DATA_DIR)env.STUDY_DATA_DIR=data;
  if(customHome)env.STUDY_CONFIG_FILE ??= path.join(configRoot,'config.env');
  if(profile.credentialsDisabled){env.TEXTIN_APP_ID='';env.TEXTIN_SECRET_CODE='';env.STUDY_CONFIG_FILE ??= path.join(configRoot,'profiles',configuration.name,'no-credentials.env');}
  const python=path.join(state.folder,'runtime/bin/python');
  const argv=command==='self-test'?['-I',path.join(packageRoot,'runtime/verify_release.py'),configFile]:['-I','-m','study_errorbook.server'];
  activeChild=spawn(python,argv,{env,stdio:'inherit'});
  for(const signal of ['SIGINT','SIGTERM'])process.on(signal,()=>activeChild?.kill(signal));
  activeChild.on('error',()=>{say('MCP启动失败；请运行doctor或setup排查。');process.exitCode=1;});
  activeChild.on('exit',(code,signal)=>{process.exitCode=code??(signal==='SIGINT'?130:1);});
}
main().catch(error=>{say(error instanceof SyntaxError ? '运行包或client.json格式错误；请检查配置，不要填写凭据到client.json。' : error.message || 'client启动失败，请运行setup检查网络和缓存。');process.exitCode=1;});
