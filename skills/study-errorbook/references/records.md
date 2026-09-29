# 记录契约与调用样例

工具以 MCP 实际发现的 schema 为准。工具前缀可能因宿主而异。所有 ID 必须来自真实工具返回，不能照抄本文示例值。

## 最小错题草稿

```json
{
  "document_id": "工具返回的 document_id",
  "question_label": "第 1 题",
  "subject": "数学",
  "stem": "展开 2(x+3)",
  "student_answer": "2x+3",
  "evidence_ids": ["工具返回的 evidence_id"],
  "teacher_verdict": "uncertain",
  "agent_verdict": "wrong",
  "annotations": [
    {
      "text": "作答旁红色叉号",
      "kind": "cross",
      "author": "unknown",
      "color": "red",
      "evidence_ids": ["同一原卷的 evidence_id"]
    }
  ],
  "analysis": {
    "explanation": "结果中常数项未乘以2，可能漏用了分配律。",
    "evidence_ids": ["对应作答的 evidence_id"],
    "error_type": "分配律漏乘常数项",
    "knowledge_points": ["分配律"],
    "uncertainty": "仅凭最终答案不能区分概念误解与偶发笔误。",
    "diagnostic_question": "你能分别写出2乘括号内每一项的结果吗？"
  },
  "exercises": []
}
```

判定枚举：teacher_verdict 为 correct/wrong/partial/unmarked/uncertain；agent_verdict 为 correct/wrong/partial/unanswered/uncertain。二者独立，不要因一方存在而覆盖另一方。

新增记录不传 record_id，expected_revision=0。返回 id/revision 后，修改使用这两个值。保存本身不代表人工确认。

## 作答来源与判断范围

`student_answer` 可以保存可见草稿，不表示每个式子都是最终提交。利用现有文字字段注明来源与范围，不添加 schema 中不存在的字段：

- `student_answer` 忠实记录“草稿可见”“答题栏可见”，或明确标为“外部提交标注（来源……）”。不同来源冲突时分别保留；未知内容不补写。参考正确答案写入分析，不混入学生作答。
- `analysis.explanation` 分别说明可见步骤、题目所问结果和能够下的结论；`analysis.uncertainty` 写清缺失条件、未见最终提交或参考冲突。给出 `agent_verdict` 时说明判断范围，不让“某一步正确”变成“整题正确”。只见正确中间量而未见所问结果时，整体保持 uncertain，不能据此认定学生未答或已答错；若可见步骤已错，可以说明该步骤错误，但不编造最终提交。
- `teacher_verdict` 只描述有依据的卷面批改；数据集的错误标签、参考答案和未知作者的红叉都不能直接当成老师意见。上例的红叉作者未知，因此 teacher_verdict 为 uncertain，Agent 可以独立判断算式错误。
- 等价验算回答“表达式是否相等”，不判断是否满足“完全因式分解”“最简形式”或实际所问数量。等价但未完成题目要求时，分别说明成立的部分与尚缺步骤。题目条件缺失时只给适用条件明确的结论，不从参考解反填原题。

例如，工程题问实际日量，草稿把原计划日量设为 x 并正确求得 x=500：保存“草稿可见：x=500（原计划日量）；未见实际日量的最终填写”，整体暂为 uncertain；分析另写实际日量需按增加比例换算。若另有外部提交标注500，应注明来源后再判断该提交，而不声称图像证明学生把500填进答案栏。需要澄清时询问缺失的答题区域或条件；不因缺少最终值就丢掉已有步骤证据。

## 返回值与修订

`save_study_record`和`read_study_record`均返回外层状态加内层题目：

| 位置 | 用途 |
| --- | --- |
| `result.id`、`result.revision` | 记录ID与此份内容的版本；更新前读取当前版 |
| `result.record` | 补齐可选默认字段后的完整StudyRecord；题干为`record.stem`，分析为`record.analysis`，练习为`record.exercises` |
| `result.review_status` | 用户复核状态，不能写入StudyRecord来授权纳入 |
| `result.exercise_validation`等 | 读取当前版时的派生检查；保存返回和指定版本快照不包含这些实时检查 |

