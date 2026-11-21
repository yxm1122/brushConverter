# -*- coding: utf-8 -*-
import json, os, sys
sys.path.insert(0, "tools")
import krita_render_runner as runner
p = "converted/kpp/003_大怪兽-圆头虚_用数位板.kpp"
r1 = runner.render_one(p, "tools/krita_probe/fix_1.png", route="gui")
r2 = runner.render_one(p, "tools/krita_probe/fix_2.png", route="gui")
print("r1", json.dumps({k: r1.get(k) for k in ("ok","alpha_nonzero","elapsed_s","reason")}, ensure_ascii=False))
print("r2", json.dumps({k: r2.get(k) for k in ("ok","alpha_nonzero","elapsed_s","reason")}, ensure_ascii=False))
kritarc = os.path.join(os.environ["LOCALAPPDATA"], "kritarc")
text = open(kritarc, encoding="utf-8", errors="replace").read()
print("kritarc enable_bc 出现次数:", text.count("enable_bc_auto_render"))
print("sessionOnStartup 行:", [l for l in text.splitlines() if l.startswith("sessionOnStartup")])
print("插件残留:", os.path.isdir(os.path.join(os.environ["APPDATA"], "krita", "pykrita", "bc_auto_render")))
