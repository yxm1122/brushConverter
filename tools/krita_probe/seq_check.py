# -*- coding: utf-8 -*-
import json, os, subprocess, sys
sys.path.insert(0, "tools")
import krita_render_runner as runner
p = "converted/kpp/003_大怪兽-圆头虚_用数位板.kpp"
for i in range(1, 4):
    r = runner.render_one(p, "tools/krita_probe/seq_%d.png" % i, route="gui")
    print("run%d ok=%s exit=%r elapsed=%s reason=%s" % (i, r.get("ok"), r.get("exit_code"), r.get("elapsed_s"), (r.get("reason") or "")[:80]))
    log = os.path.join(os.environ["LOCALAPPDATA"], "krita.log")
    try:
        lines = open(log, encoding="utf-8", errors="replace").read().splitlines()
        tail = [l for l in lines[-8:]]
        print("   krita.log tail:", " | ".join(tail[-5:]))
    except Exception as exc:
        print("   log err", exc)