保存的`record`参数是**完整替换，不是局部patch**。先读取当前版，复制其`record`，只改需要修订的字段，再把整份题目传回；不要把含id/revision等字段的外层结果作为record，也不要只提交几个必填字段而意外丢掉原作答、批注或分析。省略的可选字段按模型默认值保存。

以下为调用顺序示意，`current`和`updated`表示真实工具结果，不是工具参数中的字符串：

```text
current = read_study_record(record_id=真实ID)  # 省略revision，读取当前版
updated = current.record的副本
updated.question_label = 用户指定的新题号
save_study_record(record=updated, record_id=current.id,
                  expected_revision=current.revision)
```

仅改题号或学科仍会产生新版本并回到needs_review；原作答、批注和分析/练习在完整传回时保留。修改题干、作答、主证据、批注、卷面批改或Agent判断则会清空分析和练习，即使本次同时传入新分析；先保存修正依据，读取返回的新版本，再基于新依据分析并完整保存一次。发生版本冲突时回读核对，不能只调大expected_revision覆盖他人的修订。

历史读取中的`revision`属于所选旧内容，`current_revision`只是当前版本提示。不要把旧record配上current_revision直接写回；恢复旧文字也须先读取当前版、合并所需修正后保存。历史正文不随current_revision变化而改写。

## 变式记录

先检查 `expression="3*(x+4)"` 与 `proposed_answer="3*x+12"`。如果 equivalent，可保存：

```json
{
  "prompt": "展开 3(x+4)",
  "answer": "3*x+12",
  "explanation": "3分别乘x和4，得到3x+12。",
  "targets_error": "巩固分配律中每一项都要相乘",
  "check_id": "实际验算返回的 id"
}
```

answer 必须与被检查的 proposed_answer 完全一致，变化后重验。工具不自动确认 prompt 的语义等于被验算的 expression，Agent 必须检查这一点，无法确定时保留待审核。

每道练习的`prompt`、`answer`、`targets_error`均必填；`explanation`和`check_id`可省略。未验算或不适用验算时省略check_id，保留待复核，不编造验算ID。

## 练习检查与正式导出

read_study_record的exercise_validation逐题返回expression_verified（仅所给表达式等价）、user_reviewed（当前版本用户复核）、not_equivalent或needs_review。answer_ready仅表示练习检查条件，不能替代整题纳入或教学效果验收。

用户明确核对了具体练习题干与答案后，review_study_record的include可带approved_exercise_indices，例如第一道为[0]，user_note忠实记录意见。名单默认空，不接受重复、越界或布尔下标；修改记录或重新复核会使旧人工名单失效。验算明确不等价时先修订，不能用确认覆盖。仅确认原题为错题，不表示这些练习已经复核。草稿仍可include_drafts=true导出，contains_drafts标识待复核内容。

## 坐标与材料

`Region` 只有 page 和 box，page从1开始，box是原页的 [left,top,right,bottom]，各值0–1。示例 `[0,0,1,1]` 是完整原页。非线性矫正、旋转、印刷版式识别可能改变 OCR 参照图，先看原页再划区域。

read_document另含review_hints：统计未截断objects中的formula/handwriting数量，区分char_details缺失、非法、空与可用，统计有效/缺失/非法分数，按有效0–1分数升序列出至多5项（同分按数组位置）。detail_index指char_details数组位置，字符预览最多16字符；lowest_confidence_truncated表示还有其他有效分数。它不表示已找全错误，不统计准确率，不决定复核状态；高分仍须看图。当前最多返回5000字符/元素、8个行内对象，工具结果中有截断提示；不足时查看原图与本地完整解析结果，不能把截断内容当成题干结束。

read_document的page_summary与元素分页无关，空元素列表仍返回。requested_pages来自已保存任务，reported_success_count为0–20的整数，否则null；count_matches_requested仅比较两项，不证明识别完整。pages最多20项，含page_number、reported_status、metadata_entries与实际elements中的element_count；不下载或返回page_image_url。缺失、重复元数据与没有可用状态文本分别列出；重复条目不擅自选择某一个状态，过长/非法状态不回显。non_success_pages仅表示明确状态字符串不是Success，不将未知状态一律判为失败。pages_without_elements须回看原图，无元素不代表空白或漏答。invalid_metadata_entries/unassigned_elements统计非法或超出已请求范围的页号，布尔值不是页码。旧任务requested_pages缺失时只列已见1–20页号，不根据最大页号猜整卷页数。该摘要不改写原始缓存、任务状态或触发重传；全部Success仍须逐行核对转录。
## 原题净题关联与复核

