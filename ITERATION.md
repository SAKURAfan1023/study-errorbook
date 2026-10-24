# 迭代记录与验收入口

本轮在原仓库的 `contest-mvp-20261023` 分支完成，未另建工作树。基线为 `cb2ab09`。以下是实施与验证摘要，不是完整对话或原始执行日志。

## 持续迭代 0.1.1（进行中）

上一轮窗口整体为部分完成，完整导出缺第4题。本轮门槛与进展见[0.1.1迭代记录](docs/iteration-0.1.1.md)，失败证据保留，尚未宣称修复或重新验收通过。

## 参赛 MVP

- 默认三个入口：整理作业、开始重练、导出材料。保留原图、脱敏、付费授权、防重复请求、复核、验算和练习历史。
- 每页题号清点持久化；题目显示已处理、待定或跳过，提供下一步。同页不同题号不能复用同一条记录，合法跨页续题可关联同一记录。
- 统一 HTTP429/40306 暂停与显式恢复；完成异步任务可只用 GET 恢复丢失缓存；未知付费提交不会自动重发。
- 统一离线 HTML 样式，学生 ZIP 使用允许列表，答案册和反馈独立。产出不是自动 PDF，也不是整卷自动批改。

实现入口：[Skill](skills/study-errorbook/SKILL.md)、[服务](src/study_errorbook/service.py)、[MCP 工具](src/study_errorbook/server.py)、[回归](tests/test_contest_mvp.py)、[MVP 说明](docs/mvp.md)。

## npm 本地 client

[study-errorbook@0.1.0](https://www.npmjs.com/package/study-errorbook/v/0.1.0) 已在官方npm发布。使用薄 Node 启动器，自动准备固定且校验过的 uv、私有 Python 与锁定依赖，保留现有 Python 核心。数据与配置独立于 npm 缓存。用户无需解压 ZIP 或手写 Python 路径，仍需 Node 22+/npm 和支持本地 stdio 的宿主。补齐 `.agents/plugins/marketplace.json` 的固定npm源、包内标准 `plugin.json`/`mcp.json` 与 `scripts/plugin_link.mjs`：窗口路径不要求复制MCP配置；真实registry安装和固定npx冷启动/离线35工具发现已通过；桌面窗口仍待验收。

源码与步骤：[npm client](npm-client/README.md)、[构建](scripts/build_npm_client.py)、[隔离验收](scripts/verify_npm_client.mjs)、[Node 回归](npm-client/test/client.test.mjs)、[输入目录回归](tests/test_client_input_default.py)。

## 已完成的验证

- Python 全量：561 passed；lint 通过。Node 回归：6 passed。
- 在 PATH 不含 Python/uv 的干净环境安装本地 npm tarball，真实下载私有运行时，通过 35 工具 stdio 合成闭环。
- 移走 npm 安装目录后，从生成的稳定配置离线启动通过，测试数据保留。
- tarball 及内层 wheel 审计通过：18 文件，1,102,378 字节；未发现凭据、真实材料、数据库或机器路径。
- npm实际发布已由registry核实，包与批准SHA256/SRI一致。全新无npm认证、无全局Python/uv环境运行固定npx命令，35工具发现和离线重启通过；没有调用TextIn。
- 合成导出的桌面、390px 窄屏、打印和学生 ZIP 离线资源已实际检查；截图来自前一轮 MVP，未将模拟结果当真实 OCR 质量。

## 本地交付文件

下列路径属于本轮原库本地交付，沿用 Git 忽略规则，不会随源码克隆或 npm 下载；可自行运行上述脚本复现。

- `dist/study-errorbook-0.1.0.tgz`
- `output/npm-client-20261024/验收记录.md`
- `output/npm-client-20261024/final-clean/report.json`
- `output/npm-client-20261024/package-audit.json`
- `output/npm-release-20261024/发布核对.md`
- `output/npm-release-20261024/registry-acceptance.json`
- `output/contest-mvp-20261023/验收记录.md`
- `output/contest-mvp-20261023/screenshots/`

npm tarball SHA256：`31c12df09576982336e89c56d096c5c5ce4eea6d48c8cf532268be0a0dd15cf3`。

## 未验证边界

实际运行环境为 macOS arm64 / Node 24.21.0。macOS Intel 和 Linux glibc 尚未实机验证，原生 Windows 不支持。ChatGPT 原生安装窗口未验收，未更改现有宿主连接。真实 TextIn 识别、线上恢复与限流、擦除质量及真实学习效果均不在本轮合成验收结论内。

源码已非force推送至 `origin/contest-mvp-20261023`，main未改动。仓库尚无GitHub Actions工作流，以上为实际本地验收，不声称远端CI通过。
