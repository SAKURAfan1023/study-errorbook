## 输出

## 逐题补全再导出

用户要学生材料时，先明确原题/变式范围：practice_scope=originals只导出原题文字重练及已复核净题图；variants只导出变式；all导出所选题已有的学生材料。用户明确只留档用practice_scope=archive，仅生成私有错题本/历史反馈，不生成空学生ZIP；student_materials.requested=false且complete=null。每条选中记录必须覆盖，不能用一题的多个变式抵另一题缺失，也不能拿变式冒充原题。

1. 逐题read_study_record，查看exercises及其kind、exercise_validation、clean_copy_validation。没有可用学生材料的题先补齐，不先生成少题包。跨页题保持同一记录及全部证据。
2. 无练习/净题图但原题文字已由完整原图核实（例如代数Q4）时，补一条kind=original_text：prompt必须与当前record.stem完全一致，answer和explanation独立保存，不把原作答或诊断写进题面。按真实题意计算并用check_exercise做适用检查，答案字符串与proposed_answer一致。用户明确要新变式则kind=variant。涉及未补齐的图形、表格或条件时先补材料/询问范围，不能臆造或自动调用付费擦除。
3. 用当前完整record及revision保存，保留所有原证据、作答、分析及历史。按工具返回完成所需复核；只转述用户已有明确意见，不代填新的人工作答或答案确认。原题核心改动时旧练习失效，须重新核实。
4. 正式export_errorbook缺题会返回student_material_missing且不创建少题成品。补全后按原范围重试；不改为include_drafts来掩盖缺项。include_drafts仅供用户明确要求讨论草稿，缺题时contains_drafts=true且student_materials.complete=false，不是完成交付。
5. 交付前核对student_materials：selected_record_count=covered_record_count、missing_record_ids为空，逐record的原题/变式及真实题目数与实际HTML相符。核对学生ZIP清单和题面；答案册独立且对应同一资料编号。ZIP资源文件数不是题目数。


擦除调用返回的provider_receipt供反馈定位：request_ids保留响应头标识，x_request_id来自响应体，两者不相互冒充；version只是接口返回版本。response_saved=true时可按response_path查看本地响应文件，response_sha256对应实际归档字节；credential_echo_redacted=true表示已遮盖凭据回显，不能称为完全未修改的原始响应。文件可能含图片base64，不要整段贴入对话。旧缓存或失败调用可能缺少标识/响应，不猜填，也不为补齐记录重复付费提交。receipt只证明调用与归档状态，不证明擦除质量，仍须核对题干损伤和作答残留。

正式导出前用 `read_study_record` 查看exercise_validation。每道练习需要适用表达式验算通过，或本版本用户明确复核。表达式通过仍不证明自然语言题意匹配；没有用户意见时不得代填人工确认。未就绪内容可include_drafts=true导出讨论，contains_drafts标识是否含待复核内容。

选择了净题图时，还需检查clean_copy_validation。正式导出要求所选图均已复核；草稿会显示题干损伤/作答残留风险。导出会额外生成 `original-practice.html` 原题重练册，按所选图顺序显示并留答题区，不带入原作答、分析或转录题干。未选择净题图的记录不进入此册，原有三册仍照常生成。不要把未复核净题草稿说成已可放心使用的空白卷。

`export_errorbook` 输出可打印 HTML、原卷证据、独立练习册和答案册；公式使用 `\(...\)` 行内、`\[...\]` 或 `$$...$$` 独立公式分隔符，单个美元符号按普通文本保留。导出自带离线KaTeX与字体。完整目录含答案和原作答，供留档；给学生练习材料时使用返回的practice_bundle_path（practice-only.zip），完整解压后打开练习页。此包只含练习页、所选净题图和离线资源，不含答案册/错题本/原卷证据图/完整manifest；仍要检查题干和净题图中的提示残留。不要把“没有答案册”说成已证明无提示，也不要自动对外发送。遇到公式错误提示，核对原文后再打印；排版成功不是答案已验算。 没有生成 PDF 时不要称为 PDF。默认导出用户纳入的记录，草稿要明确 `include_drafts=true` 并保留其状态。学生练习册不应包含答案。错题本会分别展示录入的卷面批改、Agent判断、笔迹归属和用户复核意见，并将题目、批注、分析引用的全部证据编号为页内链接；发现链接目标不符时返回原图核对。练习目的显示在答案册；学生变式/净题重练册使用中性“第N题”编号，原标签保留在错题本和答案册，避免标题或图片说明泄露错因。题干/净题图自身仍须核对提示残留。manifest.json的display_number对应本次导出顺序，连同记录ID、版本及证据区域用于追溯；不是跨导出的稳定编号。每份HTML和练习包README显示同一资料编号（本次export_id）；核对答案必须匹配资料编号和题号，不要只按第N题配对。重新导出会产生新编号，即使题目未修改；它不是题目revision或防篡改证明。MCP返回摘要及manifest_path。

导出错题本的“材料来源”列出保存的裁图/遮盖关系，证据标题区分材料页和裁图页。manifest中的document_provenance保留最多16层来源链，evidence的document_id说明页码/坐标所属材料，file_sha256对应本次导出图片字节。complete_saved_chain仅表示保存关系能追溯到导入记录，不证明父文件当前完整、笔迹归属或教学判断；父页和完整原件不会因此被复制进导出。缺失、循环或超深来源明确标为不完整，不把裁图第1页冒称原卷第1页。不要将哈希相同说成来源已被独立认证。

向用户说明：本次材料与题目范围、确认/待定项、每个主要判断的证据入口、错因与练习、导出位置。区分 OCR 输出、Agent 推断、用户复核与代数验算，不报告未经测试的准确率或教学效果。

原卷证据导出只读取对应材料目录内的普通PNG，单图最多20MiB，不跟随文件或目录链接。evidence_unavailable表示缺失、损坏或路径/类型异常，evidence_image_too_large表示超出读取上限；先核对本地材料，不能用草稿模式绕过。读取检查只证明该次路径、类型、格式和大小，不证明图像仍与历史原件逐像素一致。
