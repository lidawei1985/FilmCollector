# -*- coding: utf-8 -*-
"""
run_report.py —— 长期运行报告 CLI（运营能力）

逻辑已统一到 backend/core/report.py（单一事实来源），本文件仅作为命令行入口：
- 读真实数据生成报告并落盘：output/report_daily.json + 追加 output/report.log
- 同时把报告打印到终端，便于手动查看

用法：
  python tools/run_report.py            # 读真实数据生成报告（窗口 7 天）
  python tools/run_report.py --days 7   # 报告窗口（仅影响展示文案）
"""
import os
import sys
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.core import report as report_mod


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    args = ap.parse_args()

    rep = report_mod.write_report(days=args.days)
    print(report_mod.json.dumps(rep, ensure_ascii=False, indent=2) if hasattr(report_mod, "json") else rep)
    return rep


if __name__ == "__main__":
    main()
