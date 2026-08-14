"""Run the release gate with an author-friendly summary.

A valid candidate may still have pending publication decisions.  Those pending
items are reported without turning the command itself into a process failure.
Only an inability to execute the check returns a non-zero exit code.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from feeddb.release_check import check_release_package


CHECK_LABELS = {
    "rights_review": "来源工作簿权利审查",
    "data_license": "数据许可证确认",
    "terminology_candidates": "候选名称人工复核",
}


def _label(code: str) -> str:
    return CHECK_LABELS.get(code, code)


def _message(item: dict[str, object]) -> str:
    code = str(item.get("code", ""))
    message = str(item.get("message", ""))
    if code == "rights_review" and "=" in message:
        pending = message.split("=", 1)[1].split(";", 1)[0]
        return f"仍有 {pending} 个来源工作簿等待作者确认"
    if code == "data_license":
        return "需要作者选定并确认公开数据许可证"
    if code == "terminology_candidates" and "=" in message:
        count = message.split("=", 1)[1].split(";", 1)[0]
        return f"{count} 条观察至少含一项候选名称映射；这不表示 Excel 数值错误"
    return message


def main() -> int:
    parser = argparse.ArgumentParser(description="Check a release package and print an author-friendly summary.")
    parser.add_argument("release_directory", type=Path)
    args = parser.parse_args()

    try:
        report = check_release_package(args.release_directory, write_report=True)
    except Exception as exc:  # The batch wrapper must distinguish execution failures from pending gates.
        print("\n[程序运行错误] 数据发布包检查未能完成。")
        print(f"原因：{exc}")
        return 2

    checks = list(report.get("checks", []))
    passed = [item for item in checks if item.get("status") == "pass"]
    failed = [item for item in checks if item.get("status") == "fail"]
    warnings = [item for item in checks if item.get("status") == "warning"]

    print("\n========== 数据发布包检查 ==========")
    print(f"已通过：{len(passed)} 项；待确认：{len(failed)} 项；提示：{len(warnings)} 项。")

    if report.get("ready_for_repository"):
        print("结果：全部上传闸门已通过，可以进入最终上传步骤。")
    else:
        print("结果：数据结构检查已完成，但发布前确认事项尚未全部完成。")

    if failed:
        print("\n发布前待确认：")
        for item in failed:
            print(f"- {_label(str(item['code']))}：{_message(item)}")

    if warnings:
        print("\n需要继续整理但不判定为数据错误：")
        for item in warnings:
            print(f"- {_label(str(item['code']))}：{_message(item)}")

    report_path = args.release_directory.resolve() / "validation" / "release_readiness.json"
    print(f"\n详细报告：{report_path}")
    print("========================================")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
