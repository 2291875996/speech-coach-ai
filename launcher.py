"""
launcher.py — AI演讲反馈教练 中文菜单启动器
由 运行.bat 调用，提供交互式菜单选择。
"""

import os
import subprocess
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent

BANNER = """
╔══════════════════════════════════════════════════╗
║     AI 演讲反馈教练 — AI Speech Coach             ║
║                     v8.0                          ║
╚══════════════════════════════════════════════════╝
"""

MENU = """
  [1]  命令行分析 — 指定演讲视频路径
  [2]  Web 仪表盘 — Streamlit 可视化界面
  [3]  自动化测试 — 合成视频跑全流程
  [4]  环境检查 — 验证依赖是否就绪
  [5]  安装依赖 — pip install -r requirements.txt
  [0]  退出
"""


def clear():
    os.system("cls" if os.name == "nt" else "clear")


def pause():
    input("\n  按 Enter 键返回主菜单...")


def run_cmd_analyze():
    clear()
    print("\n  命令行分析模式")
    print("  " + "─" * 40)
    print("  将视频文件拖放到此窗口后按回车:\n")
    video_path = input("  视频路径: ").strip().strip('"')
    if not video_path:
        print("  未输入路径")
        return
    if not os.path.isfile(video_path):
        print(f"  文件不存在: {video_path}")
        return

    output_dir = input("  输出目录 (直接回车=默认 output/): ").strip()
    print("\n  正在分析演讲视频，请等待...\n  " + "─" * 40 + "\n")

    cmd = [sys.executable, str(PROJECT_DIR / "main.py"), video_path]
    if output_dir:
        cmd += ["-o", output_dir]
    subprocess.run(cmd)

    print("\n  分析完成!")


def run_dashboard():
    clear()
    print("\n  Streamlit Web 仪表盘")
    print("  " + "─" * 40)
    print("  浏览器打开 http://localhost:8501 即可使用")
    print("  按 Ctrl+C 停止仪表盘")
    print("  " + "─" * 40 + "\n")

    # Check streamlit
    try:
        subprocess.run([sys.executable, "-c", "import streamlit"], check=True,
                       capture_output=True)
    except subprocess.CalledProcessError:
        print("  [错误] 未安装 streamlit，请先选择菜单 [5] 安装依赖")
        return

    subprocess.run([
        sys.executable, "-m", "streamlit", "run",
        str(PROJECT_DIR / "ui" / "dashboard.py"),
    ])
    print("\n  仪表盘已停止")


def run_test():
    clear()
    print("\n  自动化测试 — 3 个合成视频全流程测试")
    print("  " + "─" * 40 + "\n")
    subprocess.run([sys.executable, str(PROJECT_DIR / "run_test.py"), "--keep-output"])
    print("\n  测试完成!")


def run_validate():
    clear()
    print("\n  环境依赖检查")
    print("  " + "─" * 40 + "\n")
    subprocess.run([sys.executable, "-c",
                     "import sys; print(f'Python {sys.version}')"])
    print()
    subprocess.run([sys.executable, str(PROJECT_DIR / "main.py"),
                     "dummy.mp4", "--validate"])
    print()


def run_install():
    clear()
    print("\n  安装 Python 依赖")
    print("  " + "─" * 40 + "\n")
    result = subprocess.run([
        sys.executable, "-m", "pip", "install", "-r",
        str(PROJECT_DIR / "requirements.txt"),
    ])
    if result.returncode == 0:
        print("\n  全部依赖安装完成!")
    else:
        print("\n  [警告] 部分依赖安装失败，请检查网络或手动安装")


def main():
    while True:
        clear()
        print(BANNER)
        print(MENU)
        print("  " + "─" * 50)
        choice = input("  请输入选项 [0-5]: ").strip()

        if choice == "1":
            run_cmd_analyze()
            pause()
        elif choice == "2":
            run_dashboard()
            pause()
        elif choice == "3":
            run_test()
            pause()
        elif choice == "4":
            run_validate()
            pause()
        elif choice == "5":
            run_install()
            pause()
        elif choice == "0":
            print("\n  再见!\n")
            sys.exit(0)
        else:
            print("  [!] 无效选项，请重新输入")


if __name__ == "__main__":
    main()
