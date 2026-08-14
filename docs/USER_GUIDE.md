# 饲料原料数据库软件使用指南 / Feed Ingredient Database User Guide

## 1. 数据真源与安全边界

- Excel 是公开、可修改的数据真源；SQLite 是从 Excel 和受控词典构建出的工作库或发布库。
- 发布库默认只读。新增、修改和删除都在工作库执行。
- 删除数据库不会删除 Excel；只要 Excel 仍在，重新建库会恢复其中的数据。
- 软件保留 `raw_name`、`raw_metric`、`raw_unit` 和来源单元格；统一名称与单位另存，不静默改数值。

Excel is the editable source of truth. SQLite databases are derived working or release products. Raw names, units, values, and source cells remain traceable.

## 2. 新数据进入系统

1. 在“导入与核对”选择单个或多个 Excel/JSON 文件。
2. 软件先生成核对草稿，不立即写数据库。
3. 核对论文、原料、动物、组分、语境、单位、basis 和数值警告。
4. 通过验证后明确点击保存，整批写入工作库。
5. 批量历史数据可用“从 Excel 目录重建数据库”；必须输出到新数据库文件。

New files are loaded into a review draft first. The user validates the complete batch and explicitly commits it to the editable working database.

## 3. 四类必须人工核对的情况

### 不认识的组分/指标

- 数据仍导入，原始名称保留，状态记为 `candidate`；不会因词典未收录而删除或进入 `held`。
- 到“词典与指标 → 待核对新词”：
  - 确认只是已有组分的另一写法：选择“映射到已有标准项”。
  - 确认是新的独立组分：选择“建立新标准项”，同时指定指标分组和常用单位。
  - 证据不足：选择“暂缓判断”，不要猜。

### 不认识的单位

- 优先级固定为：显式单位列 → 指标名称末尾单位 → 指标专属规则 → 能量数值量级 → 指标目录默认 → 语境默认。
- 英文逗号 `,` 或中文逗号 `，` 后若为已知单位或明显的单位式写法，软件自动拆成“组分名称＋源单位”；普通说明文字不拆。
- 普通组分空单位默认 `%`；普通能量空单位默认 `kJ/kg`。历史 AME/TMEn 等专属 `MJ/kg` 规则优先。
- AID/SID/ATTD/STTD 空单位默认 `%`，basis 记作 `not applicable`。
- 导入草稿会直接显示自动分配的单位；点击“核对/切换单位”可逐项修改。源表单位和最终采用单位分开保存。
- 不认识的单位进入候选词核对；只新增单位同义关系，不换算原始数值。

### 名称很接近的原料

- 所有查重、匹配和相似项检查先生成 Unicode 小写比较键（NFKC + `casefold`）。这只用于比较，不改变显示或 raw 字段。
- 名称接近只是提醒，不能自动合并。根据原文判断加工方式、来源、品种或副产物是否构成独立原料。
- 同一原料的大小写、连字符、空格或明确拼写变体可写入别名词典；科学含义不同则建立新标准原料。

### 数字疑似超标

- 软件给出警告，不删除、不截断、不自动换单位。
- 先核对原文、单位、basis、小数点和干物质/原样基础。
- 百分数超出 0–100、浓度/能量为负、能量数量级异常都会提示；表观消化率的负值或个别超过 100 的值可保留，但应记录核对依据。

## 4. 组分名称与测量语境

- 组分名称只描述“测了什么”，例如 `IDF`、`AEE`、`Phosphorus`。
- `AID`、`SID`、`ATTD`、`STTD` 描述“怎样测/在哪个消化率范围”，存入 `measurement_context`，不拼进组分名称。
- 同一组分可同时用于组成含量和多个消化率语境；词典只核对一次。

Component identity and measurement context are separate. The same canonical component can be used in composition, AID, SID, ATTD, or STTD records.

## 5. 词典与指标分类的区别

- 名称词典：处理“同一个东西的不同写法”，即 `raw term → canonical term`。
- 指标分类：管理界面选择器中的常用组分、分组、默认单位和默认 basis。
- 新指标通常同时需要一个标准词典项和一个指标分类项；新别名只需要增加词典映射。

## 6. 查询、导出和重建验收

- 关键词查询同时检索标题、DOI、原料、指标和动物；自定义 SQL 仅允许一条只读 `SELECT`/`WITH`。
- 导出前确认使用的是 `canonical_*` 字段，并保留需要的 `raw_*` 与来源字段。
- 重建后检查：研究数、观察数、`held/rejected`、候选词、未解析单位、外键和 SQLite integrity。
- 新旧数据库必须使用不同文件名；验收完成前不得覆盖发布库。
