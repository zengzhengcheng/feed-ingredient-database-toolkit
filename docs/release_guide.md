# 公开版本构建与就绪检查

## 1. 两条命令

`build-release` 从历史目录重新构建公开候选版，不复制正在编辑的工作数据库。工作数据库只提供已经整理的名称词典和指标目录；历史数据仍从源 Excel 重新解析。

```powershell
conda run -n pyqt312 feeddb build-release `
  path\to\source_workbooks `
  database\contribution_workspace.sqlite `
  release_working\v1.0.1 `
  --duplicate-resolution config\duplicate_resolution.csv `
  --rights-review config\rights_review.csv `
  --release-version v1.0.1 `
  --data-license "CC BY 4.0"
```

`check-release` 可以在人工修改权利审查或许可后重新运行：

```powershell
conda run -n pyqt312 feeddb check-release release_working\v1.0.1
```

构建命令拒绝写入非空输出目录，避免覆盖已有候选版。需要重新构建时应使用新的可读版本目录。

## 2. 权利审查文件

权利审查 CSV 使用以下字段：

| 字段 | 含义 |
|---|---|
| `public_workbook_id` | 构建后稳定的公开编号，如 `study_0001` |
| `doi` | DOI；没有 DOI 时可以为空 |
| `rights_status` | `approved`、`cleared` 或 `pending` |
| `rights_note` | 作者确认依据或待处理说明 |

构建器可按公开编号或 DOI 读取决定。未提供权利文件时，全部工作簿标记为 `pending`；数据仍可生成供内部复核，但不能通过“可上传”检查。

## 3. 许可

未提供 `--data-license` 时使用 `PENDING`，系统只生成许可确认提醒，不生成正式数据许可文件。作者确认后再显式传入许可名称；软件不会替作者选择数据许可。

## 4. 输出结构

```text
release_directory/
├── README.md
├── data/
│   ├── editable_workbooks/       # 顺序编号、只含两个英文数据sheet
│   ├── exchange_tables/          # SQLite 核心表和便利视图的 CSV
│   └── study_source_index.csv    # DOI/书目信息，不含论文文件和本地路径
├── database/
│   └── feed_ingredient_release.sqlite
├── dictionaries/
│   ├── name_alias_dictionary.csv
│   ├── metric_catalog.csv
│   └── unit_basis_mapping.csv
├── documentation/
│   ├── data_dictionary.csv
│   ├── release_contents.csv
│   └── 录入、词典、数据模型与 AI 接口说明
├── templates/
│   ├── feed_ingredient_contribution_template.xlsx
│   ├── contribution.schema.json
│   └── ai_contribution_example.json
└── validation/
    ├── release_readiness.json
    ├── release_statistics.json
    ├── working_database_comparison.json
    ├── duplicate_decisions.csv
    ├── source_audit_summary.json
    ├── clean_workbook_report.csv
    ├── scope_and_rights_review.csv
    └── import_issues.csv
```

## 5. 发布闸门

必须通过：

- 必需文件齐全；
- `database/`只含SQLite；可修改SQL查询规则位于GitHub代码包；
- 公开包中没有论文 PDF、截图或其他图像副本；
- 文本文件中没有用户本机绝对路径；
- 清洁 Excel 数量与来源索引一致；
- 每个可编辑Excel严格只有 `Article Metadata` 和 `Ingredient Data`；
- 题名存在，DOI 可以为空；
- 内部录入人员、存疑、口令和路径字段已剔除；
- SQLite 完整性、外键、重复 DOI、数据状态和来源定位检查通过；
- CSV 逐表记录数与 SQLite 一致；
- 历史重建与工作快照的核心记录数一致；
- 导入错误为零；
- 权利审查和数据许可已经由作者确认。

候选别名可以产生警告但不自动判定数据无效。需要科学判断的名称继续保留原始写法和 `candidate` 状态。

## 6. 数据仓库边界

正式数据、公开工作簿、交换表和发布 SQLite 由ScienceDB数据记录（https://doi.org/10.57760/sciencedb.46393）管理，不进入本GitHub软件仓库。发布构建的实际记录数、权利状态和许可状态必须从当次生成的验证报告读取，不得把历史工作目录中的数字写死为当前事实。

项目不生成文件哈希清单。发布版使用语义版本、日期、公开工作簿编号、逐表记录数和 ScienceDB DOI/CSTR 管理。