StudyRecord可包含clean_evidence_ids（默认空，最多20个），按题面顺序填写已经make_clean_copy成功的来源证据ID。必须是该记录主evidence_ids的子集，不重复；跨题、只属于批注/分析或未完成的擦除图不能直接关联。

保存后read_study_record返回clean_copy_validation：source_evidence_id、sha256、status、ready。对照原图确认题干、公式和图形完整且无作答残留后，review_study_record可带approved_clean_copies={source_evidence_id:sha256}；仅include允许，记录修订和重新复核均须提交完整当前确认名单。此确认与approved_exercise_indices分别作用于净题和变式答案，不能互相替代，均是宿主转述。

export_errorbook在有关联净题时额外返回original-practice.html。正式导出要求图像当前哈希与本版本确认一致；草稿可保留待视觉复核标签，文件缺失时两种导出都拒绝。manifest记录净题来源ID、文件名、sha256及复核状态；未选净题的记录不进入原题重练册。无净题时仍只返回原有三册。

## 再次作答与历史

record_practice示例：原题用record_id、expected_revision、response、practice_kind="original"；变式使用practice_kind="exercise"（默认）及exercise_index=0。assistance默认为unknown；independent表示用户报告独立完成，hinted为使用提示，solution_seen为看过解析，均由宿主转述。不能把教师原批改或Agent对旧作答的分析当作这次新作答的结果。

建议带attempt_key（1–128字符，例如本次提交生成的UUID）。相同record_id与标识、相同参数重试返回相同作答ID；即使题目随后修改也能取回原提交结果。同一标识改参数会报attempt_key_conflict，新一次真实作答才换新标识。不带标识时每次调用都会新增记录，不能据此声称已经去重。

新作答保留source_revision与source_snapshot：题号、题干/变式题面、原证据和当时复核状态；变式包含当时参考答案、解释及验算范围。原题不从旧学生作答推断标准答案，reference_answer=null。保存不会自动判分或变为已掌握。

read_practice_history默认按保存顺序返回最多20条摘要，携带next_before继续；不要使用offset或从时间戳猜游标。include_details=true返回完整作答/快照，每页序列化输出上限80000字符，可能不足limit条；继续到next_before=null可完整读取。翻页期间新记录不会挤动旧页，刷新首页可看到新作答；total为查询时当前数量。旧数据缺少快照时snapshot_available=false，帮助情况为unknown，不自动回填。read_study_record的recent_practice_attempts也是摘要，完整response须用历史详细读取。

## 保存再次作答后的反馈

review_practice_attempt参数为attempt_id、expected_feedback_revision和feedback。先read_practice_feedback取得current_feedback_revision；首次0，不能用题目revision替代。

feedback字段：agent_verdict=correct/wrong/partial/uncertain，feedback为1–4000字符的具体说明，next_step为1–2000字符的可执行下一步；user_verdict默认unreported，其余可选值同Agent判断。只有真实用户意见时填user_verdict及非空user_note（最多2000字符），不得伪造用户认可。本次没有新增用户意见时，提交unreported和空user_note，工具会保留此前用户判断及原文；它们不是清空指令。仅当用户明确撤回时，设置withdraw_user_opinion=true、user_verdict=unreported并用非空user_note忠实记录撤回意见。新用户判断或明确撤回才改变这部分内容，历史版本始终保留。

输出user_opinion_updated表示本次是否更新用户意见；user_opinion_source_revision指向最近一次表达或撤回的反馈版本，未表达为null。仅改Agent解析时沿用来源版本，不表示用户再次发言；withdraw_user_opinion仅表示该版本是否执行撤回。旧数据缺少来源字段时，只指向最后已知意见所在版本，不推断更早原始出处，也不自动恢复旧版本已清空的意见。

反馈针对attempt.source_snapshot和response，题目修改不把旧作答改挂到新题面。没有快照的旧作答拒绝自动补判。成功后作答的grading_status变为feedback_recorded，仅表示有反馈，不等于正确、用户认可或已经掌握；feedback_summary分别给出两类判断。原作答、时间、提示使用情况和快照不变。

