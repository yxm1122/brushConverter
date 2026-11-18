// tools/ps_read_params.jsx —— 用 Photoshop 自己的脚本读纹理参数，做「PS 参数理解」交叉核对（任务 10）
//
// 目的（任务书 §三.5）：
//   用 PS 自己的 JSX 读纹理参数（亮度/对比度/深度/缩放/混合模式），把读到的值与
//   本项目 ABR 解析器解析出的值对比，确认「我们理解的参数名与取值范围」= 「PS 自己认为的」。
//
// 可用的关键通路（实测，参考并行任务 05 的 tools/ps_export.jsx 与 docs/PS真值自动化说明.md）：
//   * 画笔工具类名是 paintbrushTool（不是 brushTool/Brsh）；
//   * 工具选项的读法：executeActionGet(capp/Ordn/Trgt).getObjectValue(sid("currentToolOptions"))；
//   * 写回：putClass(sid("paintbrushTool")) + putObject(cid("T   "), sid("currentToolOptions"), o) + setd；
//   * textureBrightness / textureContrast / textureDepth / textureScale / textureBlendMode
//     只有在 useTexture=true 之后才出现在描述符里（分两阶段写）。
//
// 用法（不需要 COM）：
//   "<LOCAL_APP_DIR>/PS2025/Adobe Photoshop 2025/Photoshop.exe" -r "<REPO>/tools/ps_read_params.jsx"
// 日志：docs/研究报告/ps_read_params.log（UTF-8）；末尾写 READ_PARAMS_DONE 并 app.quit()。
//
// 注意：本脚本会读**当前画笔的工具选项**并临时改动它（不写回预设、不保存文档），
// 退出时 Photoshop 不会保存这些临时状态；若同时有别的脚本在跑 Photoshop，不要并行执行。

#target photoshop

