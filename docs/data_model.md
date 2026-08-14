# 数据模型与状态说明

## 两条数据链

原料成分和动物营养价值必须分开存储：

```text
Study -> IngredientSample -> ComponentObservation

Study -> Trial -> AnimalGroup -> IngredientEvaluation -> NutritionObservation
```

第一条链描述原料本身。第二条链说明“哪个试验中的哪组动物，对哪个原料得到什么营养价值”。

## 主要数据表

- `studies`：论文元数据和 DOI；
- `trials`：论文内的试验；
- `ingredient_samples`：被测原料样品；
- `animal_groups`：试验动物组；
- `ingredient_evaluations`：原料、试验与动物组的连接；
- `component_observations`：原料成分；
- `nutrition_observations`：能量和消化率等动物相关数值；
- `dictionaries`：名称别名；
- `metric_catalog`：可编辑的常用指标分组、排序、默认单位和默认基础；它不是别名词典；
- `source_provenance`：每条逻辑记录对应的原工作簿、sheet、行和列；
- `validation_issues`：需要解释或修正的数据问题。

`ingredients` 和 `observations` 是只读兼容视图，便于统一查询，不是写入表。

## 去重但不丢溯源

同一原料中完全相同的成分记录可以合并成一条 `component_observations`。如果相同值在来源 Excel 的多个单元格中出现，每个来源仍分别写入 `source_provenance`。因此逻辑去重不会静默删除证据。

营养价值默认按原料评价分别保存，因为动物组、试验或测量语境不同都可能改变科学含义。

## `accepted`、`held` 与名称候选

- `accepted`：结构和关系足以解释；
- `held`：数值保留，但缺少决定含义的信息，例如消化率没有 AID、SID、ATTD、STTD 或其他明确类型；
- `rejected`：经确认不是有效数据记录。

名称未在词典中只会产生 `candidate` 映射状态，不会自动把数值设为 `held`。

## 指标目录与别名词典

`metric_catalog` 决定录入界面“给用户选什么”：必需/非必需氨基酸、微量元素、维生素、抗营养因子、霉菌毒素等分组及其建议单位。`dictionaries` 决定“原文不同写法如何统一”。二者分开，避免把使用频次、界面便利性与别名判断混为一谈。

GUI、标准 Excel、CLI JSON 和用户自己的 AI 均进入同一个贡献模型和校验/写入流程，不维护独立的数据规则。

## 发布边界

公开数据库只来自历史工作簿映射后的 `Article Metadata` 和 `Ingredient Data`。历史原名 `论文参数`、`成分及营养价值数据` 保存在显式sheet名称映射表中；公开SQLite和溯源定位统一使用英文sheet名。实验日粮sheet、23篇新文献语料、论文PDF、截图和OCR中间文件均不进入本次数据发布。
