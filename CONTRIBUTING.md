# 贡献指南

项目持续迭代，欢迎通过 Issue 与 Pull Request 改进。先用合成或已获授权且可公开的最小样例复现问题；明确源码检查、模拟测试、真实接口调用与宿主验收的区别。

```sh
uv sync --locked --python 3.12
uv run pytest -q
uv run ruff check src tests scripts
uv run python scripts/configure_local.py
uv run python scripts/smoke_mcp.py
```

默认自检不调用付费 OCR。带 probe、live、acceptance、corpus 名称的实验脚本应先读说明；它们可能联网、收费或需要你自行准备的本地材料，不能把它们当作无副作用的安装步骤。

提交前用 `git diff --cached --stat` 和 `git diff --cached` 核对内容。不要提交 `.mcp.json`、`.env`、数据目录、安装记录、原始 API 响应、凭据与私有路径；通过环境变量或本地生成配置适配环境。

提交原创或有兼容授权的变更，保留第三方版权声明。贡献遵循 [MIT License](LICENSE) 和 [转载与使用声明](USE_POLICY.md)。
