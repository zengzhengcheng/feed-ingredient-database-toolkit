from __future__ import annotations

from PyQt6.QtWidgets import (
    QAbstractButton, QGroupBox, QLabel, QLineEdit, QMainWindow, QPlainTextEdit,
    QTabWidget, QTableWidget, QWidget,
)


_language = "zh"


TRANSLATIONS = {
    "界面语言": "Interface Language",
    "导入与核对": "Import & Review",
    "查询数据": "Query Data",
    "词典与指标": "Dictionary & Metrics",
    "查看数据库": "Browse Database",
    "使用指南": "User Guide",
    "无法打开使用指南": "Cannot Open User Guide",
    "核对/切换单位": "Review / Switch Units",
    "核对和切换单位": "Review and Switch Units",
    "采用单位（可切换）": "Assigned Unit (editable)",
    "源表单位": "Source Unit",
    "空白": "blank",
    "没有指标": "No Metrics",
    "请先导入包含指标的数据文件。": "Import a data file containing metrics first.",
    "待核对新词": "Candidate Terms",
    "名称词典": "Name Dictionary",
    "指标分类": "Metric Catalog",
    "关键词查询": "Keyword Search",
    "搜索": "Search",
    "自定义规则（只读 SQL）": "Custom Rule (read-only SQL)",
    "查询": "Search",
    "清空": "Clear",
    "导出当前结果": "Export Results",
    "输入多个关键词，例如：玉米 SID 仔猪；同时搜索标题、DOI、原料、指标和动物": "Enter keywords, e.g. corn SID piglet; searches title, DOI, ingredient, metric, and animal",
    "高级规则只允许执行一条 SELECT 或 WITH 查询，并通过只读连接访问数据库。可从 .sql 文件载入规则；导出支持 CSV、JSON 和 Excel。": "Advanced rules accept one SELECT or WITH query through a read-only database connection. Rules can be loaded from .sql files and exported as CSV, JSON, or Excel.",
    "输入 SELECT 或 WITH 查询": "Enter a SELECT or WITH query",
    "运行只读规则": "Run Read-only Rule",
    "载入 .sql 文件": "Load .sql File",
    "导出规则结果": "Export Rule Results",
    "尚未运行规则。": "No rule has been run.",
    "刷新表": "Refresh Table",
    "数据表": "Table",
    "名称词典就是“别名对照表”：raw term 是原 Excel 中的写法，canonical term 是统一名称。新词先作为 candidate；确认无误后改成 approved。组分名称全局共用，只需核对一次。": "The name dictionary maps raw terms from source files to canonical terms. New terms begin as candidates and become approved only after review. Component names are shared globally and reviewed once.",
    "保存对应关系": "Save Mapping",
    "类别": "Category",
    "指标语境": "Metric Context",
    "原写法 / 别名": "Raw Term / Alias",
    "统一名称": "Canonical Name",
    "状态": "Status",
    "选择营养价值指标": "Select Nutritional Metrics",
    "选择原料成分指标": "Select Composition Metrics",
    "常用指标分组": "Common Metric Group",
    "搜索指标；结果仍保留所属分组": "Search metrics; results retain their groups",
    "也可输入新指标；多个指标用逗号或换行分开": "Enter new metrics separated by commas or new lines",
    "全选当前显示": "Select Visible",
    "取消当前显示": "Clear Visible",
    "选择全部必需 + 非必需氨基酸": "Select All Essential + Nonessential Amino Acids",
    "新指标": "New Metric",
    "单位": "Unit",
    "基础": "Basis",
    "单位和基础按指标保存。建议单位排在前面，同时保留系统全部已知单位。能量未填单位时先用指标规则，再按数值量级判定MJ/kg、kcal/kg或kJ/kg；量级不能唯一判断时分配可编辑的MJ/kg默认值并提示核对。": "Units and bases are stored per metric. Likely units are ranked first while the full approved unit list remains available. Blank energy units use metric rules and then value magnitude to distinguish MJ/kg, kcal/kg, or kJ/kg; ambiguous magnitudes receive an editable MJ/kg default plus a review warning.",
    "文件是唯一数据入口：选择历史 Excel、标准 Excel 或 JSON，系统先把论文元信息、原料成分和动物营养价值载入草稿。本页只用于核对和修改；导入不会写数据库，确认后才整批保存。标题必填，DOI 可空。": "Files are the only data-entry route. Select historical Excel, standard Excel, or JSON to load article metadata, ingredient composition, and animal nutritional values into a review draft. Loading does not write the database; the full batch is saved only after confirmation. Title is required and DOI is optional.",
    "从文件识别的论文元信息（可核对修改）": "Article Metadata Detected from File (editable)",
    "必填；中文或英文原题": "Required; original Chinese or English title",
    "可选；中文期刊无 DOI 时留空": "Optional; leave blank when no DOI exists",
    "标题 *": "Title *",
    "期刊": "Journal",
    "年份": "Year",
    "研究动物": "Study Animal",
    "国家/地区": "Country / Region",
    "原料与成分": "Ingredients & Composition",
    "动物信息": "Animal Information",
    "营养价值": "Nutritional Value",
    "选择 Excel / JSON": "Select Excel / JSON",
    "检查全部数据": "Validate All Data",
    "确认写入工作数据库": "Commit to Working Database",
    "尚未选择文件。载入后可在表格中修改、复制、粘贴或删除导入行。": "No file selected. After loading, rows can be edited, copied, pasted, or deleted in the grids.",
    "删除选中的导入行": "Delete Selected Imported Rows",
    "删除选中行": "Delete Selected Rows",
    "指标列由文件自动识别；系统编号用于建立关系，无需手工创建。": "Metric columns are detected from the file; system keys establish relationships and do not need manual creation.",
    "动物信息来自文件；营养价值保存时自动关联原料和动物组。": "Animal data come from the file; nutritional values are linked to ingredients and animal groups on save.",
    "测量语境、指标和原料—动物关系均从文件识别。": "Measurement context, metrics, and ingredient–animal relationships are detected from the file.",
    "这里集中处理导入时不认识的原料、组分、单位等词。点击候选词后，可将它映射到已有标准项，或建立一个新的标准项；两种操作都会立即更新词典和已入库记录，raw字段保持不变。": "Review unrecognized ingredients, metrics, units, and other terms here. Map a candidate to an existing standard or create a new standard; both update the dictionary and stored canonical fields while raw fields remain unchanged.",
    "全部类别": "All Categories",
    "原料": "Ingredient",
    "组分/指标": "Component / Metric",
    "动物种类": "Animal Species",
    "刷新候选词": "Refresh Candidates",
    "筛选": "Filter",
    "选择相近标准项，或输入新标准名称": "Select a similar standard or enter a new canonical name",
    "选择已有分组，或直接输入新分组": "Select an existing group or enter a new group",
    "可填写核对依据或原文说明": "Optional review evidence or source note",
    "候选原始写法": "Candidate Raw Term",
    "语境": "Context",
    "所属测量区块（不是候选词）": "Measurement Block (not a candidate term)",
    "新组分所属分组": "Group for New Metric",
    "新组分默认单位": "Default Unit for New Metric",
    "新组分默认基础": "Default Basis for New Metric",
    "核对说明": "Review Note",
    "映射到已有标准项": "Map to Existing Standard",
    "建立新标准项": "Create New Standard",
    "暂缓判断": "Defer Decision",
    "这里就是指标选择器使用的‘常用指标分组’，不是别名词典。每个启用项都会按分组出现在选择器中。可选择已有组继续添加，也可直接输入新组；名称的不同写法仍在“管理名称词典”中处理。": "These are the common metric groups used by the selector, not the alias dictionary. Active entries appear by group. Add to an existing group or create a new group; alternative name spellings remain in the name dictionary.",
    "规范指标名称": "Canonical Metric Name",
    "测量语境": "Measurement Context",
    "分组名称 *": "Group Name *",
    "规范指标 *": "Canonical Metric *",
    "默认单位": "Default Unit",
    "默认基础": "Default Basis",
    "备注": "Note",
    "保存 / 更新": "Save / Update",
    "新建一项": "New Entry",
    "停用选中项": "Deactivate Selected",
    "刷新": "Refresh",
    "启用": "Active",
    "停用": "Inactive",
    "是": "Yes",
    "否": "No",
    "系统原料编号": "System Ingredient Key",
    "原料名称 *": "Ingredient Name *",
    "原文编号": "Source ID",
    "原料类型": "Ingredient Type",
    "加工方式": "Processing",
    "产地": "Origin",
    "报告基础": "Reported Basis",
    "干物质值": "Dry Matter Value",
    "系统动物组编号": "System Animal Group Key",
    "试验编号": "Trial ID",
    "品种": "Breed",
    "性别": "Sex",
    "动物类型": "Animal Type",
    "阶段": "Period",
    "日龄": "Age (days)",
    "周龄": "Age (weeks)",
    "体重 kg": "Body Weight kg",
    "ID": "ID",
    "分组": "Group",
    "规范指标": "Canonical Metric",
    "启用": "Active",
    "原始写法": "Raw Term",
    "出现次数": "Occurrences",
    "来源示例": "Source Examples",
    "相近标准项": "Similar Standards",
    "更新时间": "Updated",
}


