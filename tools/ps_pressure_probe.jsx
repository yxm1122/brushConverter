// tools/ps_pressure_probe.jsx —— PS 2025「沿路径模拟压力」最小验证（任务 143）
//
// 运行方式（由 tools/ps_pressure_ramp.py 调用）：
//   "<LOCAL_APP_DIR>/PS2025/Adobe Photoshop 2025/Photoshop.exe" -r <REPO>/tools/ps_pressure_probe.jsx
//
// 环境变量：
//   BRUSHCONVERTER_REPO     仓库根（取 job 默认路径用；可不设）
//   BRUSHCONVERTER_PS_WORK  WORK 目录（job.json / done.json / batch.log 都在这里；必填）
//
// 背景（为什么有两条路）：
//   1) PathItem.strokePath(tool, simulatePressure) —— Photoshop Scripting 官方文档有第二个
//      布尔参数（Docs: PathItem.strokePath void strokePath(ToolType tool, bool simulatePressure)），
//      旧版 ps_export.jsx 只传了 tool，默认未知（旧实测 PS 侧笔迹平坦）；
//   2) Action Manager 事件 strk + stringID "pressure"（PS 二进制里的描述符规范：
//      /stroke ... /pressure [($$$/Actions/Key/Stroke/SimulatePressure=simulate pressure) /boolean]），
//      作为对照路线记录 PS 2025 是否接受。
//
// job.json 字段（全部可选，缺省即最小验证清单）：
//   out_dir            输出目录（必填）
//   canvas             {width,height,dpi}（默认 900x300/72）
//   abr_path           可选：先 app.load 这个 .abr
//   brush_name         可选：按名选中笔刷
//   path               {n, x0, y0, x1, y1}（n 个等分点的直线路径，默认 n=2）
//   variants           变体名数组，取值 plain / false / true / am_pressure
//                       （默认 ["plain","false","true","am_pressure"]）
//   readback_pre_stroke 只回读不落笔（调试用，默认 false）
//
// 产物：每个变体一张 PNG（out_dir/<variant>.png）；done.json 记录本轮 PS 版本、笔刷回读、
//       每个变体的动作异常与像素结果的「成功/异常」；batch.log 留全过程原文。
#target photoshop
(function () {
    var REPO = $.getenv("BRUSHCONVERTER_REPO");
    if (!REPO) { try { REPO = File($.fileName).parent.parent.fsName; } catch (e0) { REPO = "."; } }
    var WORK = $.getenv("BRUSHCONVERTER_PS_WORK");
    if (!WORK) { WORK = REPO + "/.tmp/pressure/ps/_work"; }
    var jobPath = WORK + "/job.json";
    var donePath = WORK + "/done.json";

    var logFile = new File(WORK + "/batch.log");
    logFile.encoding = "UTF-8";
    logFile.open("w");
    function L(s) { logFile.writeln(String(s)); }

    function cid(s) { return charIDToTypeID(s); }
    function sid(s) { return stringIDToTypeID(s); }
    function keyName(k) {
        try { var s = typeIDToStringID(k); if (s && s.length) { return s; } } catch (e) {}
        try { return typeIDToCharID(k); } catch (e) { return "?" + k; }
    }
    function appDesc() {
        var r = new ActionReference(); r.putEnumerated(cid("capp"), cid("Ordn"), cid("Trgt"));
        return executeActionGet(r);
    }
    function getOpts() { return appDesc().getObjectValue(sid("currentToolOptions")); }
    function readInt(o, id) { try { return String(o.getInteger(id)); } catch (e) { return "ABSENT"; } }
    function readUnit(o, id) { try { return String(o.getUnitDoubleValue(id)); } catch (e) { return "ABSENT"; } }
    function readBool(o, id) { try { return String(o.getBoolean(id)); } catch (e) { return "ABSENT"; } }
    function readEnumName(o, id) { try { return keyName(o.getEnumerationValue(id)); } catch (e) { return "ABSENT"; } }
    function readPatternName(o) {
        try { return String(o.getObjectValue(sid("texture")).getString(sid("name"))); } catch (e) { return "ABSENT"; }
    }
    function selectBrushByName(name) {
        var d = new ActionDescriptor();
        var r = new ActionReference();
        r.putName(cid("Brsh"), name);
        d.putReference(cid("null"), r);
        executeAction(cid("slct"), d, DialogModes.NO);
    }
    function toolReadback() {
        var o = getOpts();
        var line = "useTexture=" + readBool(o, sid("useTexture")) +
                   ";pattern=" + readPatternName(o) +
                   ";textureScale=" + readUnit(o, sid("textureScale")) +
                   ";textureDepth=" + readUnit(o, sid("textureDepth")) +
                   ";textureBrightness=" + readInt(o, sid("textureBrightness")) +
                   ";textureContrast=" + readInt(o, sid("textureContrast")) +
                   ";blend=" + readEnumName(o, sid("textureBlendMode")) +
                   ";invert=" + readBool(o, sid("invertTexture")) +
                   ";opacity=" + readInt(o, sid("opacity")) +
                   ";flow=" + readInt(o, sid("flow")) +
                   ";spacing=" + readUnit(o, sid("spacing"));
        try { line += ";diameter=" + readUnit(o.getObjectValue(sid("brush")), sid("diameter")); } catch (e) {}
        return line;
    }
    function addLinePath(cfg) {
        var pts = [];
        var n = cfg && cfg.n ? cfg.n : 2;
        for (var i = 0; i < n; i++) {
            var t = (n === 1) ? 0 : i / (n - 1);
            var p = new PathPointInfo();
            p.kind = PointKind.CORNERPOINT;
            p.anchor = [cfg.x0 + (cfg.x1 - cfg.x0) * t, cfg.y0 + (cfg.y1 - cfg.y0) * t];
            p.leftDirection = p.anchor;
            p.rightDirection = p.anchor;
            pts.push(p);
        }
        var sub = new SubPathInfo();
        sub.closed = false;
        sub.operation = ShapeOperation.SHAPEADD;
        sub.entireSubPath = pts;
        var pi = app.activeDocument.pathItems.add("bc_stroke_path", [sub]);
        return pi;
    }
    // 变体 4：Action Manager 事件 strk —— 以「描述符规范」里的 stringID "pressure" 传模拟压力
    function strokeViaActionManager(pi) {
        var desc = new ActionDescriptor();
        var ref = new ActionReference();
        ref.putEnumerated(cid("Path"), cid("Path"), cid("Trgt"));
        desc.putReference(cid("null"), ref);
        var toolType = null;
        try { toolType = getOpts().getEnumerationType(sid("textureBlendMode")); } catch (eT) {}
        desc.putEnumerated(sid("tool"), cid("Pt  "), cid("Brsh"));
        desc.putBoolean(sid("pressure"), true);
        executeAction(cid("strk"), desc, DialogModes.NO);
        return "AM strk+pressure ok";
    }
    function exportPng(path) {
        var po = new PNGSaveOptions();
        try { po.transparency = true; } catch (e) {}
        app.activeDocument.saveAs(new File(path), po, true, Extension.LOWERCASE);
    }

    var job = {
        out_dir: WORK,
        canvas: { width: 900, height: 300, dpi: 72 },
        path: { n: 2, x0: 70, y0: 150, x1: 830, y1: 150 },
        variants: ["plain", "false", "true", "am_pressure"],
        readback_pre_stroke: false
    };
    var results = [];
    try {
        app.displayDialogs = DialogModes.NO;
        var jf = new File(jobPath);
        if (jf.exists) {
            jf.encoding = "UTF-8";
            jf.open("r");
            var t = jf.read();
            jf.close();
            var userJob = eval("(" + t + ")");
            for (var k in userJob) { job[k] = userJob[k]; }
            L("job.json 已加载");
        } else {
            L("未找到 job.json，使用内置最小清单");
        }
        var col = new SolidColor();
        col.rgb.red = 0; col.rgb.green = 0; col.rgb.blue = 0;
        app.foregroundColor = col;
        // 清空当前文档（PS 启动可能带一个欢迎文档）
        while (app.documents.length > 0) { app.documents[0].close(SaveOptions.DONOTSAVECHANGES); }

        var abrInfo = "SKIP";
        if (job.abr_path) {
            try { app.load(new File(job.abr_path)); abrInfo = "OK"; }
            catch (eA) { abrInfo = "FAIL " + eA.message; }
        }
        L("LOAD_ABR " + abrInfo);
        // 画笔工具
        var d0 = new ActionDescriptor();
        var r0 = new ActionReference();
        r0.putClass(sid("paintbrushTool"));
        d0.putReference(cid("null"), r0);
        executeAction(cid("slct"), d0, DialogModes.NO);
        var brushSel = "SKIP";
        if (job.brush_name) {
            try { selectBrushByName(job.brush_name); brushSel = "OK " + job.brush_name; }
            catch (eB) { brushSel = "FAIL " + eB.message; }
        }
        L("SELECT_BRUSH " + brushSel);
        L("TOOL_READBACK " + toolReadback());

        var variants = job.variants || ["plain", "false", "true"];
        for (var vi = 0; vi < variants.length; vi++) {
            var variant = variants[vi];
            var res = { variant: variant, ok: false, error: "", readback: "", file: variant + ".png" };
            var doc = null;
            try {
                doc = app.documents.add(job.canvas.width, job.canvas.height, job.canvas.dpi,
                                        "bc_" + variant, NewDocumentMode.RGB, DocumentFill.TRANSPARENT);
                if (job.brush_name) { selectBrushByName(job.brush_name); }
                res.readback = toolReadback();
                if (job.readback_pre_stroke) {
                    res.ok = true;
                    L("READBACK_ONLY " + variant + " | " + res.readback);
                    doc.close(SaveOptions.DONOTSAVECHANGES);
                    results.push(res);
                    continue;
                }
                var pi = addLinePath(job.path);
                if (variant === "plain") {
                    pi.strokePath(ToolType.BRUSH);
                } else if (variant === "false") {
                    pi.strokePath(ToolType.BRUSH, false);
                } else if (variant === "true") {
                    pi.strokePath(ToolType.BRUSH, true);
                } else if (variant === "am_pressure") {
                    res.error = strokeViaActionManager(pi);
                } else {
                    throw new Error("未知变体：" + variant);
                }
                res.ok = true;
                app.activeDocument = doc;
                exportPng(job.out_dir + "/" + res.file);
                L("OK " + variant + " | " + res.readback);
            } catch (e) {
                res.error = (res.error ? res.error + " / " : "") + e.message + " line " + e.line;
                L("FAIL " + variant + " : " + res.error);
            }
            try { if (doc) { doc.close(SaveOptions.DONOTSAVECHANGES); } } catch (eC) {}
            results.push(res);
        }
    } catch (e) {
        L("FATAL: " + e.message + " line " + e.line);
        results.push({ variant: "", ok: false, error: "FATAL " + e.message, readback: "", file: "" });
    } finally {
        function clean(s) {
            return String(s).replace(/"/g, "'").replace(/\\/g, "/").replace(/[\r\n]/g, " ");
        }
        try {
            var out = new File(donePath);
            out.encoding = "UTF-8";
            out.open("w");
            var arr = [];
            for (var q = 0; q < results.length; q++) {
                var r = results[q];
                arr.push('{"variant":"' + clean(r.variant) + '","ok":' + r.ok +
                         ',"error":"' + clean(r.error) + '","readback":"' + clean(r.readback) +
                         '","file":"' + clean(r.file) + '"}');
            }
            out.writeln('{"ps_version":"' + clean(app.version) + '","abr":"' + clean(abrInfo) +
                        '","brush":"' + clean(brushSel) + '","results":[' + arr.join(",") + "]}");
            out.close();
        } catch (eD) {}
        logFile.close();
        try { app.quit(); } catch (eE) {}
    }
})();
