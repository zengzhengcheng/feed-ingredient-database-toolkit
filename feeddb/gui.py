from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt, QTimer
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton, QTabWidget,
    QTableView, QTextBrowser, QVBoxLayout, QWidget,
)

from .database import apply_dictionary_mapping, connect, initialize
from .contribution_widget import ContributionEditor
from .metric_catalog_widget import MetricCatalogManager
from .term_review_widget import TermReviewWidget
from .query import search_database
from .user_query import execute_readonly_query, export_readonly_query, write_query_result
from .i18n import current_language, retranslate_tree, set_language, status_database, tr, window_title


DATABASE_TABLES = [
    "studies", "trials", "ingredient_samples", "animal_groups", "ingredient_evaluations",
    "component_observations", "nutrition_observations", "source_provenance",
    "dictionaries", "metric_catalog", "import_runs", "validation_issues",
]


class RowsModel(QAbstractTableModel):
    def __init__(self, columns=None, rows=None):
        super().__init__()
        self.columns = columns or []
        self.rows = rows or []

    def rowCount(self, parent=QModelIndex()):
        return len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return len(self.columns)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and index.isValid():
            value = self.rows[index.row()][index.column()]
            return "" if value is None else str(value)
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole:
            return self.columns[section] if orientation == Qt.Orientation.Horizontal else section + 1
        return None

    def replace(self, columns, rows):
        self.beginResetModel()
        self.columns = columns
        self.rows = rows
        self.endResetModel()


