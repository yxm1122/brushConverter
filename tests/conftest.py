# -*- coding: utf-8 -*-
"""任务183A：全量 pytest 的测试隔离契约。

背景：四个 GUI/集成测试文件原先各自要求临时目录必须落在本任务的
.tmp/170、.tmp/172、.tmp/177、.tmp/180。全量回归只用一条命令、一个
--basetemp（例如 .tmp/183A/pytest），同一进程不可能同时满足四个根，
于是出现 146 项隔离断言失败/错误。

本办法不改动任何隔离断言，也不放宽强度：
- 命令行提供 --basetemp 时，本文件在 pytest_configure 阶段把它的父目录
  （.tmp/183A/pytest → .tmp/183A）发布到环境变量
  BRUSHCONVERTER_TEST_TASK_ROOT；
- 四个测试文件改为「环境变量优先、原任务目录兜底」，全量运行时接受同一个
  工作区隔离根，单独运行时仍要求各自原来的任务根；
- 未提供 --basetemp 时不设置环境变量，保持原行为。

本文件不设置 QT_QPA_PLATFORM，GUI 测试自身仍按原样使用 offscreen；
Krita 子进程的平台问题由 tools/krita_render_runner.py 在子进程 env 中处理。
"""

from __future__ import annotations

import os
from pathlib import Path


def pytest_configure(config):
    """把 --basetemp 的父目录设为本次运行共享的任务根（未提供则不变）。"""
    basetemp = config.getoption("basetemp")
    if not basetemp:
        return
    # 与 pytest 的 TempPathFactory 一致：相对路径按当前工作目录解析成绝对路径。
    task_root = Path(os.path.abspath(str(basetemp))).parent
    os.environ["BRUSHCONVERTER_TEST_TASK_ROOT"] = str(task_root)
