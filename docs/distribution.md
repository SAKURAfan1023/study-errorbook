# 独立安装包

解压发布 ZIP 后，在解压目录执行（需要 Python 3.9+ 和 uv）：

```bash
python3 install_release.py --target "$HOME/.local/share/study-errorbook-runtime/release-1"
```

安装器校验文件清单及 SHA-256，使用 uv 安装 Python 3.12、固定版本的依赖与应用 wheel，再用真实 MCP 子进程测试导入、图片、导出和跨进程读取。测试仅使用临时合成材料，不调用 TextIn，不读取个人配置。依赖首次安装可能需要联网；发布包不含 Python 或完整离线依赖。SHA-256用于损坏检查，不是发布者签名；仅运行可信来源的安装器。

当前支持范围是 macOS/Linux 本地 stdio 服务，macOS 已实测，Linux 待实测；Windows 因文件锁依赖尚不支持。目标目录必须不存在，已有或失败的安装不会被覆盖。失败后选择新的目标目录重试；成功标志为 installation.json。升级也安装到新目录，验证后再切换宿主配置，保留旧运行目录便于回退。

安装完成后得到：

- `mcp.json`：通用 MCP 配置，供支持本地 stdio 的 Agent 宿主导入。仅包含运行路径，无密钥。
- `plugin/`：Codex 插件源，含 Skill 和同一 MCP 配置；用 Codex 的本地插件/marketplace 安装流程注册。安装器不会修改任何宿主全局配置。
- `runtime/`：非 editable 的独立 Python 环境，不依赖原项目或 ZIP 解压目录。
- `verify_release.py`：可重复执行的临时合成数据自检。

目标运行目录与 uv 管理的 Python 需保留；虚拟环境本身不能任意搬动。换电脑或移动运行目录时重新运行安装器、重新导入新配置。安装后可以删除或移动原源码和解压包。其他 Agent 宿主的字段适配及实际工具加载仍须各自验证。

## 升级后核对当前Agent是否已加载

安装文件、自检子进程和当前任务中的MCP服务是三个不同的检查对象。2026-09-25实测：已安装缓存的自检支持practice-only.zip，当前Codex任务仍返回旧版的三份HTML；虽然都显示30个工具、基础版本0.1.0，能力仍不同。查看新的Skill内容也不能证明MCP服务已同步更新。

先保留当前工作，在新任务中试用已更新的插件。若仍显示旧工具说明或旧响应，再按宿主的插件更新流程重新加载或重启应用，随后重新验证；不要直接杀死不明MCP进程、重复安装相同版本或据缓存目录名称宣称已生效。OpenAI官方[插件打包说明](https://developers.openai.com/plugins/build/plugins)说明本地插件更新后需更新市场指向的目录并重启桌面应用；本机随附的plugin-creator更新指引还建议在新任务中测试。两者都是加载建议，是否生效仍以实际调用为准。

验证时先workspace_status，将runtime_version与目标运行目录installation.json中的runtime_version比较。新发布wheel使用0.1.0+build.<UTC时间戳>，不同于插件缓存的+codex后缀；两者基础版本不变。缺失、null或不匹配时，不宣称当前服务已加载目标构建。此字段在服务模块加载时读取包元数据，不是签名或文件完整性证明；源码开发安装通常只有基础版本。版本匹配后，再读取指定的已有测试记录，不重新识别或伪造用户复核。对允许导出的记录调用export_errorbook；待复核诊断仅显式include_drafts=true。新版返回practice_bundle_path和practice_bundle摘要，路径应存在，ZIP中应有practice.html与assets，且没有answers.html、errorbook.html和原卷图。没有练习的记录只能验证空包协议，不能据此声称完成真实学习流程。若当前响应缺少这些字段，记录实际结果，不把完整目录临时压缩后冒充插件生成的练习包。

独立运行verify_release.py证明安装环境可用；上述原生调用证明当前Agent入口实际行为。自检成功但原生调用仍旧时，应先定位加载层，而不是修改学习内容、配置付费凭据或再次上传材料。

用户数据默认保存在 `~/.local/share/study-errorbook`，凭据配置在 `~/.config/study-errorbook/config.env`，都与安装目录分离；安装器不创建或覆盖这些文件。导入目录可在该配置中通过 `STUDY_INPUT_ROOT` 指定。付费接口需配置 TextIn 凭据；免费识别仍上传材料，必须明确选择。Skill 内包含完整工作流和复核边界。

开发者构建：在项目中执行 `uv run python scripts/build_release.py`。发布清单仅纳入 wheel、锁定依赖、安装/验证脚本、说明、插件 manifest、Skill入口与五份明确列出的参考文件，不复制 `.venv`、材料、数据库或本机 `.mcp.json`。浏览器公式字体和许可文件包含在 wheel 中。

构建器在临时目录复制src与pyproject.toml，仅修改临时副本的wheel版本；源码、开发环境元数据和uv.lock不改。验证脚本须用目标runtime/bin/python -I运行，它会比较MCP响应和自身环境中的包版本，配置错误指向其他构建时拒绝通过。installation.json保留完整runtime_version与wheel SHA256。


## 继续已有任务而不重复识别

使用[已有结果恢复流程](recover-existing-recognition-2026-09-26.md)，从材料名查找已保存的各模式任务，再读取缓存和可用回执。两次安装版stdio会话已验证免费/VLM结果、输入及响应哈希回读；未新增API调用。先核对data_dir，公开诊断库与日常学习库不会自动合并。
