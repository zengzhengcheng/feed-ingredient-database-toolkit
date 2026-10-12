from __future__ import annotations

import sqlite3
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .database import connect, seed_dictionaries, update_metric_catalog, upsert_metric_catalog
from .i18n import group_label, retranslate_tree, tr
from .unit_resolution import DEFAULT_UNIT_RESOLVER


class MetricCatalogManager(QWidget):
    """Editable common-metric groups; aliases remain in the separate name dictionary."""

    HEADERS = ["ID", "语境", "分组", "规范指标", "默认单位", "默认基础", "启用", "备注"]

    def __init__(self, database_path: Path, changed_callback=None, parent=None):
        super().__init__(parent)
        self.database_path = database_path
        self.changed_callback = changed_callback
        self.selected_id: int | None = None
        layout = QVBoxLayout(self)
        explanation = QLabel(
            "这里就是指标选择器使用的‘常用指标分组’，不是别名词典。每个启用项都会按分组出现在选择器中。"
            "可选择已有组继续添加，也可直接输入新组；名称的不同写法仍在“管理名称词典”中处理。"
        )
        explanation.setWordWrap(True); layout.addWidget(explanation)

        form = QFormLayout()
        self.context = QComboBox(); self.context.setEditable(True)
        self.context.addItems(["composition", "energy", "AID", "SID", "ATTD", "STTD", "CATTD"])
        self.group = QComboBox(); self.group.setEditable(True)
        self.group.lineEdit().setPlaceholderText("选择已有分组，或直接输入新分组")
        self.context.currentTextChanged.connect(self.refresh_group_choices)
        self.context.currentTextChanged.connect(self._apply_context_default)
        self.metric = QLineEdit(); self.metric.setPlaceholderText("规范指标名称")
        self.unit = QComboBox(); self.unit.setEditable(True)
        self.unit.addItems([*DEFAULT_UNIT_RESOLVER.available_units, ""])
        self.basis = QComboBox(); self.basis.setEditable(True); self.basis.addItems(["dry matter", "as-fed", ""])
        self.active = QComboBox(); self.active.addItems(["启用", "停用"])
        self.note = QLineEdit()
        for label, field in (
            ("测量语境", self.context), ("分组名称 *", self.group), ("规范指标 *", self.metric),
            ("默认单位", self.unit), ("默认基础", self.basis), ("状态", self.active), ("备注", self.note),
        ):
            form.addRow(label, field)
        layout.addLayout(form)

        actions = QHBoxLayout()
        save = QPushButton("保存 / 更新"); save.clicked.connect(self.save_entry)
        new = QPushButton("新建一项"); new.clicked.connect(self.clear_form)
        deactivate = QPushButton("停用选中项"); deactivate.clicked.connect(self.deactivate_selected)
        refresh = QPushButton("刷新"); refresh.clicked.connect(self.refresh)
        for button in (save, new, deactivate, refresh): actions.addWidget(button)
        actions.addStretch(); layout.addLayout(actions)

        self.table = QTableWidget(0, len(self.HEADERS)); self.table.setHorizontalHeaderLabels(self.HEADERS)
        self.table.setAlternatingRowColors(True); self.table.cellClicked.connect(self.load_row)
        self.table.setColumnHidden(0, True); self.table.setColumnWidth(2, 140); self.table.setColumnWidth(3, 190)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)
        self.refresh()
        self.retranslate_ui()

    def retranslate_ui(self):
        retranslate_tree(self)
        self.group.blockSignals(True)
        for index in range(self.group.count()):
            raw_group = self.group.itemData(index)
            if raw_group is not None:
                self.group.setItemText(index, group_label(str(raw_group)))
        self.group.blockSignals(False)
        active_index = self.active.currentIndex()
        self.active.setItemText(0, tr("启用", "Active")); self.active.setItemText(1, tr("停用", "Inactive"))
        self.active.setCurrentIndex(active_index)
        headers = [
            "ID", tr("语境", "Context"), tr("分组", "Group"), tr("规范指标", "Canonical Metric"),
            tr("默认单位", "Default Unit"), tr("默认基础", "Default Basis"), tr("启用", "Active"), tr("备注", "Note"),
        ]
        self.table.setHorizontalHeaderLabels(headers)
        for row in range(self.table.rowCount()):
            group_item = self.table.item(row, 2)
            if group_item is not None:
                raw_group = str(group_item.data(Qt.ItemDataRole.UserRole) or group_item.text())
                group_item.setText(group_label(raw_group))
            item = self.table.item(row, 6)
            if item is not None:
                item.setText(tr("是", "Yes") if bool(item.data(Qt.ItemDataRole.UserRole)) else tr("否", "No"))

    def refresh_group_choices(self):
        current = self._group_value()
        with connect(self.database_path) as conn:
            groups = [row[0] for row in conn.execute(
                """SELECT group_name FROM metric_catalog WHERE context=?
                   GROUP BY group_name ORDER BY min(sort_order),group_name""",
                (self.context.currentText().strip(),),
            )]
        self.group.blockSignals(True); self.group.clear()
        for group in groups:
            self.group.addItem(group_label(group), group)
        match = self.group.findData(current)
        if match >= 0:
            self.group.setCurrentIndex(match)
        else:
            self.group.setCurrentText(current if current and current not in groups else "")
        self.group.blockSignals(False)

    def _group_value(self) -> str:
        value = self.group.currentData()
        return str(value if value is not None else self.group.currentText()).strip()

    def _apply_context_default(self):
        if self.selected_id is None:
            self.unit.setCurrentText("MJ/kg" if self.context.currentText().casefold() == "energy" else "%")

    def refresh(self):
        with connect(self.database_path) as conn:
            seed_dictionaries(conn)
            rows = conn.execute(
                """SELECT metric_catalog_id,context,group_name,canonical_metric,default_unit,
                          default_basis,active,note FROM metric_catalog
                   ORDER BY context,group_name,sort_order,canonical_metric"""
            ).fetchall()
        self.table.setRowCount(len(rows))
        for row_number, row in enumerate(rows):
            values = [*row[:6], tr("是", "Yes") if row[6] else tr("否", "No"), row[7] or ""]
            for column, value in enumerate(values):
                item = QTableWidgetItem(group_label(value) if column == 2 else str(value))
                if column == 2:
                    item.setData(Qt.ItemDataRole.UserRole, str(value))
                if column == 6:
                    item.setData(Qt.ItemDataRole.UserRole, bool(row[6]))
                self.table.setItem(row_number, column, item)
        self.refresh_group_choices()

    def load_row(self, row: int, _column: int):
        self.selected_id = int(self.table.item(row, 0).text())
        self.context.setCurrentText(self.table.item(row, 1).text())
        raw_group = str(self.table.item(row, 2).data(Qt.ItemDataRole.UserRole) or self.table.item(row, 2).text())
        group_index = self.group.findData(raw_group)
        if group_index >= 0:
            self.group.setCurrentIndex(group_index)
        else:
            self.group.setCurrentText(raw_group)
        self.metric.setText(self.table.item(row, 3).text())
        self.unit.setCurrentText(self.table.item(row, 4).text())
        self.basis.setCurrentText(self.table.item(row, 5).text())
        self.active.setCurrentIndex(0 if bool(self.table.item(row, 6).data(Qt.ItemDataRole.UserRole)) else 1)
        self.note.setText(self.table.item(row, 7).text())

    def clear_form(self):
        self.selected_id = None
        self.context.setCurrentText("composition"); self.group.setCurrentText(""); self.metric.clear()
        self.unit.setCurrentText("%"); self.basis.setCurrentText("dry matter")
        self.active.setCurrentIndex(0); self.note.clear(); self.table.clearSelection()

    def save_entry(self):
        context = self.context.currentText().strip(); group = self._group_value(); metric = self.metric.text().strip()
        if not context or not group or not metric:
            QMessageBox.warning(self, tr("缺少内容", "Missing Content"), tr("测量语境、分组名称和规范指标都必须填写。", "Measurement context, group name, and canonical metric are required."))
            return
        try:
            with connect(self.database_path) as conn:
                arguments = (
                    context, group, metric, self.unit.currentText(), self.basis.currentText(),
                    self.active.currentIndex() == 0, self.note.text(),
                )
                if self.selected_id is None:
                    upsert_metric_catalog(conn, *arguments)
                else:
                    update_metric_catalog(conn, self.selected_id, *arguments)
        except sqlite3.IntegrityError as exc:
            QMessageBox.critical(self, tr("不能保存", "Cannot Save"), tr(f"同一语境和分组中不能有重复指标。\n{exc}", f"Duplicate metrics are not allowed within the same context and group.\n{exc}"))
            return
        self.refresh()
        if self.changed_callback: self.changed_callback()
        QMessageBox.information(self, tr("已保存", "Saved"), tr("指标分组已经更新，重新打开指标选择器即可使用。", "The metric catalog has been updated and is available when the selector is reopened."))

    def deactivate_selected(self):
        if self.selected_id is None:
            QMessageBox.warning(self, tr("尚未选择", "Nothing Selected"), tr("请先点击表格中的一项。", "Select a table row first."))
            return
        self.active.setCurrentIndex(1); self.save_entry()
