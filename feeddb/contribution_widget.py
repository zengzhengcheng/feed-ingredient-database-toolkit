from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeySequence
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox,
    QFileDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QTabWidget, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from .contribution_batch import (
    AnimalGridRow, ContributionBatch, IngredientGridRow, MetricSpec, NutritionGridRow,
    save_contribution_batch, validate_contribution_batch,
)
from .contribution_file import load_contribution_file
from .database import connect, lookup_dictionary, validation_dictionary_connection
from .unit_resolution import DEFAULT_UNIT_RESOLVER
from .i18n import group_label, retranslate_tree, tr


def _text(table: QTableWidget, row: int, column: int) -> str:
    item = table.item(row, column)
    return item.text().strip() if item else ""


class PasteTable(QTableWidget):
    """Small Excel-like grid supporting rectangular copy, paste, Delete, and automatic row growth."""

    def __init__(self, headers: list[str], rows: int = 12):
        super().__init__(rows, len(headers))
        self.setHorizontalHeaderLabels(headers)
        self.setAlternatingRowColors(True)
        self.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectItems)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.verticalHeader().setDefaultSectionSize(24)

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.StandardKey.Paste):
            self.paste_from_clipboard(); return
        if event.matches(QKeySequence.StandardKey.Copy):
            self.copy_to_clipboard(); return
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            for item in self.selectedItems(): item.setText("")
            return
        super().keyPressEvent(event)

    def paste_from_clipboard(self):
        clipboard_text = QApplication.clipboard().text()
        if not clipboard_text:
            return
        rows = [line.split("\t") for line in clipboard_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
        if rows and rows[-1] == [""]:
            rows.pop()
        start_row = max(self.currentRow(), 0); start_column = max(self.currentColumn(), 0)
        self.setRowCount(max(self.rowCount(), start_row + len(rows)))
        for row_offset, values in enumerate(rows):
            for column_offset, value in enumerate(values):
                column = start_column + column_offset
                if column >= self.columnCount():
                    continue
                self.setItem(start_row + row_offset, column, QTableWidgetItem(value))

    def copy_to_clipboard(self):
        indexes = self.selectedIndexes()
        if not indexes:
            return
        min_row = min(index.row() for index in indexes); max_row = max(index.row() for index in indexes)
        min_col = min(index.column() for index in indexes); max_col = max(index.column() for index in indexes)
        lines = []
        for row in range(min_row, max_row + 1):
            lines.append("\t".join(_text(self, row, col) for col in range(min_col, max_col + 1)))
        QApplication.clipboard().setText("\n".join(lines))

    def add_rows(self, count: int = 10):
        self.setRowCount(self.rowCount() + count)

    def remove_selected_rows(self):
        rows = sorted({index.row() for index in self.selectedIndexes()}, reverse=True)
        for row in rows:
            self.removeRow(row)


class MetricDialog(QDialog):
    GROUPED_COMMON = "全部常用指标（按分组）"
    ALL_KNOWN = "所有已知指标（含名称词典）"

    def __init__(self, database_path: Path, nutrition: bool, amino_acids: bool = False, parent=None):
        super().__init__(parent)
        self.database_path = database_path
        self.nutrition = nutrition
        self.checked: dict[tuple[str, str], tuple[str, str, str, str]] = {}
        self._loading = False
        self.setWindowTitle("选择营养价值指标" if nutrition else "选择原料成分指标")
        self.resize(660, 680)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.context = QComboBox()
        self.context.addItems(["energy", "AID", "SID", "ATTD", "STTD", "CATTD"] if nutrition else ["composition"])
        if amino_acids:
            self.context.setCurrentText("SID")
        self.group = QComboBox()
        self.context.currentTextChanged.connect(self.reload_groups)
        self.group.currentTextChanged.connect(self.load_group)
        self.search = QLineEdit(); self.search.setPlaceholderText("搜索指标；结果仍保留所属分组")
        self.search.textChanged.connect(self.filter_terms)
        self.custom = QLineEdit(); self.custom.setPlaceholderText("也可输入新指标；多个指标用逗号或换行分开")
        self.unit = QComboBox(); self.unit.setEditable(True)
        self.unit.addItems([*DEFAULT_UNIT_RESOLVER.available_units, ""])
        self.unit.activated.connect(self.apply_unit_to_visible_checked)
        self.basis = QComboBox(); self.basis.addItems(["", "dry matter", "as-fed"])
        self.basis.activated.connect(self.apply_basis_to_visible_checked)
        form.addRow("测量语境", self.context); form.addRow("常用指标分组", self.group); form.addRow("搜索", self.search)
        layout.addLayout(form)
        self.list = QListWidget(); self.list.itemChanged.connect(self._item_changed)
        self.list.currentItemChanged.connect(self._show_item_unit_options)
        layout.addWidget(self.list)
        actions = QHBoxLayout()
        select_all = QPushButton("全选当前显示"); select_all.clicked.connect(self.select_current_group)
        clear_all = QPushButton("取消当前显示"); clear_all.clicked.connect(self.clear_current_group)
        self.selection_count = QLabel("已选 0 项")
        actions.addWidget(select_all); actions.addWidget(clear_all); actions.addWidget(self.selection_count); actions.addStretch()
        layout.addLayout(actions)
        if nutrition:
            amino_button = QPushButton("选择全部必需 + 非必需氨基酸")
            amino_button.clicked.connect(self.select_amino_acids)
            layout.addWidget(amino_button)
        bottom = QFormLayout(); bottom.addRow("新指标", self.custom); bottom.addRow("单位", self.unit); bottom.addRow("基础", self.basis)
        layout.addLayout(bottom)
        hint = QLabel(
            "单位和基础按指标保存。建议单位排在前面，同时保留系统全部已知单位。"
            "能量未填单位时先用指标规则，再按数值量级判定MJ/kg、kcal/kg或kJ/kg；"
            "量级不能唯一判断时分配可编辑的kJ/kg默认值并提示核对。"
        )
        hint.setWordWrap(True); layout.addWidget(hint)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept); self.buttons.rejected.connect(self.reject); layout.addWidget(self.buttons)
        self.reload_groups()
        if amino_acids:
            self.select_amino_acids()
        self.retranslate_ui()

    def retranslate_ui(self):
        self.setWindowTitle(tr(
            "选择营养价值指标" if self.nutrition else "选择原料成分指标",
            "Select Nutritional Metrics" if self.nutrition else "Select Composition Metrics",
        ))
        retranslate_tree(self)
        self._update_count()
        self.group.blockSignals(True)
        for index in range(self.group.count()):
            value = self.group.itemData(index)
            if value == self.GROUPED_COMMON:
                self.group.setItemText(index, tr(self.GROUPED_COMMON, "All Common Metrics (grouped)"))
            elif value == self.ALL_KNOWN:
                self.group.setItemText(index, tr(self.ALL_KNOWN, "All Known Metrics (including aliases)"))
            elif value is not None:
                self.group.setItemText(index, group_label(str(value)))
        self.group.blockSignals(False)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText(tr("确定", "OK"))
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(tr("取消", "Cancel"))

    def _selected_group(self) -> str:
        return str(self.group.currentData() or self.group.currentText())

    def _save_visible_checks(self):
        if self._loading:
            return
        for index in range(self.list.count()):
            item = self.list.item(index)
            data = item.data(Qt.ItemDataRole.UserRole)
            if not isinstance(data, tuple) or len(data) != 4:
                continue
            context, name, unit, basis = data
            key = (context, name.casefold())
            if item.checkState() == Qt.CheckState.Checked:
                self.checked[key] = (context, name, unit, basis)
            else:
                self.checked.pop(key, None)

    def reload_groups(self):
        self._save_visible_checks()
        context = self.context.currentText()
        with connect(self.database_path) as conn:
            groups = [row[0] for row in conn.execute(
                """SELECT group_name FROM metric_catalog WHERE context=? AND active=1
                   GROUP BY group_name ORDER BY min(sort_order),group_name""",
                (context,),
            )]
        self.group.blockSignals(True)
        self.group.clear()
        self.group.addItem(tr(self.GROUPED_COMMON, "All Common Metrics (grouped)"), self.GROUPED_COMMON)
        self.group.addItem(tr(self.ALL_KNOWN, "All Known Metrics (including aliases)"), self.ALL_KNOWN)
        for group in groups:
            self.group.addItem(group_label(str(group)), str(group))
        self.group.blockSignals(False)
        self.load_group()

    def _group_rows(self) -> list[tuple[str, str, str, str]]:
        context = self.context.currentText(); group_name = self._selected_group()
        with connect(self.database_path) as conn:
            catalog = conn.execute(
                """SELECT group_name,canonical_metric,default_unit,default_basis FROM metric_catalog
                   WHERE context=? AND active=1 AND (? IN (?,?) OR group_name=?)
                   ORDER BY group_name,sort_order,canonical_metric""",
                (context, group_name, self.GROUPED_COMMON, self.ALL_KNOWN, group_name),
            ).fetchall()
            rows = [(str(row[0]), str(row[1]), str(row[2] or ""), str(row[3] or "")) for row in catalog]
            if group_name == self.ALL_KNOWN:
                known = {name.casefold() for _, name, _, _ in rows}
                for row in conn.execute(
                    """SELECT DISTINCT canonical_term FROM dictionaries
                       WHERE category='metric' AND context=? AND status='approved' ORDER BY canonical_term""",
                    (context,),
                ):
                    name = str(row[0])
                    if name.casefold() not in known:
                        rows.append(("名称词典其他指标", name, "kJ/kg" if context == "energy" else "%", ""))
                        known.add(name.casefold())
        return rows

    def load_group(self):
        self._save_visible_checks()
        context = self.context.currentText(); rows = self._group_rows()
        self._loading = True; self.list.clear()
        show_headers = self._selected_group() in (self.GROUPED_COMMON, self.ALL_KNOWN)
        previous_group = None
        for group_name, name, unit, basis in rows:
            if show_headers and group_name != previous_group:
                header = QListWidgetItem(f"▾ {group_label(group_name)}")
                font = header.font(); font.setBold(True); header.setFont(font)
                header.setFlags(Qt.ItemFlag.ItemIsEnabled)
                self.list.addItem(header); previous_group = group_name
            saved = self.checked.get((context, name.casefold()))
            item_context, item_name, item_unit, item_basis = saved or (context, name, unit, basis)
            item = QListWidgetItem(name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setData(Qt.ItemDataRole.UserRole, (item_context, item_name, item_unit, item_basis))
            item.setCheckState(Qt.CheckState.Checked if saved else Qt.CheckState.Unchecked)
            self.list.addItem(item)
        self._loading = False
        defaults = {unit for _, _, unit, _ in rows if unit}
        selected_default = defaults.pop() if len(defaults) == 1 else ("kJ/kg" if context == "energy" else "%")
        group_name = self._selected_group()
        self._set_unit_options(
            context,
            "" if group_name in (self.GROUPED_COMMON, self.ALL_KNOWN) else group_name,
            default_unit=selected_default,
        )
        bases = {basis for _, _, _, basis in rows if basis}
        self.basis.setCurrentText(bases.pop() if len(bases) == 1 else "")
        self.filter_terms(self.search.text())
        self._update_count()

    def _set_unit_options(self, context: str, group_name: str = "", metric: str = "", default_unit: str = ""):
        current = self.unit.currentText().strip()
        options = DEFAULT_UNIT_RESOLVER.possible_units(context, metric, group_name, default_unit)
        self.unit.blockSignals(True)
        self.unit.clear(); self.unit.addItems([*options, ""])
        self.unit.setCurrentText(default_unit or (current if current in options else (options[0] if options else "")))
        self.unit.blockSignals(False)

    def _show_item_unit_options(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None):
        if current is None:
            return
        data = current.data(Qt.ItemDataRole.UserRole)
        if not isinstance(data, tuple) or len(data) != 4:
            return
        context, name, unit, _basis = data
        with connect(self.database_path) as conn:
            catalog = conn.execute(
                """SELECT group_name,default_unit FROM metric_catalog
                   WHERE context=? AND CASEFOLD(canonical_metric)=CASEFOLD(?) AND active=1
                   ORDER BY sort_order,metric_catalog_id LIMIT 1""",
                (context, name),
            ).fetchone()
        self._set_unit_options(
            context, str(catalog[0]) if catalog else "", name, unit or (str(catalog[1]) if catalog else "")
        )

    def filter_terms(self, text: str):
        needle = text.strip().casefold()
        header = None; group_has_visible_child = False; group_matches = False
        for index in range(self.list.count()):
            item = self.list.item(index)
            data = item.data(Qt.ItemDataRole.UserRole)
            if not isinstance(data, tuple) or len(data) != 4:
                if header is not None:
                    header.setHidden(not group_has_visible_child)
                header = item; group_has_visible_child = False
                group_matches = bool(needle and needle in item.text().casefold()); item.setHidden(True)
                continue
            hidden = bool(needle and not group_matches and needle not in item.text().casefold())
            item.setHidden(hidden); group_has_visible_child = group_has_visible_child or not hidden
        if header is not None:
            header.setHidden(not group_has_visible_child)

    def _item_changed(self, item: QListWidgetItem):
        if self._loading:
            return
        data = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(data, tuple) or len(data) != 4:
            return
        context, name, unit, basis = data
        key = (context, name.casefold())
        if item.checkState() == Qt.CheckState.Checked:
            self.checked[key] = (context, name, unit, basis)
        else:
            self.checked.pop(key, None)
        self._update_count()

    def _update_count(self):
        self.selection_count.setText(tr(f"已选 {len(self.checked)} 项", f"{len(self.checked)} selected"))

    def select_current_group(self):
        for index in range(self.list.count()):
            item = self.list.item(index)
            data = item.data(Qt.ItemDataRole.UserRole)
            if not item.isHidden() and isinstance(data, tuple) and len(data) == 4:
                item.setCheckState(Qt.CheckState.Checked)

    def clear_current_group(self):
        for index in range(self.list.count()):
            item = self.list.item(index)
            data = item.data(Qt.ItemDataRole.UserRole)
            if not item.isHidden() and isinstance(data, tuple) and len(data) == 4:
                item.setCheckState(Qt.CheckState.Unchecked)

    def apply_unit_to_visible_checked(self):
        unit = self.unit.currentText().strip()
        for index in range(self.list.count()):
            item = self.list.item(index)
            data = item.data(Qt.ItemDataRole.UserRole)
            if item.checkState() != Qt.CheckState.Checked or not isinstance(data, tuple) or len(data) != 4:
                continue
            context, name, _, basis = data
            item.setData(Qt.ItemDataRole.UserRole, (context, name, unit, basis))
            self.checked[(context, name.casefold())] = (context, name, unit, basis)

    def apply_basis_to_visible_checked(self):
        basis = self.basis.currentText()
        for index in range(self.list.count()):
            item = self.list.item(index)
            data = item.data(Qt.ItemDataRole.UserRole)
            if item.checkState() != Qt.CheckState.Checked or not isinstance(data, tuple) or len(data) != 4:
                continue
            context, name, unit, _ = data
            item.setData(Qt.ItemDataRole.UserRole, (context, name, unit, basis))
            self.checked[(context, name.casefold())] = (context, name, unit, basis)

    def select_amino_acids(self):
        self.context.setCurrentText("SID")
        with connect(self.database_path) as conn:
            rows = conn.execute(
                """SELECT context,canonical_metric,default_unit,default_basis FROM metric_catalog
                   WHERE context='SID' AND group_name IN ('必需氨基酸','非必需氨基酸') AND active=1
                   ORDER BY sort_order"""
            ).fetchall()
        for context, name, unit, basis in rows:
            self.checked[(str(context), str(name).casefold())] = (str(context), str(name), str(unit or "%"), str(basis or ""))
        self._loading = True; self.list.clear(); self._loading = False
        essential_index = self.group.findData("必需氨基酸")
        if essential_index >= 0:
            self.group.blockSignals(True)
            self.group.setCurrentIndex(essential_index)
            self.group.blockSignals(False)
        self.load_group()
        self._update_count()

    def selected_specs(self) -> list[MetricSpec]:
        self._save_visible_checks()
        specs = [MetricSpec(name, context, unit, basis) for context, name, unit, basis in self.checked.values()]
        seen = {(spec.context, spec.raw_metric.casefold()) for spec in specs}
        for name in (value.strip() for value in re.split(r"[,，;；\n]+", self.custom.text()) if value.strip()):
            key = (self.context.currentText(), name.casefold())
            if key in seen:
                continue
            seen.add(key)
            specs.append(MetricSpec(name, self.context.currentText(), self.unit.currentText().strip(), self.basis.currentText()))
        return specs


class UnitReviewDialog(QDialog):
    """Edit the effective unit while retaining the source unit in MetricSpec."""

    def __init__(self, specs: list[MetricSpec], parent=None):
        super().__init__(parent)
        self.specs = list(specs)
        self.setWindowTitle(tr("核对和切换单位", "Review and Switch Units"))
        self.resize(760, min(720, 170 + 42 * max(1, len(specs))))
        layout = QVBoxLayout(self)
        note = QLabel(tr(
            "导入时已自动分配默认单位。可逐项切换；源表原单位仍单独保留。",
            "Default units were assigned during import. You may switch each unit; source units remain stored separately.",
        ))
        note.setWordWrap(True); layout.addWidget(note)
        self.table = QTableWidget(len(specs), 4)
        self.table.setHorizontalHeaderLabels([
            tr("测量语境", "Context"), tr("组分/指标", "Component / Metric"),
            tr("源表单位", "Source Unit"), tr("采用单位（可切换）", "Assigned Unit (editable)"),
        ])
        self.combos: list[QComboBox] = []
        for row, spec in enumerate(specs):
            metric_name, embedded = DEFAULT_UNIT_RESOLVER.split_embedded_unit(spec.raw_metric)
            source_unit = spec.source_unit if spec.source_unit is not None else (embedded or spec.unit)
            for column, value in enumerate((spec.context, metric_name, source_unit or tr("空白", "blank"))):
                item = QTableWidgetItem(str(value)); item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(row, column, item)
            combo = QComboBox(); combo.setEditable(True)
            options = DEFAULT_UNIT_RESOLVER.possible_units(spec.context, metric_name, default_unit=spec.unit)
            combo.addItems(options)
            combo.setCurrentText(spec.unit)
            self.table.setCellWidget(row, 3, combo); self.combos.append(combo)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 105); self.table.setColumnWidth(2, 125); self.table.setColumnWidth(3, 190)
        layout.addWidget(self.table)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); layout.addWidget(buttons)

    def reviewed_specs(self) -> list[MetricSpec]:
        result: list[MetricSpec] = []
        for spec, combo in zip(self.specs, self.combos):
            chosen = combo.currentText().strip()
            changed = chosen.casefold() != spec.unit.casefold()
            result.append(replace(
                spec,
                unit=chosen,
                unit_resolution_method="user_selected" if changed else spec.unit_resolution_method,
                unit_rule_reference="GUI unit review" if changed else spec.unit_rule_reference,
            ))
        return result