read_practice_feedback返回指定作答的全部反馈修订，默认5条、最多20条且80000序列化字符；用next_before_revision继续直到null。current_feedback_revision是查询起点的当前版本；并发新反馈可能导致下一次写入冲突，此时回读核对，不自动改版本重写。反馈正文独立读取，避免练习历史因累积反馈无限膨胀。


## 日期复习队列

update_review_plan独立管理每题一个当前计划，使用record_id、expected_record_revision、expected_plan_revision和change。首次计划版本为0；先read_review_plan读取当前版本，不能用题目版本或反馈版本替代。每次变更保留旧版本，read_review_plan默认5条、最多20条，用next_before_revision继续。read_study_record也带最新计划及current_plan_revision。

schedule示例change={"action":"schedule","due_at":"2030-01-02T09:00:00+08:00","user_note":"忠实记录用户明确安排的日期"}。示例日期不能直接当作真实安排。due_at必须含时区；原题默认practice_kind=original，不传exercise_index。变式用practice_kind=exercise和从0开始的exercise_index。题目必须已被用户include，变式需要适用验算或当前版本明确复核。再次schedule会替换当前日期/目标并产生新版本，不代表已经练习；旧计划历史仍保留。

complete使用change={"action":"complete","attempt_id":"真实新作答ID","user_note":"真实完成指示"}；cancel只传action和真实user_note。完成要求该作答在最近一次安排之后保存，按同一数据库中持久化插入顺序核对，不使用可能相同或回拨的系统时间比较先后；反馈修订不会改变作答的原保存顺序。题目、题目版本、原题/变式下标均与计划一致且有题面快照。可以提前完成；保存时间不是独立验证的实际学习时间。提示作答也可记录为完成，但completion_assistance忠实保留，未评价的作答仍needs_review，不自动判定掌握。题目修订或被移出后，旧计划不能完成，只能取消或核对新版后重新安排。

read_review_queue默认status=due、as_of=当前时刻。scheduled包含未到期的有效安排，outdated表示题目版本/纳入状态变化，completed/cancelled是最近计划的终态，all包含上述当前状态。已完成或取消的历史不会因为题目后续改变而改写。结果按绝对到期时间排序，单页最多20条，默认仅含中性display_label、日期、目标、记录ID及版本，不返回题干、答案、计划说明或自由标签。整理题库时可显式include_labels=true，额外返回当前question_label/subject；这些自由文字可能含答案或错因，不能在独立重练前展示。响应include_labels说明本次选择。offset分页返回next_offset；跨页期间计划可能变化，修改后刷新首页，不承诺跨调用一致快照。完整计划说明可能含提示，不在独立答题前直接展示。

日期由用户决定，不是FSRS等自适应算法输出；没有后台任务、通知或保证学习效果的固定周期。Agent可以结合历史作答和反馈讨论日期，但不得把讨论建议写成用户已决定的计划。


## 上传前指定区域遮盖

redact_document(document_id, regions)接受1–100个Region，坐标基于本地原页方向，每项包含page和归一化box。例如page=1、box=[0,0,1,0.1]表示第一页顶部10%，只能按实际隐私区域选择，不要机械照抄。页码不能超过文档页数；重复或乱序的相同区域复用同一副本。它不调用TextIn，不自动识别姓名、学号或其他敏感信息，也不会覆盖原件。

返回新的document_id、通用文件名redacted.png/pdf、sha256、redaction来源及范围。PNG只重编码像素；PDF所有页面重新生成为图片，保留可视页面尺寸和方向，不复制原文本层、表单、附件或元数据。PDF像素上限沿用证据渲染（最长边2400、最多2倍页面点尺寸），失去文本选择和交互，小字可能降质；超过20MiB拒绝登记副本。须检查全部页面，既看区域是否遮全，也看公式、图形和题干是否被误遮。无法判断时保留待检查，不声称“已完全匿名”。