GROUP_TRANSLATIONS = {
    "常用组分": "Common Components",
    "必需氨基酸": "Essential Amino Acids",
    "非必需氨基酸": "Nonessential Amino Acids",
    "微量元素": "Trace Elements",
    "维生素": "Vitamins",
    "抗营养因子": "Antinutritional Factors",
    "霉菌毒素": "Mycotoxins",
    "其他": "Other",
    "名称词典其他指标": "Other Metrics from the Name Dictionary",
}


def set_language(language: str) -> None:
    global _language
    _language = "en" if str(language).casefold().startswith("en") else "zh"


def current_language() -> str:
    return _language


def tr(chinese: str, english: str | None = None, **values) -> str:
    template = chinese if _language == "zh" else (english or TRANSLATIONS.get(chinese, chinese))
    return template.format(**values) if values else template


def group_label(raw_group: str) -> str:
    """Translate built-in group labels without changing their stored database value."""
    raw = str(raw_group or "")
    return raw if _language == "zh" else GROUP_TRANSLATIONS.get(raw, raw)


def _source(obj, attribute: str, current: str) -> str:
    storage = f"_i18n_source_{attribute}"
    if not hasattr(obj, storage):
        setattr(obj, storage, current)
    return getattr(obj, storage)


def retranslate_tree(root: QWidget) -> None:
    """Translate static widget chrome while leaving scientific/database values untouched."""
    objects = [root, *root.findChildren(QWidget)]
    for obj in objects:
        if bool(obj.property("_i18n_skip")):
            continue
        if isinstance(obj, (QLabel, QAbstractButton)):
            source = _source(obj, "text", obj.text())
            if source in TRANSLATIONS:
                obj.setText(tr(source))
        if isinstance(obj, QGroupBox):
            source = _source(obj, "title", obj.title())
            if source in TRANSLATIONS:
                obj.setTitle(tr(source))
        if isinstance(obj, (QLineEdit, QPlainTextEdit)):
            source = _source(obj, "placeholder", obj.placeholderText())
            if source in TRANSLATIONS:
                obj.setPlaceholderText(tr(source))
        if isinstance(obj, QTabWidget):
            if not hasattr(obj, "_i18n_tab_sources"):
                obj._i18n_tab_sources = [obj.tabText(index) for index in range(obj.count())]
            for index, source in enumerate(obj._i18n_tab_sources):
                if index < obj.count() and source in TRANSLATIONS:
                    obj.setTabText(index, tr(source))
        if isinstance(obj, QTableWidget):
            if not hasattr(obj, "_i18n_header_sources"):
                obj._i18n_header_sources = [
                    obj.horizontalHeaderItem(index).text() if obj.horizontalHeaderItem(index) else ""
                    for index in range(obj.columnCount())
                ]
            for index, source in enumerate(obj._i18n_header_sources):
                if index < obj.columnCount() and source in TRANSLATIONS:
                    item = obj.horizontalHeaderItem(index)
                    if item is not None:
                        item.setText(tr(source))


def window_title(database_name: str) -> str:
    return tr(
        f"饲料原料数据库辅助工具 — {database_name}",
        f"Feed Ingredient Database Assistant — {database_name}",
    )


def status_database(database: object, read_only: bool) -> str:
    return tr(
        f"当前数据库：{database} | 模式：{'只读（不会修改数据库）' if read_only else '可编辑工作库'}",
        f"Current database: {database} | Mode: {'read-only (no database changes)' if read_only else 'editable working database'}",
    )
