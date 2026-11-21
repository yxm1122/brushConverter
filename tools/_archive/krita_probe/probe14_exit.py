# -*- coding: utf-8 -*-
"""探测 14：kritarunner 是否透传脚本退出码。"""
import sys

print("EXIT_PROBE_BEGIN", flush=True)
sys.stderr.write("[STEP] 即将 sys.exit(2)\n")
sys.stderr.flush()
sys.exit(2)
