# 0.1.1 迭代门槛与进展

状态：诊断与实现中。沿用原库 `contest-mvp-20261023`，不另建工作树。拟交付 `study-errorbook@0.1.1` 候选，不覆盖已发布0.1.0；发布前另报包与验证证据。

## 失败基线

上一轮真实原生窗口要求导出第2、4题：两条记录均保留，第4题还有两页证据，但它没有已保存变式或净题图；学生包和答案册只含第2题。会话如实报告了缺项，完整交付仍未通过。模型还出现跳过项关联冲突、无变式取题、反馈字段与分页上限等错误，工具拦截后恢复。

原始证据保留在本地 `output/window-acceptance-20261024/`，不修改失败DB、导出包或原会话记录。其HTML入口继续为 `http://127.0.0.1:55798/`。

## 本轮必须达到的门槛

- [ ] 正式学生导出逐题完整，选两题必须覆盖两题；缺少学生材料时不创建少题成品。
- [ ] 跨页题无练习/净题图时，Agent依据已核实题面补全安全文字练习与独立参考答案，完成适用验算及必要复核后再导出；不自动擦除或付费。
- [ ] 学生ZIP不含答案、历史作答、反馈或原卷作答图；答案册与学生材料范围对应。
- [ ] 已有题筛选能避开无可用变式的记录；工具schema和Skill明确易错参数，不靠试错选题。
- [ ] 回归覆盖失败反例、补全后成功、跨页证据、持久化及错误恢复，保留既有安全保障。
- [ ] 新版npm允许列表包通过内容审计、无全局Python/uv的新鲜安装与stdio自检。
- [ ] 真实原生窗口重新执行连续与独立案例；显式测试连接和默认插件开箱分别记录，不互相替代。
- [ ] 最终HTML与实际导出页面完成桌面/窄屏/打印及离线资源QA，更新同一本机入口；真实OCR未测则明确列出。

## 实施顺序

1. 保留失败基线，定位导出遍历、就绪校验与Agent补全调用链。
2. 增加逐题学生材料覆盖守卫和可核验清单，完善Skill补全流程。
3. 改善现有取题筛选及参数schema说明，不新增复杂编排框架。
4. 回归、构建0.1.1候选、隔离安装与产物视觉检查。
5. 报告候选包版本与证据，协调原生窗口及默认安装复验，全部达标后更新报告结论。

不能把模拟缓存当成实时OCR，不把工具自检当成默认插件开箱，也不通过直接改验收库制造成功。

## 已实际实施的阶段记录

- `src/study_errorbook/service.py`：正式导出缺题在创建exports目录前返回student_material_missing；学生清单按记录、文字题与原题图片题计数；支持all/variants/originals意图区分。原题文字练习沿已核实stem并独立保存答案，修改原题仍使旧练习失效。
- `src/study_errorbook/models.py`：区分variant/original_text；澄清跳过关联互斥、用户自判与模拟来源字段。
- `src/study_errorbook/server.py`：公开就绪练习筛选和真实下标，MCP分页schema声明1–50范围，完整透传导出意图。
- `npm-client/bin/client.mjs`：公开profile在默认serve启动前读取；setup选择input/data，exports固定派生data/exports；配置自动备份及restore-config恢复，不存密钥；支持测试profile禁用凭据继承。
- 已执行：全量Python **572 passed in 12.83s**，Node **11 passed**，Python lint通过。源代码核心可独立复核：`uv run pytest -q tests/test_student_export_coverage.py`。
- 本地日志：`output/iteration-0.1.1/python-full.log`、`python-targeted.log`、`node-tests.log`。这些结果不代替原生窗口或默认插件开箱。
- 候选为study-errorbook@0.1.1，尚未发布。最终Python构建指纹为0.1.0+client.0.1.1.f520ae65411ed。
- 剩余：打包与新鲜安装/stdio、profile持久化与恢复、导出页面视觉QA、默认原生窗口复验。无审批阻断；宿主由协调器串行切换，本任务不改宿主配置。

## 当前待发候选

- 包名/版本：study-errorbook@0.1.1，尚未发布；0.1.0保留。
- tarball：`dist/study-errorbook-0.1.1.tgz`，1,107,865字节，18文件。
- SHA256：`1f1c3be21a2a7ac4bf09f8f20cd706e007a2f1a54180b167c68c1d3a0e55e0cd`。
- 构建：`0.1.0+client.0.1.1.f520ae65411ed`。
- 允许列表及内层wheel审计无敏感发现。最终新鲜安装、35工具stdio、逐题导出阻断/补全、profile启动前生效/持久化/备份恢复通过；数据与程序缓存分离，未使用全局Python/uv。
- 正式导出范围为all/variants/originals；archive仅留档，不生成学生ZIP。学生范围缺项不会创建少题成品；明确缺题讨论草稿会标注不完整，不能替代正式交付。
- 真实非秘密状态见本地 `output/iteration-0.1.1/fresh-install-final/report.json`，包审计为 `package-audit.json`。它们是内部安装/stdio证据，非默认插件窗口证据。
- 协调器仍需接入该固定版本的默认npm插件并在新会话核验；实际导出视觉检查进行中。没有发布、修改宿主、调用TextIn或修复旧失败DB。