(function () {
    // 日志落在 docs/研究报告/ 下（本任务的写入范围），便于随报告一起归档
    // 仓库根：环境变量优先（BRUSHCONVERTER_REPO），其次按脚本自身位置推导（不写死本机绝对路径）
    var REPO = $.getenv("BRUSHCONVERTER_REPO");
    if (!REPO) { try { REPO = File($.fileName).parent.parent.fsName; } catch (e) { REPO = "."; } }
    var LOG = REPO.replace(/\\/g, "/") + "/docs/研究报告/ps_read_params.log";
    var f = new File(LOG);
    f.encoding = "UTF-8";
    f.open("w");
    function L(s) { f.writeln(String(s)); }
    function cid(s) { return charIDToTypeID(s); }
    function sid(s) { return stringIDToTypeID(s); }
    function keyName(k) {
        try { var s = typeIDToStringID(k); if (s && s.length) { return s; } } catch (e) {}
        try { return typeIDToCharID(k); } catch (e) { return "?" + k; }
    }
    function appDesc() {
        var r = new ActionReference();
        r.putEnumerated(cid("capp"), cid("Ordn"), cid("Trgt"));
        return executeActionGet(r);
    }
    function getOpts() { return appDesc().getObjectValue(sid("currentToolOptions")); }
    function setOpts(o) {
        var d = new ActionDescriptor();
        var r = new ActionReference();
        r.putClass(sid("paintbrushTool"));
        d.putReference(cid("null"), r);
        d.putObject(cid("T   "), sid("currentToolOptions"), o);
        executeAction(cid("setd"), d, DialogModes.NO);
    }
    function readInt(o, id) {
        try { return String(o.getInteger(id)); } catch (e) { return "读取失败:" + e.message; }
    }
    function readUnit(o, id) {
        try { return String(o.getUnitDoubleValue(id)); } catch (e) { return "读取失败:" + e.message; }
    }
    function readBool(o, id) {
        try { return String(o.getBoolean(id)); } catch (e) { return "读取失败:" + e.message; }
    }
    function readEnumName(o, id) {
        try { return keyName(o.getEnumerationValue(id)); } catch (e) { return "读取失败:" + e.message; }
    }

    var doc = null, texDoc = null;
    try {
        app.displayDialogs = DialogModes.NO;
        L("=== ps_read_params.jsx（任务 10 参数交叉核对） ===");
        L("PS 版本: " + app.version + " / " + app.name);
        L("启动时间: " + new Date().toString());

        // 1) 选中画笔工具（类名 paintbrushTool，实测可用）
        var d0 = new ActionDescriptor();
        var r0 = new ActionReference();
        r0.putClass(sid("paintbrushTool"));
        d0.putReference(cid("null"), r0);
        executeAction(cid("slct"), d0, DialogModes.NO);
        L("选中画笔工具: OK，currentTool=" + app.currentTool);

        // 2) 定义一个临时图案（纹理参数只在 useTexture=true 后才出现）
        doc = app.documents.add(64, 64, 72, "ps_read_params_tex", NewDocumentMode.GRAYSCALE, DocumentFill.WHITE);
        doc.selection.selectAll();
        doc.selection.fill(app.foregroundColor);
        doc.selection.deselect();
        var dp = new ActionDescriptor();
        dp.putString(cid("Nm  "), "ps_read_params_tex");
        executeAction(cid("DfnP"), dp, DialogModes.NO);
        doc.close(SaveOptions.DONOTSAVECHANGES);
        doc = null;
        L("临时图案已定义: ps_read_params_tex");

        // 3) 打开纹理并写入一组「已知值」，再读回（读回值 = PS 自己认为的值）
        var o1 = getOpts();
        o1.putBoolean(sid("useTexture"), true);
        var tp = new ActionDescriptor();
        tp.putString(sid("name"), "ps_read_params_tex");
        o1.putObject(sid("texture"), sid("pattern"), tp);
        o1.putUnitDouble(sid("textureScale"), cid("#Prc"), 100);
        o1.putInteger(sid("textureBrightness"), 0);
        o1.putInteger(sid("textureContrast"), 0);
        o1.putUnitDouble(sid("textureDepth"), cid("#Prc"), 100);
        setOpts(o1);
        L("已启用纹理并写入中性值");

        var o2 = getOpts();
        L("");
        L("--- 纹理参数读取（PS 自己读回的值） ---");
        L("useTexture          = " + readBool(o2, sid("useTexture")));
        L("textureScale        = " + readUnit(o2, sid("textureScale")));
        L("textureBrightness   = " + readInt(o2, sid("textureBrightness")));
        L("textureContrast     = " + readInt(o2, sid("textureContrast")));
        L("textureDepth        = " + readUnit(o2, sid("textureDepth")));
        L("textureBlendMode    = " + readEnumName(o2, sid("textureBlendMode")));
        L("纹理图案名          = " + (function () {
            try { return String(o2.getObjectValue(sid("texture")).getString(sid("name"))); }
            catch (e) { return "读取失败:" + e.message; }
        })());

        // 4) 用一组本项目 ABR 解析里真实出现过的值写入并读回
        //    （示例值取自 docs/texture-mapping-proposal.md §1.2 的实测表：亮度 -31..40、
        //      对比度 -50..6、深度 4..55、缩放 20..73）
        L("");
        L("--- 写入本项目 ABR 实测范围内的值，再读回 ---");
        function writeAndRead(label, mutator) {
            try {
                var o = getOpts();
                mutator(o);
                setOpts(o);
                var back = getOpts();
                L(label + " -> 写入成功，读回: brightness=" + readInt(back, sid("textureBrightness")) +
                  " contrast=" + readInt(back, sid("textureContrast")) +
                  " depth=" + readUnit(back, sid("textureDepth")) +
                  " scale=" + readUnit(back, sid("textureScale")));
            } catch (e) {
                L(label + " -> 写入失败（原始报错）" + e.message);
            }
        }
        writeAndRead("写 b=-27 c=-17 depth=41 scale=53（软铅实测值）", function (o) {
            o.putInteger(sid("textureBrightness"), -27);
            o.putInteger(sid("textureContrast"), -17);
            o.putUnitDouble(sid("textureDepth"), cid("#Prc"), 41);
            o.putUnitDouble(sid("textureScale"), cid("#Prc"), 53);
        });
        writeAndRead("写 b=40 c=6 depth=55 scale=73（样本上界）", function (o) {
            o.putInteger(sid("textureBrightness"), 40);
            o.putInteger(sid("textureContrast"), 6);
            o.putUnitDouble(sid("textureDepth"), cid("#Prc"), 55);
            o.putUnitDouble(sid("textureScale"), cid("#Prc"), 73);
        });

        // 5) 量程探测：PS 自己接受哪些值（越界是否被拒绝或钳制）
        L("");
        L("--- 量程探测：亮度 / 对比度 / 深度 ---");
        function probeInt(label, id, values) {
            for (var i = 0; i < values.length; i++) {
                var v = values[i];
                try {
                    var o = getOpts();
                    o.putInteger(id, v);
                    setOpts(o);
                    L("  " + label + " = " + v + " -> 接受，读回=" + readInt(getOpts(), id));
                } catch (e) {
                    L("  " + label + " = " + v + " -> 拒绝（原始报错）" + e.message);
                }
            }
        }
        function probeUnit(label, id, values) {
            for (var i = 0; i < values.length; i++) {
                var v = values[i];
                try {
                    var o = getOpts();
                    o.putUnitDouble(id, cid("#Prc"), v);
                    setOpts(o);
                    L("  " + label + " = " + v + " -> 接受，读回=" + readUnit(getOpts(), id));
                } catch (e) {
                    L("  " + label + " = " + v + " -> 拒绝（原始报错）" + e.message);
                }
            }
        }
        probeInt("textureBrightness", sid("textureBrightness"), [0, 100, 150, 151, -150, -151]);
        probeInt("textureContrast", sid("textureContrast"), [0, 50, 100, 101, -50, -51]);
        probeUnit("textureDepth", sid("textureDepth"), [0, 1, 100, 101]);
        probeUnit("textureScale", sid("textureScale"), [1, 100, 1000]);

        // 6) 混合模式可写值（枚举 charID，取自 ABR textureBlendMode 的已知取值）
        L("");
        L("--- 混合模式写入/读回（ABR 里的取值） ---");
        var modeChars = { "Sbtr": "减去", "Mltp": "正片叠底", "Hght": "高度", "LnHt": "线性高度",
                          "HrdM": "强光混合(Hard Mix)" };
        for (var mc in modeChars) {
            try {
                var o = getOpts();
                var typeId;
                try { typeId = o.getEnumerationType(sid("textureBlendMode")); }
                catch (e) { typeId = sid("blendMode"); }
                o.putEnumerated(sid("textureBlendMode"), typeId, cid(mc));
                setOpts(o);
                L("  " + mc + "（" + modeChars[mc] + "）-> 读回=" + readEnumName(getOpts(), sid("textureBlendMode")));
            } catch (e) {
                L("  " + mc + "（" + modeChars[mc] + "）-> 写入失败（原始报错）" + e.message);
            }
        }

        L("");
        L("READ_PARAMS_DONE");
    } catch (e) {
        L("脚本异常: " + e.message + "（行 " + e.line + "）");
    } finally {
        try { if (doc) { doc.close(SaveOptions.DONOTSAVECHANGES); } } catch (e2) {}
        try { if (texDoc) { texDoc.close(SaveOptions.DONOTSAVECHANGES); } } catch (e3) {}
        f.close();
        try { app.quit(); } catch (e4) {}
    }
})();
