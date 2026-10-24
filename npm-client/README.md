# study-errorbook 本地 client

当前公开版本为0.1.0。本目录是 **study-errorbook@0.1.1待发布候选**，尚未上传npm；以下固定1.1命令需发布后才可从registry执行。本地候选验收使用tarball安装后的client，不能把它当默认插件窗口开箱通过。

## 安装与使用

需要Node.js22+/npm及系统tar。插件由仓库`.agents/plugins/marketplace.json`指向固定npm版本；包内包含标准plugin.json、mcp.json和Skill，默认启动`npx --yes study-errorbook@0.1.1 serve`。用户在支持本地插件的桌面端安装后说“整理作业”，不需要解压ZIP或复制MCP配置。桌面实际结果以本轮原生验收记录为准。

首次serve自动从Astral官方固定发布下载uv0.12.17并校验SHA256，准备私有CPython3.12.14和哈希锁定依赖，随后本地stdio运行。不是纯Node；首次需npm/GitHub/PyPI网络。只支持macOS/Linux glibc arm64/x64，本轮实测macOS arm64，原生Windows不支持。stdout仅MCP，程序下载和错误写stderr。

## 启动前选择profile

默认serve启动前读取`~/.config/study-errorbook/client.json`的activeProfile；无配置时沿用原有核心设置。公开文件只接受路径、profile名与credentialsDisabled，不接受密钥。先停止旧进程，选择profile，再用新会话启动插件，不在对话后切目录。

```sh
npx --yes study-errorbook@0.1.1 setup --profile acceptance --input /绝对/合成输入目录 --data /绝对/独立数据目录 --no-credentials
npx --yes study-errorbook@0.1.1 doctor
```

导出目录固定为所选data下的`exports/`，不额外扩大写入根。`--no-credentials`使该profile不继承TextIn凭据，workspace_status应显示textin_configured=false；它不是上传授权，免费模式仍会上传，验收不得调用识别接口。profile只影响新启动进程，不热切换运行中的库。显式进程环境路径优先；必须由实际workspace_status核对client_profile、data_dir、input_root和exports_dir，不能只看setup成功。

未给named profile路径时，默认在`~/.local/share/study-errorbook/profiles/名称/`下准备inbox/data。旧inputRoot单字段配置可继续读取。默认profile沿用现有核心配置；需要自选目录可用setup --input/--data。

## 备份、恢复与边界

setup变更client.json前写`client.json.bak`，首次无旧文件时备份为等效默认`{}`。`restore-config`验证备份并恢复，当前文件另存`client.json.before-restore`，不会修改凭据或宿主连接：

```sh
npx --yes study-errorbook@0.1.1 restore-config
```

恢复后重启插件/新会话才生效；或用`setup --profile default`返回默认profile。不要把密码、OTP或token发到聊天。真实TextIn仅由runtime沿既有环境或核心config.env机制读取，client不创建、复制或打印密钥，也不调整Keychain或2FA。

程序缓存与profile数据分开：macOS为`~/Library/Caches/study-errorbook/client`，Linux为XDG缓存目录。默认学习数据仍是`~/.local/share/study-errorbook`。每个载荷独立安装，完成才写ready；失败可重跑，旧数据不重置。稳定插件配置不依赖npm缓存。`--offline`不回退全局Python/uv。`doctor`只读不安装；`self-test`仅合成stdio测试，不验证窗口体验。`STUDY_CLIENT_HOME`仅供完全独立的开发验收，不是默认插件开箱证据。

## 完整学生材料

正式导出缺题会停止而不生成少题包。没有变式/净题图的已核实文字题，可由Agent补为kind=original_text，题面须等于当前stem，答案独立保存并验算/复核；原题范围不能用变式顶替。学生ZIP与答案册分开，student_materials清单按所选记录和题目类型对账，资源文件数不是题目数。明确讨论草稿才用include_drafts；缺题草稿不是完成交付。

## 构建与原生验收

开发者使用项目Python3.12和uv运行`uv run python scripts/build_npm_client.py`，再在npm-client执行`npm pack --ignore-scripts`。允许列表只含启动器、wheel、锁定依赖、自检、Skill、manifest与许可，无postinstall、资料、数据库、凭据或机器配置。0.1.0不可覆盖；发布0.1.1前须提供新包哈希与验收证据。

本地已有仓库可运行`node scripts/plugin_link.mjs`打印真实本地市场详情链接，脚本不注册/打开宿主。默认插件窗口需协调器按实际安装配置验证；显式测试MCP和CLI自检不得冒充开箱结果。
