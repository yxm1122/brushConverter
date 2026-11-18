# -*- coding: utf-8 -*-
"""kritarunner 环境探测脚本（v2）。

注意：kritarunner 把脚本当模块加载执行（实测 __name__ = 'probe_env'），
所以本文件不写 if __name__ == "__main__" 守卫，直接在模块级调用 main()。
运行方式（cwd 必须是本目录）：
  cd tools/krita_probe && "<LOCAL_APP_DIR>/Krita (x64)/bin/kritarunner.exe" -s probe_env
"""
import json
import os
import sys


def main():
    info = {}
    info["python_version"] = sys.version
    info["executable"] = sys.executable
    info["sys_path_head"] = sys.path[:6]
    info["argv"] = sys.argv
    info["cwd"] = os.getcwd()
    try:
        import krita
        info["krita_module"] = getattr(krita, "__file__", None)
        inst = krita.Krita.instance()
        info["instance_repr"] = repr(inst)
        if inst is not None:
            info["krita_version"] = inst.version()
            info["documents"] = len(inst.documents())
            info["windows"] = len(inst.windows())
            info["activeWindow"] = repr(inst.activeWindow())
            info["activeDocument"] = repr(inst.activeDocument())
            info["batchmode"] = inst.batchmode()
            for rtype in ("preset", "paintoppresets", "brushes"):
                try:
                    res = inst.resources(rtype)
                    info["resources_" + rtype] = len(res)
                except Exception as exc:
                    info["resources_" + rtype + "_error"] = repr(exc)
            try:
                doc = inst.createDocument(100, 100, "probe", "RGBA", "U8", "", 72.0)
                info["createDocument"] = repr(doc)
                if doc is not None:
                    info["doc_width"] = doc.width()
                    info["doc_height"] = doc.height()
                    info["rootNode"] = repr(doc.rootNode())
                    info["activeNode"] = repr(doc.activeNode())
                    doc.close()
            except Exception as exc:
                info["createDocument_error"] = repr(exc)
    except Exception as exc:
        info["error"] = repr(exc)
    print("PROBE_JSON_BEGIN")
    print(json.dumps(info, ensure_ascii=False, indent=2))
    print("PROBE_JSON_END")
    sys.stdout.flush()


main()
