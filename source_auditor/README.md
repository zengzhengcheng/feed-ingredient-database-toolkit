# 历史饲料原料工作簿独立审查器

这是一个独立、只读、可复用的建库前审查器。它直接检查原始文件树和 Excel，不导入 SQLite，也不依赖 `feeddb`。因此审查结论不会被词典映射、入库状态或数据库模式掩盖。

## 审查边界

- 输入：包含来源子目录和历史 `.xlsx`/`.xlsm` 的任意根目录。
- 输出：CSV、JSON 和 Markdown 问题台账。
- 必填身份字段：论文题目；DOI 可为空。
- 论文 PDF：只清点文件角色，不读取正文，也不进入公开数据包。
- 源 Excel：永不修改。
- 数据库：永不创建、删除或更新。
- 文件哈希：不计算、不比较、不输出。

问题按责任边界分为：

- `SOURCE_EXCEL`：应回到源工作簿核对或修正的问题。
- `IMPORTER_RULE`：源信息存在，但导入器必须正确识别、拆分或保留。
- `DATABASE_QC`：入库后应执行的通用约束（当前版本预留）。
- `MANUAL_REVIEW`：必须结合论文或项目语境人工判断。

严重性分为 `BLOCKER`、`ERROR`、`WARNING`、`INFO`。只要存在 `BLOCKER` 或 `ERROR`，`release_gate.json` 的 `can_build_database` 就是 `false`。

## 直接运行

运行 `run_audit.bat` 时必须提供待审查来源目录；第二个参数为可选输出目录，省略时使用当前目录下的 `audit_output`：

```bat
run_audit.bat "D:\source_workbooks" "D:\audit_report"
```

命令行方式：

```text
conda run -n pyqt312 python -B auditor.py SOURCE_DIR OUTPUT_DIR
```

退出码：

- `0`：审查通过，可以进入建库流程。
- `2`：审查完成，但门禁未通过。
- `1`：审查器本身运行失败。

## 输出文件

- `AUDIT_REPORT.md`：面向人工阅读的结论和阻断项。
- `summary.json`：审查统计。
- `release_gate.json`：能否建库的机器可读门禁。
- `file_inventory.csv`：所有文件及角色。
- `workbook_inventory.csv`：整理后工作簿身份和结构。
- `metric_profile.csv`：按语境、原始指标、显式单位和基础汇总的数值概况，用于完善单位/指标规则。
- `all_issues.csv`：完整问题台账。
- `source_excel_issues.csv`：源 Excel 问题。
- `importer_rule_issues.csv`：导入器应处理的规则。
- `database_qc_issues.csv`：数据库质控规则问题。
- `manual_review_issues.csv`：需人工核对的问题。

## 当前检查

- 同一来源目录多份整理后工作簿、临时锁文件、PDF 副本等文件树风险。
- `论文参数` 和 `成分及营养价值数据` 的存在性。
- 题目必填、DOI 规范化、跨工作簿重复题目/DOI。
- 原料列空标题、重复原料名、重复原料编号。
- Excel 错误值、无缓存公式、非有限数。
- 单位和干湿物质基础是否显式、单位是否嵌入指标标签。
- 同一指标行的百倍量级离群值；普通离群值只要求人工核对，能量行出现明显混合量级才阻断。
- MJ/kg 能量异常、明确为百分比的消化率超过 100、百分比成分超范围。

原料编号允许在不同试验或处理列中重复。审查器只提示导入器必须采用“来源工作簿 + 源列 + 试验编号 + 原料编号”等可追溯组合身份，不会把原料编号重复本身判为源表错误。AID/SID 等结构标签也不等同于单位；数值超过 100 只有在单位或指标名明确表示百分比/消化率时才按错误处理。

所有数值异常仅报告证据和位置，审查器不会自动缩放、纠正或删除数据。
