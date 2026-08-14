from __future__ import annotations

from pathlib import Path

from PyQt6.QtWidgets import (
    QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .term_review import (
    create_standard_from_candidate,
    list_candidate_terms,
    resolve_candidate_as_alias,
)
from .unit_resolution import DEFAULT_UNIT_RESOLVER
from .i18n import group_label, retranslate_tree, tr


class TermReviewWidget(QWidget):
    """One-screen review for unmatched names and units."""

    HEADERS = ["ID", "类别", "原始写法", "出现次数", "来源示例", "相近标准项"]

    def __init__(self, database_path: Path, changed_callback=None, parent=None):
        super().__init__(parent)
        self.database_path = database_path
        self.changed_callback = changed_callback
        self.selected_id: int | None = None
        self.selected_category = ""
        self.selected_context = ""
        self.candidates = []

        layout = QVBoxLayout(self)
        note = QLabel(
            "这里集中处理导入时不认识的原料、组分、单位等词。点击候选词后，"
            "可将它映射到已有标准项，或建立一个新的标准项；两种操作都会立即更新词典和已入库记录，raw字段保持不变。"
        )
        note.setWordWrap(True); layout.addWidget(note)

        filters = QHBoxLayout()
        self.category_filter = QComboBox()
        self.category_filter.addItem("全部类别", "")
        for value, label in (
            ("ingredient", "原料"), ("metric", "组分/指标"), ("unit", "单位"),
            ("basis", "基础"), ("species", "动物种类"),
        ):
            self.category_filter.addItem(label, value)
        self.category_filter.currentIndexChanged.connect(self.refresh)
        refresh = QPushButton("刷新候选词"); refresh.clicked.connect(self.refresh)
        self.summary = QLabel()
        filters.addWidget(QLabel("筛选")); filters.addWidget(self.category_filter)
        filters.addWidget(refresh); filters.addWidget(self.summary); filters.addStretch()
        layout.addLayout(filters)

        self.table = QTableWidget(0, len(self.HEADERS)); self.table.setHorizontalHeaderLabels(self.HEADERS)
        self.table.setAlternatingRowColors(True); self.table.cellClicked.connect(self.load_candidate)
        self.table.setColumnHidden(0, True); self.table.setColumnWidth(2, 180); self.table.setColumnWidth(4, 330)
        self.table.setColumnWidth(5, 260); self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)

        form = QFormLayout()
        self.raw = QLineEdit(); self.raw.setReadOnly(True)
        self.canonical = QComboBox(); self.canonical.setEditable(True)
        self.canonical.lineEdit().setPlaceholderText("选择相近标准项，或输入新标准名称")
        self.group = QComboBox(); self.group.setEditable(True)
        for group in ("常用组分", "必需氨基酸", "非必需氨基酸", "微量元素", "维生素", "抗营养因子", "霉菌毒素", "其他"):
            self.group.addItem(group_label(group), group)
        self.group.currentTextChanged.connect(self._refresh_unit_options)
        self.unit = QComboBox(); self.unit.setEditable(True)
        self.unit.addItems(["", *DEFAULT_UNIT_RESOLVER.available_units])
        self.basis = QComboBox(); self.basis.setEditable(True); self.basis.addItems(["", "dry matter", "as-fed", "not applicable"])
        self.review_note = QLineEdit(); self.review_note.setPlaceholderText("可填写核对依据或原文说明")
        for label, field in (
            ("候选原始写法", self.raw), ("统一名称", self.canonical),
            ("新组分所属分组", self.group), ("新组分默认单位", self.unit),
            ("新组分默认基础", self.basis), ("核对说明", self.review_note),
        ):
            form.addRow(label, field)
        layout.addLayout(form)

        actions = QHBoxLayout()
        alias = QPushButton("映射到已有标准项"); alias.clicked.connect(self.map_existing)
        create = QPushButton("建立新标准项"); create.clicked.connect(self.create_new)
        defer = QPushButton("暂缓判断"); defer.clicked.connect(self.clear_selection)
        actions.addWidget(alias); actions.addWidget(create); actions.addWidget(defer); actions.addStretch()
        layout.addLayout(actions)
        self.refresh()
        self.retranslate_ui()

    def retranslate_ui(self):
        retranslate_tree(self)
        category_labels = {
            "": tr("全部类别", "All Categories"), "ingredient": tr("原料", "Ingredient"),
            "metric": tr("组分/指标", "Component / Metric"), "unit": tr("单位", "Unit"),
            "basis": tr("基础", "Basis"), "species": tr("动物种类", "Animal Species"),
        }
        for index in range(self.category_filter.count()):
            value = self.category_filter.itemData(index) or ""
            self.category_filter.setItemText(index, category_labels.get(value, str(value)))
        for index in range(self.group.count()):
            raw_group = self.group.itemData(index)
            if raw_group is not None:
                self.group.setItemText(index, group_label(str(raw_group)))
        self.table.setHorizontalHeaderLabels([
            "ID", tr("类别", "Category"), tr("原始写法", "Raw Term"), tr("出现次数", "Occurrences"),
            tr("来源示例", "Source Examples"), tr("相近标准项", "Similar Standards"),
        ])
        self.summary.setText(tr(f"待核对 {self.table.rowCount()} 项", f"{self.table.rowCount()} candidates pending review"))

    def refresh(self):
        category = self.category_filter.currentData() if hasattr(self, "category_filter") else ""
        rows = list_candidate_terms(self.database_path, category or "")
        self.candidates = rows
        self.table.setRowCount(len(rows))
        for row_number, candidate in enumerate(rows):
            values = (
                candidate.dictionary_id, candidate.category, candidate.raw_term, candidate.occurrences,
                "；".join(candidate.source_examples), "；".join(candidate.suggestions),
            )
            for column, value in enumerate(values):
                self.table.setItem(row_number, column, QTableWidgetItem(str(value)))
        self.summary.setText(tr(f"待核对 {len(rows)} 项", f"{len(rows)} candidates pending review"))

    def load_candidate(self, row: int, _column: int):
        candidate = self.candidates[row]
        self.selected_id = candidate.dictionary_id
        self.selected_category = candidate.category
        self.selected_context = candidate.context
        self.raw.setText(candidate.raw_term)
        suggestions = [item for item in self.table.item(row, 5).text().split("；") if item]
        self.canonical.clear(); self.canonical.addItems(suggestions)
        self.canonical.setCurrentText(suggestions[0] if suggestions else self.raw.text())
        is_metric = self.selected_category == "metric"
        for field in (self.group, self.unit, self.basis): field.setEnabled(is_metric)
        self._refresh_unit_options()

    def _refresh_unit_options(self):
        current = self.unit.currentText().strip()
        context = self.selected_context.split(" / ", 1)[0]
        if not current:
            current = "kJ/kg" if context.casefold() == "energy" else (
                "%" if context.casefold() in {"composition", "aid", "sid", "attd", "sttd"} else ""
            )
        options = DEFAULT_UNIT_RESOLVER.possible_units(
            context, self.canonical.currentText(), self._group_value(), current
        )
        self.unit.blockSignals(True)
        self.unit.clear(); self.unit.addItems(["", *options])
        self.unit.setCurrentText(current)
        self.unit.blockSignals(False)

    def _group_value(self) -> str:
        value = self.group.currentData()
        return str(value if value is not None else self.group.currentText()).strip()

    def _require_selection(self) -> bool:
        if self.selected_id is None:
            QMessageBox.warning(self, tr("尚未选择", "Nothing Selected"), tr("请先点击一条候选词。", "Select a candidate term first."))
            return False
        return True

    def map_existing(self):
        if not self._require_selection(): return
        try:
            resolve_candidate_as_alias(
                self.database_path, self.selected_id, self.canonical.currentText(), self.review_note.text()
            )
        except ValueError as exc:
            QMessageBox.warning(self, tr("无法映射", "Cannot Map"), str(exc)); return
        self._finish(tr("候选写法已映射到已有标准项；原始字段没有改变。", "The candidate was mapped to an existing standard; raw fields were not changed."))

    def create_new(self):
        if not self._require_selection(): return
        try:
            create_standard_from_candidate(
                self.database_path, self.selected_id, self.canonical.currentText(), self._group_value(),
                self.unit.currentText(), self.basis.currentText(), self.review_note.text(),
            )
        except ValueError as exc:
            QMessageBox.warning(self, tr("无法建立", "Cannot Create"), str(exc)); return
        message = tr("新组分已加入指标分类，同时写入名称词典。", "The new metric was added to the metric catalog and name dictionary.") if self.selected_category == "metric" else tr("新标准项已写入词典。", "The new standard was added to the dictionary.")
        self._finish(message + tr(" 原始字段没有改变。", " Raw fields were not changed."))

    def _finish(self, message: str):
        self.refresh(); self.clear_selection()
        if self.changed_callback: self.changed_callback()
        QMessageBox.information(self, tr("核对完成", "Review Complete"), message)

    def clear_selection(self):
        self.selected_id = None; self.selected_category = ""; self.selected_context = ""
        self.raw.clear(); self.canonical.clear(); self.review_note.clear()
        self.table.clearSelection()
