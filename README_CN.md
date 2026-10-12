# 饲料原料数据库工具包

本软件用于检查、核对、导入、查询、导出和持续扩充饲料原料组成及动物营养价值数据。

当前公开软件版本：**v1.0.1**。

### v1.0.1 修订内容

- 缺失能量单位的通用可编辑默认值为 `MJ/kg`；来源明确的 `kJ/kg`、`kcal/kg` 等单位与数值保持不变。
- AID/ATTD 的 GE 消化率优先使用已批准的 `%` 规则，不会被能量通用默认覆盖。
- 合成 AI/Agent JSON 示例中的 SID Lysine 改为数值 `94`、单位 `%`，明确表示94个百分点，而不是 `0.94`。
- 既有NE/ME模型文件和结果保持不变，本次未重训。

[English README](README.md) · [完整使用指南](docs/USER_GUIDE.md) · [Excel 填写说明](docs/excel_entry_guide.md) · [词典说明](docs/dictionary_guide.md)

## 软件解决什么问题

新数据经常出现四类情况：新组分没进词典、单位写法不统一、原料名称相似但科学含义不同、数值疑似超范围。软件会把完整 Excel 或 JSON 先加载成可核对草稿，保留原名称、原单位、原数值和来源位置，再把需要人工判断的事项集中显示。

不认识的名称不会导致观察值被删除。名称可以映射到已有标准项、建立为新的独立标准项，或者暂缓判断。

## 数据包和代码包分别在哪里

本 GitHub 仓库只放软件、测试、空白模板、词典种子、只读 SQL 规则、文档和复用示例。正式数据、110 个公开工作簿、交换表和只读发布 SQLite 单独存放于 ScienceDB。

