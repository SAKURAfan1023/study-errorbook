# study-errorbook 本地 client

npm 名称已确认：`study-errorbook`。这是发布候选，尚未发布到 npm；当前账号未认证。以下 npx 命令在发布成功后才可从公共 registry 使用，本地验收使用生成的 tarball。

需要 Node.js 22+ / npm，以及macOS自带或Linux已有的 `/usr/bin/tar` 解包工具；用户无需解压ZIP或手动安装Python/uv。client保留现有Python核心，首次从Astral官方固定发布下载uv 0.12.17（内置SHA256校验）、自动准备隔离CPython 3.12.14和锁定依赖，随后通过本地stdio运行MCP。不是纯Node实现。只支持macOS/Linux glibc arm64/x64；本轮实测macOS arm64，其他三种组合未实机验证，不支持原生Windows。

## 普通用户步骤

1. 一次准备：`npx --yes study-errorbook@0.1.0 setup`。首次需要网络访问npm、GitHub发布资产及PyPI，可能需数分钟；下载的是运行程序，不会上传作业。需要自选导入目录时加 `--input /绝对/目录`，仅保存该目录设置，不保存凭据。
2. 获取启动配置：`npx --yes study-errorbook@0.1.0 mcp-config`。输出完整MCP JSON，指向已生成的稳定client，不需要手写Python路径；`setup`也返回可注册的plugin_directory。把该配置或插件目录交给支持本地stdio的宿主。ChatGPT桌面的原生注册/安装窗口尚未验收，client本身不会修改全局连接或自动注册。
3. 在已接入宿主中说“整理作业”。默认输入目录为 `~/Documents/StudyErrorbook/inbox`，自动创建；先放入你明确选择的材料。已有核心配置中的输入目录优先，显式 `setup --input` 可选择client输入目录。

第一次真实识别前仍需服务凭据及材料上传/页数授权。client只沿用核心原有环境变量或 `~/.config/study-errorbook/config.env` 读取逻辑，不生成、存储或打印凭据；也不强迫每个本地操作逐次确认。没有真实凭据可做本地导入、已授权缓存读取和合成自检；默认付费识别不会悄悄切到免费上传。

宿主若直接运行client，命令为 `study-errorbook serve`；无参数也运行serve。首次serve可以自动准备环境，但宿主初始化可能超时，因此建议先单独setup。serve的stdout仅输出MCP协议，下载、安装和错误均发到stderr。

## 缓存、升级与排查

- macOS程序缓存：`~/Library/Caches/study-errorbook/client`；Linux为 `$XDG_CACHE_HOME/study-errorbook/client` 或 `~/.cache/study-errorbook/client`。
- 学习数据仍在 `~/.local/share/study-errorbook`，已有核心凭据配置仍在 `~/.config/study-errorbook/config.env`；两者都独立于npm/npx缓存。client目录选择保存在同目录的 `client.json`，只含输入目录，不含令牌。
- 运行 `study-errorbook doctor` 只读检查，不安装或读取/打印凭据。`self-test`复用随包的合成stdio自检，无真实OCR。
- `serve --offline` / `setup --offline` 要求已有完整隔离缓存；不会退回全局Python或uv。冷启动缺失缓存会给出stderr错误，失败不会改动学习数据。
- 每个发布载荷使用独立运行目录，安装完成后才写ready标记。失败可重跑setup，同一机器并发准备通过锁串行，退出后旧锁可恢复。旧学习数据不因升级重置。
- 升级显式改用新npm版本并先setup，再更新宿主配置；旧版本目录保留用于回退。本地stdio、Skill和运行版本仍须实际核对，不由安装包名代替。
- 如需完全隔离的开发验收，可设置 `STUDY_CLIENT_HOME=/新建临时目录`；这会把该次client的程序缓存、配置路径、输入和数据限制在该目录，不修改HOME或当前宿主。

## 源码构建与发布边界

在原仓库运行 `python3 scripts/build_npm_client.py`（开发者环境需要Python 3.12和uv），再进入npm-client运行 `npm pack --ignore-scripts`。生成的npm包只纳入启动器、wheel、锁定依赖、合成自检、Skill、兼容manifest和许可说明；没有postinstall下载钩子，没有材料、数据库、`.env`、机器路径MCP配置或凭据。

公开目标已获确认，但只在完整打包/干净环境验收和敏感文件审计通过后发布。当前未登录npm，需要用户自己在终端执行 `npm login --auth-type=web` 并在浏览器完成认证；随后用 `npm whoami`核对，发布若要求2FA由用户完成。不要把密码、OTP或token发到聊天。此client不承担npm登录或令牌管理。
