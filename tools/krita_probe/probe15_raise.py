# -*- coding: utf-8 -*-
"""探测 15：脚本抛未捕获异常时 kritarunner 的退出码。"""
import sys

print("RAISE_PROBE_BEGIN", flush=True)
raise RuntimeError("故意抛出的测试异常")