class ContributionEditor(QWidget):
    INGREDIENT_HEADERS = ["系统原料编号", "原料名称 *", "原文编号", "原料类型", "加工方式", "产地", "报告基础", "干物质值"]
    ANIMAL_HEADERS = ["系统动物组编号", "试验编号", "动物种类", "品种", "性别", "动物类型", "阶段", "日龄", "周龄", "体重 kg"]
    NUTRITION_HEADERS = ["系统原料编号", "系统动物组编号"]

    @classmethod
    def ingredient_headers(cls) -> list[str]:
        english = ["System Ingredient Key", "Ingredient Name *", "Source ID", "Ingredient Type", "Processing", "Origin", "Reported Basis", "Dry Matter Value"]
        return [tr(chinese, translated) for chinese, translated in zip(cls.INGREDIENT_HEADERS, english)]

    @classmethod
    def animal_headers(cls) -> list[str]:
        english = ["System Animal Group Key", "Trial ID", "Animal Species", "Breed", "Sex", "Animal Type", "Period", "Age (days)", "Age (weeks)", "Body Weight kg"]
        return [tr(chinese, translated) for chinese, translated in zip(cls.ANIMAL_HEADERS, english)]

    @classmethod
    def nutrition_headers(cls) -> list[str]:
        english = ["System Ingredient Key", "System Animal Group Key"]
        return [tr(chinese, translated) for chinese, translated in zip(cls.NUTRITION_HEADERS, english)]

    def __init__(self, database_path: Path, saved_callback=None, parent=None):
        super().__init__(parent)
        self.database_path = database_path
        self.saved_callback = saved_callback
        self.source_path: Path | None = None
        self.source_kind = ""
        self.file_warnings: list[str] = []
        self.composition_metrics: list[MetricSpec] = []
        self.nutrition_metrics: list[MetricSpec] = []
        layout = QVBoxLayout(self)
        note = QLabel(
            "文件是唯一数据入口：选择历史 Excel、标准 Excel 或 JSON，系统先把论文元信息、原料成分和动物营养价值载入草稿。"
            "本页只用于核对和修改；导入不会写数据库，确认后才整批保存。标题必填，DOI 可空。"
        )
        note.setWordWrap(True); layout.addWidget(note)

        metadata = QGroupBox("从文件识别的论文元信息（可核对修改）")
        form = QFormLayout(metadata)
        self.title_edit = QLineEdit(); self.title_edit.setPlaceholderText("必填；中文或英文原题")
        self.doi_edit = QLineEdit(); self.doi_edit.setPlaceholderText("可选；中文期刊无 DOI 时留空")
        self.journal_edit = QLineEdit(); self.year_edit = QLineEdit(); self.species_edit = QLineEdit(); self.country_edit = QLineEdit()
        form.addRow("标题 *", self.title_edit); form.addRow("DOI", self.doi_edit); form.addRow("期刊", self.journal_edit)
        form.addRow("年份", self.year_edit); form.addRow("研究动物", self.species_edit); form.addRow("国家/地区", self.country_edit)
        layout.addWidget(metadata)

        self.entry_tabs = QTabWidget()
        self.entry_tabs.addTab(self._composition_page(), "原料与成分")
        self.entry_tabs.addTab(self._animal_page(), "动物信息")
        self.entry_tabs.addTab(self._nutrition_page(), "营养价值")
        layout.addWidget(self.entry_tabs)

        actions = QHBoxLayout()
        import_file = QPushButton("选择 Excel / JSON"); import_file.clicked.connect(self.import_contribution_file)
        validate_button = QPushButton("检查全部数据"); validate_button.clicked.connect(self.show_validation)
        self.save_button = QPushButton("确认写入工作数据库"); self.save_button.clicked.connect(self.save_batch); self.save_button.setEnabled(False)
        actions.addWidget(import_file); actions.addWidget(validate_button); actions.addWidget(self.save_button); actions.addStretch()
        layout.addLayout(actions)
        self.status = QLabel("尚未选择文件。载入后可在表格中修改、复制、粘贴或删除导入行。"); self.status.setWordWrap(True); self.status.setProperty("_i18n_skip", True)
        layout.addWidget(self.status)
        self.retranslate_ui()

    def retranslate_ui(self):
        retranslate_tree(self)
        self.ingredients_table.setHorizontalHeaderLabels(
            [*self.ingredient_headers(), *(metric.label for metric in self.composition_metrics)]
        )
        self.animals_table.setHorizontalHeaderLabels(self.animal_headers())
        self.nutrition_table.setHorizontalHeaderLabels(
            [*self.nutrition_headers(), *(metric.label for metric in self.nutrition_metrics)]
        )
        if self.source_path is None:
            self.status.setText(tr(
                "尚未选择文件。载入后可在表格中修改、复制、粘贴或删除导入行。",
                "No file selected. After loading, rows can be edited, copied, pasted, or deleted in the grids.",
            ))
        elif self.save_button.isEnabled():
            self.status.setText(tr(
                f"已载入 {self.source_path.name}，尚未写入数据库；请检查后确认保存。",
                f"Loaded {self.source_path.name}; the database has not been changed. Validate and confirm before saving.",
            ))
        else:
            self.status.setText(tr("本批数据已经写入工作数据库。", "This batch has been saved to the working database."))

    def _composition_page(self):
        page = QWidget(); layout = QVBoxLayout(page); toolbar = QHBoxLayout()
        remove = QPushButton("删除选中的导入行"); remove.clicked.connect(lambda: self.ingredients_table.remove_selected_rows())
        edit_units = QPushButton("核对/切换单位"); edit_units.clicked.connect(lambda: self.edit_metric_units(False))
        toolbar.addWidget(remove); toolbar.addWidget(edit_units)
        toolbar.addWidget(QLabel("指标列由文件自动识别；系统编号用于建立关系，无需手工创建。"))
        toolbar.addStretch(); layout.addLayout(toolbar)
        self.ingredients_table = PasteTable(self.ingredient_headers(), 1)
        self.ingredients_table.setColumnWidth(0, 95); self.ingredients_table.setColumnWidth(1, 190)
        layout.addWidget(self.ingredients_table)
        return page

    def _animal_page(self):
        page = QWidget(); layout = QVBoxLayout(page); toolbar = QHBoxLayout()
        remove = QPushButton("删除选中的导入行"); remove.clicked.connect(lambda: self.animals_table.remove_selected_rows())
        toolbar.addWidget(remove); toolbar.addWidget(QLabel("动物信息来自文件；营养价值保存时自动关联原料和动物组。")); toolbar.addStretch(); layout.addLayout(toolbar)
        self.animals_table = PasteTable(self.animal_headers(), 1)
        self.animals_table.setColumnWidth(0, 110); self.animals_table.setColumnWidth(2, 110); self.animals_table.setColumnWidth(3, 180)
        layout.addWidget(self.animals_table)
        return page

    def _nutrition_page(self):
        page = QWidget(); layout = QVBoxLayout(page); toolbar = QHBoxLayout()
        remove = QPushButton("删除选中行"); remove.clicked.connect(lambda: self.nutrition_table.remove_selected_rows())
        edit_units = QPushButton("核对/切换单位"); edit_units.clicked.connect(lambda: self.edit_metric_units(True))
        toolbar.addWidget(remove); toolbar.addWidget(edit_units)
        toolbar.addWidget(QLabel("测量语境、指标和原料—动物关系均从文件识别。"))
        toolbar.addStretch(); layout.addLayout(toolbar)
        self.nutrition_table = PasteTable(self.nutrition_headers(), 1)
        self.nutrition_table.setColumnWidth(0, 110); self.nutrition_table.setColumnWidth(1, 120)
        layout.addWidget(self.nutrition_table)
        return page

    def _seed_ingredient_keys(self, start: int):
        for row in range(start, self.ingredients_table.rowCount()):
            if not _text(self.ingredients_table, row, 0): self.ingredients_table.setItem(row, 0, QTableWidgetItem(f"I{row + 1:03d}"))

    def _seed_animal_keys(self, start: int):
        for row in range(start, self.animals_table.rowCount()):
            if not _text(self.animals_table, row, 0): self.animals_table.setItem(row, 0, QTableWidgetItem(f"A{row + 1:03d}"))

    def _add_ingredient_rows(self, count: int):
        start = self.ingredients_table.rowCount(); self.ingredients_table.add_rows(count); self._seed_ingredient_keys(start)

    def _add_animal_rows(self, count: int):
        start = self.animals_table.rowCount(); self.animals_table.add_rows(count); self._seed_animal_keys(start)

    def _append_metric_columns(self, table: PasteTable, target: list[MetricSpec], specs: list[MetricSpec]):
        existing = {(metric.context.casefold(), metric.raw_metric.casefold()) for metric in target}
        for metric in specs:
            key = (metric.context.casefold(), metric.raw_metric.casefold())
            if key in existing: continue
            existing.add(key); target.append(metric)
            column = table.columnCount(); table.insertColumn(column)
            item = QTableWidgetItem(metric.label); item.setToolTip(f"context={metric.context}; unit={metric.unit or 'blank'}; basis={metric.basis or 'blank'}")
            table.setHorizontalHeaderItem(column, item); table.setColumnWidth(column, 130)

    @staticmethod
    def _write_row(table: QTableWidget, row: int, values: list[str]):
        for column, value in enumerate(values):
            table.setItem(row, column, QTableWidgetItem("" if value is None else str(value)))

    def populate_batch(self, batch: ContributionBatch):
        """Replace the visible entry grids with a parsed contribution, without writing SQLite."""
        self.title_edit.setText(batch.title); self.doi_edit.setText(batch.doi)
        self.journal_edit.setText(batch.journal); self.year_edit.setText(batch.publication_year)
        self.species_edit.setText(batch.species); self.country_edit.setText(batch.country)

        self.ingredients_table.clearContents(); self.ingredients_table.setColumnCount(len(self.INGREDIENT_HEADERS))
        self.ingredients_table.setHorizontalHeaderLabels(self.ingredient_headers())
        self.ingredients_table.setRowCount(max(1, len(batch.ingredients)))
        self.composition_metrics = []
        self._append_metric_columns(self.ingredients_table, self.composition_metrics, list(batch.composition_metrics))
        for row, item in enumerate(batch.ingredients):
            self._write_row(self.ingredients_table, row, [
                item.ingredient_key, item.raw_name, item.source_ingredient_no, item.feed_type,
                item.processing_technic, item.place_of_origin, item.reported_basis,
                item.dry_matter_value, *item.values,
            ])

        self.animals_table.clearContents(); self.animals_table.setColumnCount(len(self.ANIMAL_HEADERS))
        self.animals_table.setHorizontalHeaderLabels(self.animal_headers())
        self.animals_table.setRowCount(max(1, len(batch.animals)))
        for row, item in enumerate(batch.animals):
            self._write_row(self.animals_table, row, [
                item.animal_group_key, item.trial_no, item.species, item.breed, item.sex,
                item.animal_type, item.period, item.age_days, item.age_weeks, item.body_weight_kg,
            ])

        self.nutrition_table.clearContents(); self.nutrition_table.setColumnCount(len(self.NUTRITION_HEADERS))
        self.nutrition_table.setHorizontalHeaderLabels(self.nutrition_headers())
        self.nutrition_table.setRowCount(max(1, len(batch.nutrition_rows)))
        self.nutrition_metrics = []
        self._append_metric_columns(self.nutrition_table, self.nutrition_metrics, list(batch.nutrition_metrics))
        for row, item in enumerate(batch.nutrition_rows):
            self._write_row(self.nutrition_table, row, [item.ingredient_key, item.animal_group_key, *item.values])
        self.entry_tabs.setCurrentIndex(0)

    def prepare_batch_units(self, batch: ContributionBatch) -> ContributionBatch:
        """Assign editable effective units without losing source-unit text."""
        with validation_dictionary_connection(self.database_path) as conn:
            def prepare(specs: list[MetricSpec], rows) -> list[MetricSpec]:
                prepared: list[MetricSpec] = []
                for index, spec in enumerate(specs):
                    if spec.source_unit is not None:
                        prepared.append(spec); continue
                    parsed_metric, _embedded = DEFAULT_UNIT_RESOLVER.split_embedded_unit(spec.raw_metric)
                    found = lookup_dictionary(conn, "metric", parsed_metric, spec.context)
                    canonical_metric = found[0] if found else parsed_metric
                    catalog = conn.execute(
                        """SELECT default_unit FROM metric_catalog
                           WHERE CASEFOLD(context)=CASEFOLD(?) AND CASEFOLD(canonical_metric)=CASEFOLD(?) AND active=1
                           ORDER BY sort_order,metric_catalog_id LIMIT 1""",
                        (spec.context, canonical_metric),
                    ).fetchone()
                    sample_value = next(
                        (row.values[index] for row in rows if index < len(row.values) and str(row.values[index]).strip()),
                        None,
                    )
                    resolution = DEFAULT_UNIT_RESOLVER.resolve(
                        spec.raw_metric, spec.context, canonical_metric, spec.unit,
                        catalog[0] if catalog else "", sample_value,
                    )
                    prepared.append(replace(
                        spec,
                        unit=resolution.canonical_unit,
                        source_unit=resolution.raw_unit,
                        unit_resolution_method=resolution.resolution_method,
                        unit_rule_reference=resolution.rule_reference,
                    ))
                return prepared
            return replace(
                batch,
                composition_metrics=prepare(list(batch.composition_metrics), batch.ingredients),
                nutrition_metrics=prepare(list(batch.nutrition_metrics), batch.nutrition_rows),
            )

    def edit_metric_units(self, nutrition: bool):
        specs = self.nutrition_metrics if nutrition else self.composition_metrics
        if not specs:
            QMessageBox.information(self, tr("没有指标", "No Metrics"), tr("请先导入包含指标的数据文件。", "Import a data file containing metrics first."))
            return
        dialog = UnitReviewDialog(specs, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        reviewed = dialog.reviewed_specs()
        if nutrition:
            self.nutrition_metrics = reviewed
            self.nutrition_table.setHorizontalHeaderLabels([*self.nutrition_headers(), *(item.label for item in reviewed)])
        else:
            self.composition_metrics = reviewed
            self.ingredients_table.setHorizontalHeaderLabels([*self.ingredient_headers(), *(item.label for item in reviewed)])

    def _has_entered_data(self) -> bool:
        if self.source_path is not None:
            return True
        if any(field.text().strip() for field in (
            self.title_edit, self.doi_edit, self.journal_edit, self.year_edit, self.species_edit, self.country_edit,
        )):
            return True
        return any(_text(self.ingredients_table, row, 1) for row in range(self.ingredients_table.rowCount()))

    def import_contribution_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, tr("选择完整数据文件", "Select Complete Data File"), "", "Supported (*.xlsx *.xlsm *.json);;Excel (*.xlsx *.xlsm);;JSON (*.json)"
        )
        if not path:
            return
        if self._has_entered_data():
            answer = QMessageBox.question(
                self, tr("替换当前导入草稿？", "Replace Current Review Draft?"),
                tr("重新载入文件会替换当前尚未写入数据库的核对内容。继续吗？", "Loading another file will replace the current unsaved review draft. Continue?"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        try:
            loaded = load_contribution_file(path)
            batch = self.prepare_batch_units(loaded.batch)
            check = validate_contribution_batch(self.database_path, batch)
        except Exception as exc:
            QMessageBox.critical(self, tr("文件无法载入", "File Could Not Be Loaded"), str(exc)); return
        self.source_path = loaded.source_path
        self.source_kind = loaded.source_kind
        self.file_warnings = list(loaded.warnings)
        self.save_button.setEnabled(True)
        self.populate_batch(batch)
        text = self._check_text(check)
        composition_count = sum(bool(str(value).strip()) for row in batch.ingredients for value in row.values)
        nutrition_count = sum(bool(str(value).strip()) for row in batch.nutrition_rows for value in row.values)
        counts = tr(
            f"原料 {len(batch.ingredients)}；成分值 {composition_count}；动物组 {len(batch.animals)}；营养价值 {nutrition_count}。",
            f"Ingredients {len(batch.ingredients)}; composition values {composition_count}; animal groups {len(batch.animals)}; nutritional values {nutrition_count}.",
        )
        warnings = (tr(" 文件解析提示：", " File parsing notes: ") + "；".join(self.file_warnings)) if self.file_warnings else ""
        self.status.setText(tr(
            f"已载入 {Path(path).name}（{loaded.source_kind}），尚未写入数据库。{counts}{warnings}",
            f"Loaded {Path(path).name} ({loaded.source_kind}); database not yet changed. {counts}{warnings}",
        ))
        message = tr("文件已进入核对表，尚未写入数据库。", "The file is in the review grids; the database has not been changed.") + f"\n\n{counts}\n\n{text}"
        if self.file_warnings:
            message += tr("\n\n文件解析提示：\n- ", "\n\nFile parsing notes:\n- ") + "\n- ".join(self.file_warnings)
        (QMessageBox.warning if check.errors or check.warnings or self.file_warnings else QMessageBox.information)(self, tr("文件已载入", "File Loaded"), message)

    def import_ai_json(self):
        """Backward-compatible entry point used by older integrations."""
        self.import_contribution_file()

    def add_composition_metrics(self):
        dialog = MetricDialog(self.database_path, nutrition=False, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._append_metric_columns(self.ingredients_table, self.composition_metrics, dialog.selected_specs())

    def add_nutrition_metrics(self, amino_acids: bool = False):
        dialog = MetricDialog(self.database_path, nutrition=True, amino_acids=amino_acids, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._append_metric_columns(self.nutrition_table, self.nutrition_metrics, dialog.selected_specs())

    def refresh_reference_choices(self):
        self.ingredient_choice.clear(); self.animal_choice.clear()
        for row in range(self.ingredients_table.rowCount()):
            key = _text(self.ingredients_table, row, 0); name = _text(self.ingredients_table, row, 1)
            if key and name: self.ingredient_choice.addItem(f"{key} | {name}", key)
        for row in range(self.animals_table.rowCount()):
            key = _text(self.animals_table, row, 0); species = _text(self.animals_table, row, 2); breed = _text(self.animals_table, row, 3)
            if key and any((species, breed, _text(self.animals_table, row, 1))):
                self.animal_choice.addItem(" | ".join(value for value in (key, species, breed) if value), key)

    def add_nutrition_pair(self):
        if self.ingredient_choice.count() == 0 or self.animal_choice.count() == 0:
            self.refresh_reference_choices()
        ingredient = self.ingredient_choice.currentData(); animal = self.animal_choice.currentData()
        if not ingredient or not animal:
            QMessageBox.warning(self, tr("缺少原料或动物组", "Missing Ingredient or Animal Group"), tr("先在前两个表中填写原料和动物组，再刷新选择。", "Fill the ingredient and animal-group grids before refreshing the selection."))
            return
        for row in range(self.nutrition_table.rowCount()):
            if _text(self.nutrition_table, row, 0) == ingredient and _text(self.nutrition_table, row, 1) == animal:
                self.nutrition_table.setCurrentCell(row, 2 if self.nutrition_table.columnCount() > 2 else 1); return
        row = next((r for r in range(self.nutrition_table.rowCount()) if not _text(self.nutrition_table, r, 0) and not _text(self.nutrition_table, r, 1)), -1)
        if row < 0: row = self.nutrition_table.rowCount(); self.nutrition_table.insertRow(row)
        self.nutrition_table.setItem(row, 0, QTableWidgetItem(str(ingredient)))
        self.nutrition_table.setItem(row, 1, QTableWidgetItem(str(animal)))

    def collect_batch(self) -> ContributionBatch:
        ingredients = []
        for row in range(self.ingredients_table.rowCount()):
            ingredients.append(IngredientGridRow(
                ingredient_key=_text(self.ingredients_table, row, 0), raw_name=_text(self.ingredients_table, row, 1),
                source_ingredient_no=_text(self.ingredients_table, row, 2), feed_type=_text(self.ingredients_table, row, 3),
                processing_technic=_text(self.ingredients_table, row, 4), place_of_origin=_text(self.ingredients_table, row, 5),
                reported_basis=_text(self.ingredients_table, row, 6), dry_matter_value=_text(self.ingredients_table, row, 7),
                values=[_text(self.ingredients_table, row, 8 + index) for index in range(len(self.composition_metrics))],
            ))
        animals = []
        for row in range(self.animals_table.rowCount()):
            values = [_text(self.animals_table, row, col) for col in range(10)]
            animals.append(AnimalGridRow(*values))
        nutrition_rows = []
        for row in range(self.nutrition_table.rowCount()):
            nutrition_rows.append(NutritionGridRow(
                _text(self.nutrition_table, row, 0), _text(self.nutrition_table, row, 1),
                [_text(self.nutrition_table, row, 2 + index) for index in range(len(self.nutrition_metrics))],
            ))
        return ContributionBatch(
            title=self.title_edit.text(), doi=self.doi_edit.text(), journal=self.journal_edit.text(),
            publication_year=self.year_edit.text(), species=self.species_edit.text(), country=self.country_edit.text(),
            composition_metrics=list(self.composition_metrics), ingredients=ingredients, animals=animals,
            nutrition_metrics=list(self.nutrition_metrics), nutrition_rows=nutrition_rows,
        )

    @staticmethod
    def _check_text(check):
        parts = []
        if check.errors: parts.append(tr("错误：\n- ", "Errors:\n- ") + "\n- ".join(check.errors))
        if check.warnings: parts.append(tr("提醒：\n- ", "Warnings:\n- ") + "\n- ".join(check.warnings))
        if check.mappings: parts.append(tr("名称对应：\n- ", "Mappings:\n- ") + "\n- ".join(check.mappings))
        if check.new_terms:
            parts.append(tr("未收录名称：\n- ", "Unrecognized terms:\n- ") + "\n- ".join(
                f"{category}{f'/{context}' if context else ''}: {raw}"
                for category, context, raw in check.new_terms
            ))
        return "\n\n".join(parts) or tr("检查通过。允许保留的空值不会阻止保存。", "Validation passed. Allowed blank values do not block saving.")

    def show_validation(self):
        if self.source_path is None:
            QMessageBox.warning(self, tr("尚未导入", "No File Loaded"), tr("请先选择一个 Excel 或 JSON 文件。", "Select an Excel or JSON file first."))
            return
        check = validate_contribution_batch(self.database_path, self.collect_batch())
        text = self._check_text(check); self.status.setText(text.replace("\n", " "))
        (QMessageBox.warning if check.errors or check.warnings else QMessageBox.information)(self, tr("导入草稿检查", "Import Draft Validation"), text)

    def save_batch(self):
        if self.source_path is None:
            QMessageBox.warning(self, tr("尚未导入", "No File Loaded"), tr("数据必须来自 Excel 或 JSON 文件，请先选择文件。", "Data must come from an Excel or JSON file; select a file first."))
            return
        batch = self.collect_batch(); check = validate_contribution_batch(self.database_path, batch)
        if check.errors:
            QMessageBox.critical(self, tr("不能保存", "Cannot Save"), self._check_text(check)); return
        if check.new_terms or check.warnings or self.file_warnings:
            answer = QMessageBox.question(
                self, tr("确认带提示保存", "Save with Warnings"),
                self._check_text(check)
                + (tr("\n\n文件解析提示：\n- ", "\n\nFile parsing notes:\n- ") + "\n- ".join(self.file_warnings) if self.file_warnings else "")
                + tr("\n\n未收录名称将以 candidate 保存，稍后由词典管理员统一处理；不会自动批准。仍然写入数据库吗？", "\n\nUnrecognized terms will be saved as candidates for later dictionary review and will not be auto-approved. Continue saving?"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes: return
        try:
            result = save_contribution_batch(
                self.database_path,
                batch,
                approve_new_terms=False,
                source_prefix=f"file-import/{self.source_path.name}",
            )
        except Exception as exc:
            QMessageBox.critical(self, tr("保存失败", "Save Failed"), str(exc)); return
        self.status.setText(tr(
            f"已保存：原料 {result.ingredient_samples}，动物组 {result.animal_groups}，成分 {result.component_observations}，营养价值 {result.nutrition_observations}，重复复用 {result.deduplicated_observations}。",
            f"Saved: ingredients {result.ingredient_samples}, animal groups {result.animal_groups}, composition values {result.component_observations}, nutritional values {result.nutrition_observations}, duplicates reused {result.deduplicated_observations}.",
        ))
        self.save_button.setEnabled(False)
        if self.saved_callback: self.saved_callback()
