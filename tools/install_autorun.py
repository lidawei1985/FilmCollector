# -*- coding: utf-8 -*-
"""
install_autorun.py —— 一键注册「开机/登录后自动供片」

用途：让你关机睡觉后，电脑一开机/登录就自动跑一次 FilmCollector 的
      「找新片 → 采集 → 生成订阅包 → （有 Token 则）部署公网」闭环。
      无需打开工具界面，无需手动点按钮。

原理：在 Windows 任务计划程序里建一个任务，触发器为「登录时」，
      动作是运行 `<本程序> --auto-once`（无头单次自动更新后退出）。

使用（二选一）：
  1) 开发态（未打包）：  python tools/install_autorun.py
  2) 已打包成 exe：      双击 影视资源采集器.exe 同级目录下的本脚本，
                        或直接 `python tools/install_autorun.py`

取消自动运行：  python tools/install_autorun.py --remove

注意：
  - 本脚本只创建/删除任务计划，**不会修改你的片库、不会上传、不会写密钥**。
  - 创建任务需要「以管理员身份运行」此脚本（右键 → 以管理员身份运行）。
  - 真正的采集/部署由 app.py --auto-once 执行；是否部署公网取决于你是否已填 Token。
"""
import os
import sys
import subprocess

TASK_NAME = "FilmCollectorAutoRun"


def _resolve_command():
    """解析要注册的可执行命令：打包态用 exe，开发态用 python app.py。"""
    frozen = getattr(sys, "frozen", False)
    here = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(here)
    if frozen:
        exe = sys.executable  # 即 影视资源采集器.exe
        return f'"{exe}" --auto-once'
    # 开发态：python <root>/app.py --auto-once
    py = sys.executable
    app = os.path.join(project_root, "app.py")
    return f'"{py}" "{app}" --auto-once'


def install():
    cmd = _resolve_command()
    # /sc onlogon：登录（开机）即触发；/rl limited 以普通权限跑；/f 覆盖同名任务
    sch = [
        "schtasks", "/create", "/tn", TASK_NAME,
        "/tr", cmd,
        "/sc", "onlogon",
        "/rl", "limited",
        "/f",
    ]
    print("注册命令：", " ".join(sch))
    try:
        r = subprocess.run(sch, capture_output=True, text=True)
        if r.returncode == 0:
            print(f"✅ 已注册任务「{TASK_NAME}」：电脑登录后会自动跑一次自动供片。")
            print("   动作：", cmd)
            print("   取消：python tools/install_autorun.py --remove")
        else:
            print("❌ 注册失败（多半需要「以管理员身份运行」）：")
            print(r.stdout)
            print(r.stderr)
            return False
    except FileNotFoundError:
        print("❌ 未找到 schtasks（仅 Windows 可用）。")
        return False
    return True


def remove():
    sch = ["schtasks", "/delete", "/tn", TASK_NAME, "/f"]
    try:
        r = subprocess.run(sch, capture_output=True, text=True)
        if r.returncode == 0:
            print(f"✅ 已删除任务「{TASK_NAME}」，不再开机自动供片。")
        else:
            print("❌ 删除失败：", r.stdout, r.stderr)
            return False
    except FileNotFoundError:
        print("❌ 未找到 schtasks（仅 Windows 可用）。")
        return False
    return True


def main():
    if "--remove" in sys.argv:
        remove()
    else:
        install()


if __name__ == "__main__":
    main()