- GitHub：`https://github.com/zengzhengcheng/feed-ingredient-database-toolkit`
- ScienceDB：[可溯源且可扩展的历史饲料原料组成与营养价值数据集](https://doi.org/10.57760/sciencedb.46393)
- 关联文章或项目：**Not applicable**

上述ScienceDB DOI当前指向已公开的V3数据集。2026年10月审稿修订数据候选仍在本地，本README不声称该修订数据已经公开。

本仓库不包含来源论文、正式数据副本、工作数据库、日粮配方或用户数据。

## Conda 安装

推荐在 Windows 上建立名为 `pyqt312` 的环境：

```powershell
git clone https://github.com/zengzhengcheng/feed-ingredient-database-toolkit.git
cd feed-ingredient-database-toolkit
conda create -n pyqt312 python=3.12 -y
conda activate pyqt312
python -m pip install -e .
```

如果需要重新运行净能或代谢能示例，再安装建模依赖：

```powershell
python -m pip install -e ".[modeling]"
```

## 五分钟开始使用

1. 安装完成后双击 `run_gui.bat`。
2. 打开“导入与核对”，选择一份完整 Excel 或 JSON。
3. 检查论文元信息、原料、组分、动物信息、营养价值、单位、basis 和警告。
4. 点击完整检查。此时文件仍只是核对草稿，没有写入 SQLite。
5. 人工确认后，明确点击写入工作数据库。
6. 到“查询与导出”模糊搜索并导出 CSV、JSON 或 XLSX。

默认工作库为 `database/contribution_workspace.sqlite`。它属于用户自己的可编辑文件，Git 不会上传它。

## 界面不是逐格录入器

系统遵循“数据文件驱动”原则：一份完整 Excel 或 JSON 同时提供论文元信息、原料组成、动物信息和营养价值。界面用于集中核对、修正和确认整批数据，不要求用户在软件里逐格重新抄录。

载入文件只形成草稿。必须由用户运行检查并明确确认，整批数据才会在一个事务中写入工作库。

## Excel 与 JSON 的基本规则

空白模板：[templates/feed_ingredient_contribution_template.xlsx](templates/feed_ingredient_contribution_template.xlsx)

模板包含：

- `Instructions_说明`：中英文填写说明，解析器会忽略该 sheet；
- `Article Metadata`：论文级元信息；
- `Ingredient Data`：试验、原料、动物组、组成和营养价值。

填写时注意：

- 题名必填；DOI 可以为空，绝不能编造。
- 数值可以为空；不能用 `0` 代表缺失。
- 原料组成属于原料；能量和消化率还必须对应动物组和试验语境。
- 日粮/配方组成不在本软件的贡献范围内。

用户自己的 AI 或脚本可以按照 [integrations/contribution.schema.json](integrations/contribution.schema.json) 生成 JSON。推荐仍然先导入界面核对，不要跳过人工确认。

JSON数值按指标顺序对应。单位为`%`时填写百分点：`94`表示`94%`，不是`0.94`。示例中的SID Lysine只是合成教学值，不能据此改写真实研究数据。

## 不认识的组分、单位和原料名怎么办

### 新组分或指标

- 数据保留，原始名称保留，并进入候选核对。
- 如果只是已有组分的另一写法，映射到已有标准项。
- 如果科学上确实是独立指标，建立新的标准项，并设置分组和常用单位。
- 证据不足时暂缓，不猜测、不强行合并。

### 新单位或空单位

- 显式单位优先，软件不会用默认值覆盖来源单位。
- 指标名称在英文逗号 `,` 或中文逗号 `，` 后带已知单位时，软件会自动提取。
- 新 energy 指标的可编辑默认单位为 `MJ/kg`；AID/ATTD语境中的GE消化率优先使用`%`；原文明示的 `kJ/kg`、`kcal/kg` 仍按原单位保留，系统不静默换算数值。
- 普通 composition 指标的可编辑默认单位为 `%`。
- 历史 AME、TMEn 等已批准指标专属规则优先于通用默认。
- 软件保留来源单位和最终采用单位，不静默换算原始数值。

### 名称相似的原料

模糊匹配只提供提醒，不能自动决定是否同一种原料。大小写、空格、连字符或明确拼写差异可以进入别名词典；加工方式、品种、来源或科学含义不同，则应建立独立原料。

### 数字疑似超标

软件发出警告，但不会删除、截断或自动改单位。应核对原文、单位、basis、小数点以及干物质/原样基础。确认原文确实如此后可以保留，并记录核对依据。

## 名称词典和指标目录不是一回事

名称别名词典负责：

```text
Excel/JSON 原写法 -> 已批准别名 -> 统一标准名称
```

`metric_catalog` 负责界面中的常用指标、分组、默认单位和默认 basis。新增一个别名不等于新增指标分组；修改指标分组也不会自动合并名称。

未知名称只会进入候选队列，不会因为“不认识”而丢失观察值。更多说明见 [docs/dictionary_guide.md](docs/dictionary_guide.md)。

## 查询、只读 SQL 与导出

查询页支持标题、DOI、原料、指标和动物信息的模糊搜索。名称词典中的已批准别名会参与查询。

自定义 SQL 只允许一条 `SELECT` 或 `WITH` 语句，通过只读连接运行；修改数据库的语句会被拒绝。查询结果可以导出为 CSV、JSON 或 XLSX。示例规则位于 [examples/query_rules](examples/query_rules)。

## 中英文切换

状态栏的 `中文 / English` 可以在运行中直接切换，不需要重启。切换只改变界面显示文字，不改变数据库语义、指标名称、单位、来源字段或数值。

## 只读打开 ScienceDB 发布库

正式发布 SQLite 应按只读方式使用：

```powershell
.\run_gui.bat --database path\to\feed_ingredient_release.sqlite --read-only
```

也可以使用 DB Browser for SQLite、Python、R 或 Julia 直接连接。需要新增或修改数据时，请新建个人工作库，不要修改冻结的发布文件。

## 用户自己的 AI/Agent 接口

本软件不内置 AI 服务，也不需要 API key。用户自己的 AI、Agent、本地模型或脚本可以生成 UTF-8 JSON，但必须遵守：题名必填、DOI 可空、缺失值用 `null`、原名和单位保留、未知词不自动批准。

```powershell
feeddb validate-json contribution.json database\contribution_workspace.sqlite
feeddb import-json contribution.json database\contribution_workspace.sqlite
```

直接导入只适合已人工核对的文件。详细约束见 [integrations/AI_AGENT_GUIDE.md](integrations/AI_AGENT_GUIDE.md)。

## 常见错误与处理

- **双击后提示找不到环境**：确认已安装 Conda，并且环境名为 `pyqt312`；重新运行安装命令。
- **文件只加载但数据库没变化**：这是安全设计；必须完成检查并明确确认写入。
- **题名为空**：补充真实题名；DOI 可空。
- **用 0 表示缺失**：改为空值或 `null`。
- **出现 candidate**：它表示名称待核对，不等于数据错误，也不会自动进入 `held`。
- **单位建议不正确**：在草稿中切换为来源证据支持的单位；建议不会改动原始数值。
- **数据库处于只读模式**：发布库不能导入或改词典；切换到个人工作库。
- **重复来源被阻断**：核对题名、DOI、文件来源和重复策略，不要绕过提示重复导入。

## 净能与代谢能复用示例

- [猪净能示例](examples/net_energy_prediction/README.md)
- [全原料猪代谢能示例](examples/metabolizable_energy_all_ingredients/README.md)

两者都采用研究分组验证并保留差折结果，只用于演示数据库如何被可追溯地复用。它们不是通用最佳模型、饲养推荐方程、营养需要量，也不是独立外部验证。

## 测试

```powershell
conda run -n pyqt312 python -m pytest -q
.\run_gui.bat --smoke-test
```

最新测试环境与结果记录在 [RELEASE_VALIDATION.json](RELEASE_VALIDATION.json)。

## 文档入口

- [完整使用指南](docs/USER_GUIDE.md)
- [Excel 填写说明](docs/excel_entry_guide.md)
- [词典通俗说明](docs/dictionary_guide.md)
- [词典内容清单](docs/dictionary_inventory.md)
- [数据模型](docs/data_model.md)
- [系统功能图](docs/SYSTEM_FUNCTION_MAP_20260807.md)
- [AI/Agent JSON 说明](integrations/AI_AGENT_GUIDE.md)
- [查询与导出指南](docs/database_query_and_export_guide.md)
- [发布与重建指南](docs/release_guide.md)
- [SQL 查询规则](examples/query_rules)

## 许可证

本软件依据 [MIT License](LICENSE) 开源发布。Copyright (c) 2026 zengzhengcheng。

Zenodo软件版本族概念DOI：[10.5281/zenodo.22252121](https://doi.org/10.5281/zenodo.22252121)。
