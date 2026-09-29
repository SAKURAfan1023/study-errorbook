# TextIn 实际响应夹具

`textin-free-synthetic.json` 来自2026-09-25的一次真实免凭据同步调用。输入完全由 `scripts/probe_free_ocr.py` 绘制，没有学生材料、个人信息或手写字迹。原图 SHA-256：`ff2ad37744646d429f0f9d0c45e8f9819d14f26517c8c72299bbda7264b9c21d`。

已删除顶层 job_id/file_id 和页面下载地址；其余解析字段保持原响应。夹具用于离线回归，不能替代服务可用性或识别效果实测。其中红色叉号返回 text=`x`，须结合原图判断含义。