后续read_evidence、parse_document、save_study_record均使用副本的新ID，证据归属也属于副本。不会自动迁移原件已有的OCR、错题或导出，原件和此前上传仍存在。副本完整性哈希在重复读取和解析请求校验；文件缺失或变化会拒绝缓存复用/上传。工作区应保留本地原件，副本操作不是删除、服务商撤回或访问权限管理。

解析及显式重提把本次检查后的字节直接交给上传层，不再重新打开源路径。新尝试的submitted_source_sha256和submitted_source_bytes描述准备提交的内容；不能据此宣称服务商已接收。显式重提读取新的快照，旧尝试保留原值，旧版本任务不补造这两个字段。普通图片规范化前的document.sha256可能不同，勿当作上传哈希。parse_document即使复用任务也先检查当前文件；仅查看已保存结果可用get_job/read_document。


## 删除影响预览与本地执行

preview_document_deletion(document_id, offset=0, limit=30)只读生成清单，每页1–50项，使用next_offset继续。database列出候选数据库项；file列出关联目录内文件、字节数及sha256；export列出受影响题与同册其他题；retained_shared_check标明仍被别的记录/历史引用的验算；unresolved说明无法归类、符号链接或不可读内容。不会返回题干、原答案或反馈正文。

清单沿redaction.source_document_id包含所有后代，删除副本的预览不会向上包含原件。原题与历史、作答快照用于查找相关记录；导出按题目及证据引用关联，混合册不能仅删清单文件或假装不影响其他题。已登记关联和关联目录之外、已丢失全部关联的内容不保证可定位；输入目录原文件、手工外拷、备份及上游服务数据不在范围。

inventory_complete只表示本次已知关联扫描未发现缺项；不是可以安全并发执行删除的证明。inventory_digest绑定数据库快照和所读文件内容，任何页返回不同摘要时需从头读取；扫描不是持续锁定，结束后仍可能发生变化。recorded_remote_states不证明远端或本地任务已停止。上限20000条数据库项、10000个扫描条目、256MiB文件内容；超过限制直接报错，不把部分结果当完整清单。

operation固定preview_only、deleted固定false。本工具不清除数据库、WAL、图片或目录，不调用服务商，不产生删除授权。delete_document是单独的破坏性工具，必须有用户明确指令及当前完整摘要。


执行签名delete_document(document_id, inventory_digest, acknowledge_delete)。严格布尔值true仅表达宿主转述的指令，不是用户身份认证；用户明确要求的删除范围已包括所有影响时直接执行，混合导出等新增影响超出原指令时需先明确。执行前取工作区独占锁，在SQLite写事务内重扫摘要；其他会话存活或本会话有远端调用时拒绝，不强行终止它们。不要通过直接文件操作或旧版服务绕过。

关联行删除与恢复清单在同一数据库事务中提交，然后逐个核对文件哈希并删除，仅清理空目录。进程退出或I/O失败后，重连读取持久化清单继续；变化或清单外文件会保留并阻止恢复。恢复失败时工作区不对外开放，需检查本地异常，不能清掉恢复表来掩盖问题。恢复期间只能使用当前或更新的服务版本，旧版本不识别待恢复清单。

开启secure_delete，完成后检查WAL截断返回值；不运行可能改变rowid的VACUUM，以保留作答历史游标。范围仅限此次关联记录和工作区文件，不保证旧版本遗留空闲页、系统快照、SSD历史块、备份、输入原件或服务商副本清除。已上传的远端任务可能继续，工具不取消它们。保存文档ID哈希、摘要、数量和范围说明组成的最小回执；同摘要重试返回回执，材料重新导入后旧回执拒绝复用。connection_reopen_required=true时重新连接服务。


## 错题查询与筛选

query_errorbook(search="", offset=0, limit=20, review_status=null, subject=null, document_id=null, knowledge_point=null, agent_verdict=null)复用原工具入口，每页最多50条且摘要项JSON合计最多12000个UTF-8字节（json.dumps、ensure_ascii=false）；预算包含字符转义，不截断单项来凑满条数。limit是条数上限，少于limit也可能还有结果，必须按next_offset继续至null。元数据及MCP兼容双份内容另占空间，该预算不是整个协议包或模型token上限。search最多200字符，为Unicode casefold后的字面子串；查当前题号、学科、题干、原作答、批注文本、分析/知识点和练习内容。不查JSON字段名、存储ID、判定枚举、旧修订、作答历史或反馈。%和_不是通配符，也没有Anki查询语法。