class MainWindow(QMainWindow):
    def __init__(self, database_path: str | Path | None = None, read_only: bool = False):
        super().__init__()
        self.resize(1280, 820)
        self.db_path = Path(database_path) if database_path else Path(__file__).resolve().parents[1] / "database" / "contribution_workspace.sqlite"
        self.read_only = bool(read_only)
        if self.read_only:
            if not self.db_path.is_file():
                raise FileNotFoundError(f"只读数据库文件不存在：{self.db_path}")
        else:
            initialize(self.db_path)
        self.setWindowTitle(window_title(self.db_path.name))
        self.statusBar().showMessage(status_database(self.db_path.resolve(), self.read_only))
        self.language_label = QLabel("界面语言")
        self.language_combo = QComboBox()
        self.language_combo.addItem("中文", "zh"); self.language_combo.addItem("English", "en")
        self.language_combo.setCurrentIndex(max(0, self.language_combo.findData(current_language())))
        self.language_combo.currentIndexChanged.connect(self._change_language)
        self.guide_button = QPushButton("使用指南")
        self.guide_button.clicked.connect(self._show_user_guide)
        self.statusBar().addPermanentWidget(self.guide_button)
        self.statusBar().addPermanentWidget(self.language_label)
        self.statusBar().addPermanentWidget(self.language_combo)
        tabs = QTabWidget()
        self.contribution = ContributionEditor(self.db_path, self._refresh_all)
        self.contribution.setEnabled(not self.read_only)
        tabs.addTab(self.contribution, "导入与核对")
        tabs.addTab(self._query_tab(), "查询数据")
        management = QTabWidget()
        self.metric_catalog_manager = MetricCatalogManager(self.db_path, self._refresh_all)
        self.term_review_manager = TermReviewWidget(self.db_path, self._refresh_all)
        management.addTab(self.term_review_manager, "待核对新词")
        management.addTab(self._dictionary_tab(), "名称词典")
        management.addTab(self.metric_catalog_manager, "指标分类")
        management.setEnabled(not self.read_only)
        tabs.addTab(management, "词典与指标")
        tabs.addTab(self._database_tab(), "查看数据库")
        self.setCentralWidget(tabs)
        self.refresh_query()
        self.refresh_database()
        self.refresh_dictionary()
        self.retranslate_ui(refresh_dynamic=False)

    def _change_language(self, index: int):
        set_language(self.language_combo.itemData(index) or "zh")
        self.retranslate_ui()

    def _show_user_guide(self):
        guide_path = Path(__file__).resolve().parents[1] / "docs" / "USER_GUIDE.md"
        try:
            content = guide_path.read_text(encoding="utf-8-sig")
        except OSError as exc:
            QMessageBox.critical(self, tr("无法打开使用指南", "Cannot Open User Guide"), str(exc))
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(tr("使用指南", "User Guide"))
        dialog.resize(900, 700)
        layout = QVBoxLayout(dialog)
        browser = QTextBrowser(dialog)
        browser.setMarkdown(content)
        layout.addWidget(browser)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def retranslate_ui(self, refresh_dynamic: bool = True):
        self.setWindowTitle(window_title(self.db_path.name))
        self.statusBar().showMessage(status_database(self.db_path.resolve(), self.read_only))
        retranslate_tree(self)
        self.language_label.setText(tr("界面语言", "Interface Language"))
        for child in (self.contribution, self.term_review_manager, self.metric_catalog_manager):
            if hasattr(child, "retranslate_ui"):
                child.retranslate_ui()
        if refresh_dynamic:
            self._finish_query(self.query_edit.text().strip())
            self.refresh_dictionary()
            self.rule_status.setText(tr(
                f"规则结果保留在表格中；当前显示 {len(self.rule_model.rows)} 行。" if self.rule_model.rows else "尚未运行规则。",
                f"Rule results remain in the table; {len(self.rule_model.rows)} rows are displayed." if self.rule_model.rows else "No rule has been run.",
            ))

    def _query_tab(self):
        widget = QWidget(); layout = QVBoxLayout(widget)
        modes = QTabWidget()
        modes.addTab(self._fuzzy_query_tab(), "关键词查询")
        modes.addTab(self._rule_query_tab(), "自定义规则（只读 SQL）")
        layout.addWidget(modes)
        return widget

    def _fuzzy_query_tab(self):
        widget = QWidget(); layout = QVBoxLayout(widget); row = QHBoxLayout()
        self.query_edit = QLineEdit(); self.query_edit.setPlaceholderText("输入多个关键词，例如：玉米 SID 仔猪；同时搜索标题、DOI、原料、指标和动物")
        self.query_edit.returnPressed.connect(self.refresh_query)
        button = QPushButton("查询"); button.clicked.connect(self.refresh_query)
        clear = QPushButton("清空"); clear.clicked.connect(lambda: (self.query_edit.clear(), self.refresh_query()))
        export = QPushButton("导出当前结果"); export.clicked.connect(self.export_fuzzy_results)
        row.addWidget(self.query_edit); row.addWidget(button); row.addWidget(clear); row.addWidget(export); layout.addLayout(row)
        self.query_status = QLabel(); self.query_status.setWordWrap(True); self.query_status.setProperty("_i18n_skip", True); layout.addWidget(self.query_status)
        self.query_model = RowsModel(); self.query_view = QTableView(); self.query_view.setModel(self.query_model); self.query_view.setSortingEnabled(False)
        self.query_view.setAlternatingRowColors(True); layout.addWidget(self.query_view)
        return widget

    def _rule_query_tab(self):
        widget = QWidget(); layout = QVBoxLayout(widget)
        note = QLabel(
            "高级规则只允许执行一条 SELECT 或 WITH 查询，并通过只读连接访问数据库。"
            "可从 .sql 文件载入规则；导出支持 CSV、JSON 和 Excel。"
        )
        note.setWordWrap(True); layout.addWidget(note)
        self.rule_sql = QPlainTextEdit()
        self.rule_sql.setPlaceholderText("输入 SELECT 或 WITH 查询")
        self.rule_sql.setPlainText("SELECT * FROM observations LIMIT 100")
        layout.addWidget(self.rule_sql)
        actions = QHBoxLayout()
        run = QPushButton("运行只读规则"); run.clicked.connect(self.run_rule_query)
        load = QPushButton("载入 .sql 文件"); load.clicked.connect(self.load_rule_query)
        export = QPushButton("导出规则结果"); export.clicked.connect(self.export_rule_query)
        actions.addWidget(run); actions.addWidget(load); actions.addWidget(export); actions.addStretch()
        layout.addLayout(actions)
        self.rule_status = QLabel("尚未运行规则。"); self.rule_status.setProperty("_i18n_skip", True)
        self.rule_status.setWordWrap(True); layout.addWidget(self.rule_status)
        self.rule_model = RowsModel(); self.rule_view = QTableView(); self.rule_view.setModel(self.rule_model)
        self.rule_view.setAlternatingRowColors(True); layout.addWidget(self.rule_view)
        return widget

    def _database_tab(self):
        widget = QWidget(); layout = QVBoxLayout(widget); row = QHBoxLayout()
        self.table_name = QComboBox(); self.table_name.addItems(DATABASE_TABLES)
        button = QPushButton("刷新表"); button.clicked.connect(self.refresh_database)
        row.addWidget(QLabel("数据表")); row.addWidget(self.table_name); row.addWidget(button); row.addStretch()
        layout.addLayout(row)
        self.database_model = RowsModel(); view = QTableView(); view.setModel(self.database_model); view.setAlternatingRowColors(True)
        layout.addWidget(view)
        return widget

    def _dictionary_tab(self):
        widget = QWidget(); layout = QVBoxLayout(widget)
        explanation = QLabel(
            "名称词典就是“别名对照表”：raw term 是原 Excel 中的写法，canonical term 是统一名称。"
            "新词先作为 candidate；确认无误后改成 approved。组分名称全局共用，只需核对一次。"
        )
        explanation.setWordWrap(True); layout.addWidget(explanation)
        form = QFormLayout()
        self.category = QComboBox(); self.category.addItems(["ingredient", "metric", "unit", "basis", "species"])
        self.raw_term = QLineEdit(); self.canonical_term = QLineEdit()
        self.dict_status = QComboBox(); self.dict_status.addItems(["approved", "candidate", "deprecated"])
        save = QPushButton("保存对应关系"); save.clicked.connect(self.save_dictionary)
        for label, field in (
            ("类别", self.category), ("原写法 / 别名", self.raw_term),
            ("统一名称", self.canonical_term), ("状态", self.dict_status), ("", save),
        ):
            form.addRow(label, field)
        layout.addLayout(form)
        self.dict_model = RowsModel(); view = QTableView(); view.setModel(self.dict_model); view.setAlternatingRowColors(True)
        layout.addWidget(view)
        return widget

    def refresh_query(self):
        query = self.query_edit.text().strip() if hasattr(self, "query_edit") else ""
        self.query_status.setText(tr(
            f"查询中…… 数据库：{self.db_path.resolve()}",
            f"Searching… Database: {self.db_path.resolve()}",
        ))
        QTimer.singleShot(0, lambda: self._finish_query(query))

    def _finish_query(self, query: str):
        try:
            columns, rows = search_database(self.db_path, query)
        except Exception as exc:
            if hasattr(self, "query_status"):
                self.query_status.setText(tr(f"查询失败：{exc}", f"Search failed: {exc}"))
            return
        labels = {
            "doi": "DOI", "title": tr("标题", "Title"), "raw_ingredient": tr("原料原名", "Raw Ingredient"),
            "ingredient": tr("统一原料名", "Canonical Ingredient"), "data_type": tr("数据类型", "Data Type"),
            "context": tr("语境", "Context"), "raw_metric": tr("指标原名", "Raw Metric"),
            "metric": tr("统一指标名", "Canonical Metric"), "value_num": tr("数值", "Numeric Value"),
            "value_text": tr("文本值", "Text Value"), "unit": tr("单位", "Unit"), "basis": tr("基础", "Basis"),
            "trial_no": tr("试验编号", "Trial ID"), "animal_species": tr("动物", "Animal"),
            "breed": tr("品种", "Breed"), "data_status": tr("数据状态", "Data Status"),
        }
        self.query_model.replace([labels.get(column, column) for column in columns], [tuple(row) for row in rows])
        if hasattr(self, "query_status"):
            query_note = tr(f"关键词：{query}", f"Keywords: {query}") if query else tr("未限定关键词", "No keyword filter")
            limit_note = tr("（达到显示上限 1000 条，请增加关键词缩小范围）", " (1,000-row display limit reached; add keywords to narrow the result)") if len(rows) >= 1000 else ""
            self.query_status.setText(tr(
                f"查询完成。{query_note}；找到并显示 {len(rows)} 条记录{limit_note}。数据库：{self.db_path.resolve()}",
                f"Search complete. {query_note}; {len(rows)} rows displayed{limit_note}. Database: {self.db_path.resolve()}",
            ))
        if rows and hasattr(self, "query_view"):
            self.query_view.resizeColumnsToContents()

    def export_fuzzy_results(self):
        if not self.query_model.rows:
            QMessageBox.information(self, tr("没有结果", "No Results"), tr("请先查询，再导出当前结果。", "Run a search before exporting results."))
            return
        output, _ = QFileDialog.getSaveFileName(
            self, tr("导出关键词查询结果", "Export Keyword Search Results"), "feed_query.csv",
            "CSV (*.csv);;JSON (*.json);;Excel (*.xlsx)",
        )
        if not output:
            return
        try:
            write_query_result(output, self.query_model.columns, self.query_model.rows)
            self.query_status.setText(self.query_status.text() + tr(f" 已导出：{output}", f" Exported: {output}"))
        except Exception as exc:
            QMessageBox.critical(self, tr("导出失败", "Export Failed"), str(exc))

    def run_rule_query(self):
        try:
            result = execute_readonly_query(self.db_path, self.rule_sql.toPlainText(), max_rows=10_000)
        except Exception as exc:
            self.rule_status.setText(tr(f"规则执行失败：{exc}", f"Rule failed: {exc}"))
            return
        self.rule_model.replace(list(result.columns), result.rows)
        limit_note = tr("；达到界面显示上限 10,000 行", "; 10,000-row display limit reached") if result.truncated else ""
        self.rule_status.setText(tr(
            f"只读规则执行成功；显示 {len(result.rows)} 行{limit_note}。数据库：{self.db_path}",
            f"Read-only rule completed; {len(result.rows)} rows displayed{limit_note}. Database: {self.db_path}",
        ))
        if result.rows:
            self.rule_view.resizeColumnsToContents()

    def load_rule_query(self):
        source, _ = QFileDialog.getOpenFileName(self, tr("载入只读 SQL 规则", "Load Read-only SQL Rule"), "", "SQL rule (*.sql);;Text (*.txt)")
        if not source:
            return
        try:
            self.rule_sql.setPlainText(Path(source).read_text(encoding="utf-8-sig"))
            self.rule_status.setText(tr(f"已载入规则：{source}；请点击“运行只读规则”。", f"Rule loaded: {source}; click Run Read-only Rule."))
        except OSError as exc:
            QMessageBox.critical(self, tr("载入失败", "Load Failed"), str(exc))

    def export_rule_query(self):
        output, _ = QFileDialog.getSaveFileName(
            self, tr("导出规则结果", "Export Rule Results"), "feed_rule_result.csv",
            "CSV (*.csv);;JSON (*.json);;Excel (*.xlsx)",
        )
        if not output:
            return
        try:
            report = export_readonly_query(self.db_path, self.rule_sql.toPlainText(), output)
            note = tr("；已达到导出上限", "; export limit reached") if report["truncated"] else ""
            self.rule_status.setText(tr(
                f"已导出 {report['rows']} 行到 {report['output']}{note}",
                f"Exported {report['rows']} rows to {report['output']}{note}",
            ))
        except Exception as exc:
            QMessageBox.critical(self, tr("导出失败", "Export Failed"), str(exc))

    def _refresh_all(self):
        self.refresh_query(); self.refresh_database(); self.refresh_dictionary()

    def refresh_database(self):
        if not hasattr(self, "table_name"):
            return
        table = self.table_name.currentText()
        if table not in DATABASE_TABLES:
            return
        with connect(self.db_path) as conn:
            cursor = conn.execute(f"SELECT * FROM {table} LIMIT 1000")
            rows = cursor.fetchall(); columns = [item[0] for item in cursor.description]
        self.database_model.replace(columns, [tuple(row) for row in rows])

    def refresh_dictionary(self):
        with connect(self.db_path) as conn:
            rows = conn.execute(
                """SELECT category,raw_term,canonical_term,status,max(note),max(updated_at)
                   FROM dictionaries
                   GROUP BY category,CASEFOLD(raw_term),CASEFOLD(canonical_term),status
                   ORDER BY category,raw_term LIMIT 5000"""
            ).fetchall()
        self.dict_model.replace(
            [tr("类别", "Category"), tr("原写法", "Raw Term"),
             tr("统一名称", "Canonical Name"), tr("状态", "Status"), tr("备注", "Note"), tr("更新时间", "Updated")],
            [tuple(row) for row in rows],
        )

    def save_dictionary(self):
        raw = self.raw_term.text().strip(); canonical = self.canonical_term.text().strip()
        if not raw or not canonical:
            QMessageBox.warning(self, tr("缺少名称", "Missing Name"), tr("原写法和统一名称都必须填写。", "Both raw and canonical names are required."))
            return
        with connect(self.db_path) as conn:
            apply_dictionary_mapping(
                conn, self.category.currentText(), raw, canonical,
                self.dict_status.currentText(), "Edited in PyQt6 GUI", "",
            )
        self.refresh_dictionary(); self.refresh_query(); self.raw_term.clear(); self.canonical_term.clear()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Feed ingredient database desktop tool")
    parser.add_argument("--database", help="editable working SQLite database path")
    parser.add_argument("--read-only", action="store_true", help="open an existing database without allowing edits")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    app = QApplication([sys.argv[0]])
    window = MainWindow(args.database, read_only=args.read_only); window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
