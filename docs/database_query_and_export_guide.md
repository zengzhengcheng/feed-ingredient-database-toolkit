# SQLite 数据库查询、规则与导出指南

## 1. 这个数据库文件可以直接使用

ScienceDB 发布数据库是标准 SQLite 3 文件，不需要启动数据库服务器，也不要求安装本项目的软件。用户可直接用 SQLite、DB Browser for SQLite、Python、R、Julia 或其他支持 SQLite 的工具打开。

建议把发布文件视为只读版本。需要编辑词典或继续导入时，先复制为个人工作库，再使用 PyQt6 界面或导入代码操作。这样可以保持论文引用的数据版本不变。

## 2. 最简单的查看方法

在仓库根目录双击 `run_gui.bat`，或运行 `conda run -n pyqt312 feeddb-gui`。打开“查询与导出”后有两种方式：

1. **关键词查询**：输入原料、指标、标题、DOI、动物或 AID/SID/ATTD/STTD 等关键词。多个关键词之间是“同时满足”。名称词典中的别名会参与查询。
2. **自定义规则（只读 SQL）**：输入或载入一份 `.sql` 规则，点击“运行只读规则”。界面只允许一条以 `SELECT` 或 `WITH` 开头的语句，并通过只读连接执行。

关键词页可导出当前显示结果；规则页可把完整规则结果导出为 CSV、JSON 或 Excel。界面的 1,000/10,000 行提示是显示上限，不是数据库总记录数。

## 3. 数据表和便捷视图

两条科学数据链必须分别理解：

`studies -> ingredient_samples -> component_observations`

`studies -> trials -> animal_groups -> ingredient_evaluations -> nutrition_observations`

常用对象如下：

| 对象 | 用途 |
|---|---|
| `studies` | 论文标题、DOI、期刊、年份等元数据 |
| `ingredient_samples` | 每项研究中的原料样品及原名/统一名 |
| `component_observations` | 原料本身的组成数据 |
| `trials`、`animal_groups` | 试验编号和动物信息 |
| `ingredient_evaluations` | 原料、试验和动物组之间的连接 |
| `nutrition_observations` | 能量、AID、SID、ATTD、STTD 等动物相关营养价值 |
| `dictionaries` | 原始写法到统一名称的别名对照 |
| `source_provenance` | 记录对应的公开工作簿、sheet 和单元格位置 |
| `ingredients` | 原料样品的只读便捷视图 |
| `observations` | 组成与营养价值的统一只读便捷视图 |

`candidate` 表示名称别名仍待确认，不表示对应的 Excel 数值无效。数据是否可用应查看 `validation_status`。

## 4. 编写查询规则

规则文件就是一条可保存、可复用的 SQL 查询。可修改的只读查询规则位于本仓库的 `examples/query_rules`；ScienceDB 数据包的 `database/` 只放 SQLite 数据文件。复制示例后修改 `WHERE` 条件即可。

例如，查询统一名称包含 Corn 的原料组成：

```sql
SELECT s.title, s.doi, i.canonical_name AS ingredient,
       o.canonical_metric AS metric, o.value_num, o.value_text,
       o.canonical_unit AS unit, o.canonical_basis AS basis
FROM component_observations o
JOIN ingredient_samples i USING (ingredient_sample_id)
JOIN studies s USING (study_id)
WHERE i.canonical_name LIKE '%Corn%'
ORDER BY s.title, o.canonical_metric;
```

查询规则应遵循三条原则：

- 组成数据从 `component_observations` 查询；营养价值从 `nutrition_observations` 查询。
- 比较数值前同时检查单位、报告基础、测量语境、动物和试验条件。
- 自动化脚本优先使用 `?` 参数占位符，不要把外部文本直接拼入 SQL。

## 5. 命令行查询与导出

在本仓库根目录运行：

```powershell
conda run -n pyqt312 python -m feeddb.cli query-sql path\\to\\feed_ingredient_release.sqlite --sql-file examples\\query_rules\\01_原料成分查询.sql --limit 1000

conda run -n pyqt312 python -m feeddb.cli export-query path\\to\\feed_ingredient_release.sqlite result.xlsx --sql-file examples\\query_rules\\02_动物营养价值查询.sql
```

带参数的规则可以写成 `WHERE i.canonical_name LIKE ?`，然后传入 JSON 数组：

```powershell
conda run -n pyqt312 python -m feeddb.cli query-sql path\\to\\feed_ingredient_release.sqlite --sql "SELECT * FROM ingredients WHERE canonical_name LIKE ?" --parameters "[\\"%Corn%\\"]"
```

`export-query` 根据输出扩展名生成 `.csv`、`.json` 或 `.xlsx`。CSV 使用 UTF-8 BOM，便于中文 Windows Excel 直接打开。

## 6. Python 直接连接

Python 标准库即可读取，不需要安装本项目：

```python
import sqlite3

database = "feed_ingredient_release.sqlite"
uri = f"file:{database}?mode=ro"
with sqlite3.connect(uri, uri=True) as conn:
    rows = conn.execute(
        "SELECT canonical_name, count(*) FROM ingredients GROUP BY canonical_name"
    ).fetchall()
print(rows[:10])
```

代码仓库中的 `examples/python_direct_access.py` 演示了参数化查询和 CSV 导出：

```powershell
python examples\\python_direct_access.py path\\to\\feed_ingredient_release.sqlite corn_rows.csv --ingredient Corn
```

使用 pandas 时：

```python
import sqlite3
import pandas as pd

with sqlite3.connect("feed_ingredient_release.sqlite") as conn:
    frame = pd.read_sql_query("SELECT * FROM observations", conn)
frame.to_csv("observations.csv", index=False, encoding="utf-8-sig")
```

## 7. R 直接连接

```r
library(DBI)
library(RSQLite)

con <- dbConnect(SQLite(), "feed_ingredient_release.sqlite")
data <- dbGetQuery(con, "SELECT * FROM observations LIMIT 1000")
dbDisconnect(con)
write.csv(data, "observations.csv", row.names = FALSE, fileEncoding = "UTF-8")
```

## 8. 继续导入新数据

数据库的持续扩充仍采用文件驱动流程：完整 Excel 或 JSON 同时提供论文元数据、原料组成、动物信息和营养价值。文件先进入可视化核对页，只有用户明确确认后才写入个人工作库。标准模板见 `templates/feed_ingredient_contribution_template.xlsx`。

新名称可作为 `candidate` 保留并进入词典管理页，待人工确认后再批准为 `approved`。软件不会因为新词尚未进入别名词典而删除数值。

## 9. 引用与版本

使用公开数据时，应引用ScienceDB数据集记录（https://doi.org/10.57760/sciencedb.46393），并注明所用数据库版本；不要用本地文件路径代替正式数据引用。