可选筛选均为精确匹配、取交集。review_status为needs_review/include/exclude/uncertain；subject最长100字符，document_id最长128字符，knowledge_point最长200字符且匹配知识点列表中的完整一项。省略表示不限，空白值拒绝。agent_verdict可选correct/wrong/partial/unanswered/uncertain，匹配当前保存的Agent判断；review_status属于用户复核流程，不能混为同一条件。例如找模型判错且用户已纳入的数学题，用agent_verdict="wrong"、review_status="include"、subject="数学"；找模型仍无法判断的草稿，用agent_verdict="uncertain"、review_status="needs_review"。只筛选已保存值，不重新判题、不检查掌握程度、不自动改状态；改判后旧判断不再命中当前查询。

例如“数学里已纳入的分配律题”使用subject="数学"、review_status="include"、knowledge_point="分配律"，返回total及next_offset。若学科/知识点的具体登记名称不确定，先关键词找候选并读取记录，不猜测额外分类。返回的是按首次保存顺序倒序的当前摘要，保存修订不自动置顶；增删改会影响offset，变更后从首页刷新。服务流式扫描并只保留当前页摘要，精确总数仍需扫描匹配候选，不是全文索引或恒定时间搜索。


顺序校验复用当前objects表的rowid，计划/作答均由服务插入、更新保留rowid，且比较位于写事务中；不依赖进程内计时器，重启后仍有效。直接重建数据库、手工调整rowid、使用INSERT OR REPLACE搬移旧记录或运行改变rowid的外部维护不属于支持流程。服务自身删除会保留仍被使用的计划行，删除其他较新记录不使旧作答变成新作答。已有完成记录不因本次升级被改写；时间戳仍只是宿主保存时的系统时间，不能独立证明实际学习时间。


## 单独读取练习题面

read_practice_prompt(record_id, expected_revision, practice_kind="exercise", exercise_index=null, presentation="text")为只读入口。题目必须已include，版本必须匹配；变式还需对应答案检查通过。返回record_id/source_revision、中性display_label（原题重练或变式练习N）、题目类型/下标、呈现方式、prompt和图片说明。不再返回question_label和subject，因为自由填写的标签也可能包含答案或错因；没有student_answer、参考答案、解析、练习目标、历史作答/反馈及人工意见字段。不会创建作答、改变计划或调用远端。客户端以record_id/source_revision及目标下标对应记录，不依赖显示标签作为唯一标识。

原题text只返回stem，可能缺图；original+clean_images要求选取的所有净题图都在当前修订中按sha256复核，读取并哈希验证同一份实际返回字节。图片合计最多12MiB，只接受工作区内的普通PNG；缺失、改变、损坏、未复核、特殊文件或超限均拒绝，不返回一部分后默默省略，也不回退原图。图片按clean_evidence_ids顺序，通过MCP ImageContent返回；元数据只含序号、来源证据ID及sha256，不暴露本地路径。净题模式prompt=null，避免混入转录题干中的旧作答。

变式text只返回exercise.prompt，既有验算只覆盖表达式或宿主转述的用户复核，不保证题干本身无提示或所需图形已完整。此工具提供字段隔离，不检测语义泄题，也不是对宿主已有上下文的遗忘。用户实际作答后，用source_revision、practice_kind和exercise_index调用record_practice；仍须忠实记录assistance。没有展示事件记录或图片呈现快照，不据此证明用户看到了哪张图。


## 手写公式识别的实测边界

公开MathWriting真人笔迹小诊断中，免费入口将∇读成✓，遗漏括号并混淆等号/短横；部分错误公式内容仍返回字符confidence=1。故需要将OCR原文、图片目视判断、用户确认分开。不能依据勾叉文本直接设置teacher_verdict，也不能把高置信度当作题面已正确。NarrativeText元素仍可能含formula/handwriting行内对象；读取完整objects及char_details有助于定位，不自动解决数学语义。

当数据集标签或转录与图片不一致，先保留冲突和原图，不自动用任一文本覆盖可见证据。题面结构未确定时维持uncertain，避免把OCR错误归给学生。此经验来自3条经渲染的公开数字笔迹，不代表已验收真实批改卷或付费识别。


### 查看修正前后的保存版本

`read_study_record(record_id)`继续返回当前题目与实时检查。`read_study_record(record_id, revision=2)`返回第2版原保存快照，revision_view=saved_snapshot，current_revision说明读取时观察到的当前版；明确指定当前版本号也走快照分支，不附实时派生字段。revision必须正整数，缺失版本明确失败。版本来自save/review两条现有写入路径，旧字段缺失保持缺失，不补造修改人、原因或图片历史。历史不是OCR原始缓存；比较OCR时仍读原job的read_element，比较修正后的题目时使用保存版本。需要恢复旧文字时先读取当前版、核对原图，再用正常save_study_record和当前expected_revision保存为新版本，不覆盖历史。


### 多步草稿的完整性与来源

ScratchMath初中第一条公开样本实测中，TextIn返回completed/Success但文本漏掉原图清晰的-6x=-3，并把邻近涂写混入第二行；两个元素区域发生重叠。read_document的低分提示只能定位已返回字符，无法提示根本没输出的行。因此先看整页、逐行对照，再按实际题目/步骤组织记录，不能机械“一元素一题/一步”。

数据集student_answer=-1/2在该草稿图片中并未清楚出现，来自外部标签；若引用，须在记录文本/分析明确标注来源，不能冒充目视转录或教师批改。已知答案可以验证数学结果，但不能代替学生步骤证据。忠实保留学生写错的式子，把订正解释另存；不自动纠正原作答以匹配参考解。该样本是定向诊断且事前见过标签，不是盲测准确率证明。

### 可追溯裁图文档

`crop_document(document_id, region={page:1, box:[0.42,0.64,0.84,0.92]})`只生成本地派生文档，返回pages=1、独立ID及crop来源字段。使用真实文档ID并先核对原页；样例坐标不适用于任意材料。crop.source_sha256指裁剪时父文档的规范化源文件字节，普通导入document.sha256可能指导入前原文件，两者不能机械比较。crop.pixel_box为实际渲染页中的整数[left,top,right,bottom]，不是PDF点坐标；source_render_size和output_size记录转换所需尺寸。输出不额外缩放或旋转，PDF本身仍按现有上限渲染，可能影响细字。

同父文档、源字节和区域复用；源字节改变产生新的来源身份，旧裁图及OCR不改写。文件缺失、符号链接或派生哈希不一致会拒绝；来源记录不是防篡改审计，数据库外部修改不在保证范围。父文档删除清单递归包含裁图和遮盖后代；单独删除裁图保留父文档。重启后可用list_documents找回crop来源。保存题目时仍遵守证据属于同一document_id的规则，补识别不能自动把裁图证据混进父文档题目。

### 数学自洽不能替代原图核对

ScratchMath下一固定样本中，先于OCR/标注查看原图，候选转录为`9x-25x=3-5+7.5`；OCR却输出`9x-25x=3.5+7.5`，相关字符confidence全部为1。局部图确认减号与小数点的差异。`3.5+7.5=11`验算成立，但这是对错识文字的计算，可能掩盖可见步骤算得11的问题；原图候选`3-5+7.5`算得5.5。不要把数学一致性用作自动纠正/放行OCR的充分依据，也不强迫模糊笔迹符合参考解。

同图草稿结尾可见-11/16，数据集student_answer为1/32，参考答案为-11/32。分别说明“草稿可见”“外部填写答案标注”“参考解”，不把其中一项冒充另一项。原图支持局部数值不一致，但“学生心里把减号当小数点”“抄写错误”等心理/行为解释仍是标注或推测，需要明确来源和进一步确认；单张图片不证明认知过程。图中首行250/280仍有歧义时保留歧义，不因为下一行系数为25就断言250无误。

## 图形正确例不能被整理成错题

公开几何诊断中，所选小题只要求把两圆除B外的交点标为E。原题图和学生图的OCR Image文本均为DCAB，但学生图上方交点旁有红色E；回看原图才可判断位置。Image文本相同不表示图像相同、学生未答或答案未改变。OCR题干中的E也不能充当学生标点证据。

这类正确例不应生成虚构错因或补救变式。照片还含下一小题时，先确认用户要求的题目范围，不能据未显示的后续答案判漏答。外部教师研究标注“无误”可以作为单独来源核对，但不能填成图上存在教师批改；未见卷面批改时teacher_verdict仍为unmarked。测试记录只能留在标明公开诊断的独立工作区，不能当成用户错题或用户明确纳入决定。


### 仅练习材料的离线包

export_errorbook除原HTML files外，返回practice_bundle_path；manifest和MCP摘要中的practice_bundle包含相对文件名、SHA256、文件数与范围说明。practice-only.zip只收录practice.html、存在时的original-practice.html、所选净题PNG、assets及README.txt。按明确文件名单打包，不递归复制完整导出目录；不含answers.html、errorbook.html、原卷证据图、完整manifest或数据库。包内保留原有草稿状态，净题图仍需核对残留作答。完整解压并保留相对路径后使用，不只复制HTML。

压缩包与HTML在同一暂存目录内生成，完成后一起发布；打包失败沿用导出清理流程。包位于原导出目录，原有删除库存会计入它；发送或另行复制出去的副本不在本地删除范围。SHA256用于比对字节，不证明教学内容或身份真实性。没有对外发送动作。

各HTML页首与练习包README.txt显示资料编号，等于本次export_id。练习册和答案册需同时匹配资料编号、题号；重新导出会生成不同编号，manifest继续记录各题真实revision。该编号用于人工配对，不表示版本先后、内容相同或文件未被修改，也未保证每张打印纸都有编号。

workspace_status.runtime_version在服务模块加载时读取已安装Python包元数据；发布包形如0.1.0+build.<UTC时间戳>，源码开发安装通常只有基础版本，未安装包时为null。用它核对目标installation.json，不能与插件缓存的+codex时间戳混为一谈。它不是代码签名/能力列表，也不检测运行目录被修改；仍需实际工具调用验证。

### 当前验算与历史验算

check_exercise的工具输出结构声明id、kind、expression、proposed_answer、status、scope及可选reason；equivalent只表示所选模式下通过，not_equivalent表示不符合，needs_review表示无法自动验证而非判错。实际返回在needs_review时附reason，其他状态省略。structuredContent与文本JSON内容一致，按结构字段读取，不从说明措辞猜状态。旧连接以实际工具结构和runtime_version为准。

小数字面量先从原表达式读取为Decimal，再精确转换为Fraction；不使用AST里已舍入的float作为实际数值。单个常量绝对值不超过1000000，约分后分母不超过1000000；超出范围为needs_review，不是数学上判错，也不会近似通过。诸如0.10000000000000001或1e-400不在自动验证范围，0.1+0.2与0.3仍可精确比较。变量分母仍不支持，不通过消去因子忽略原定义域。

check_exercise的kind默认为polynomial_identity；方程需显式linear_equation，例如`{"expression":"3*x+1=0","proposed_answer":"-1/3","kind":"linear_equation"}`。保存结果含kind，旧记录缺少kind时按原恒等模式读取，不猜测或迁移。linear_equation只接受化简后ax+b=0且a非零的方程，先确认唯一解再比较有理数答案；单次代入相等不证明多解方程答案完整。方程模式通过时当前status为equation_verified、check_kind为linear_equation、answer_ready=true；原模式仍是expression_verified。

当前读取、练习准入和导出会按保存的kind本地重算expression/proposed_answer；exercise_validation的check_status/check_reason是当前结果，exercise_checks仍为历史保存值。按revision读取的快照不重写旧结论；历史equivalent不作为当前放行依据。合法的旧验算可以继续通过，不一律作废。重新计算不调用外部API、不增加check记录、不修改题目/历史或用户意见；不自动重新判定旧作答。

计算量另外受每步多项式系数限制：约分后的分子和分母均最多4096个二进制位。加减乘除、平方的子表达式返回前检查，方程移项后的系数和唯一解也遵守同一预算，防止短文本的反复平方持续扩大整数。超限返回needs_review，保留原算式，不能写成not_equivalent或截断后验算；即使最终能约消成小数，中间超限也不自动计算。此上限是本实现的计算预算，不是数学或教学标准，也不是进程沙箱/硬性耗时保证。
