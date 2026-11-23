// tools/ps_pressure_ramp.jsx —— PS 侧压力 ramp 正式批（任务 143）
//
// 运行方式（由 tools/ps_pressure_ramp.py 调用）：
//   "<LOCAL_APP_DIR>/PS2025/Adobe Photoshop 2025/Photoshop.exe" -r <REPO>/tools/ps_pressure_ramp.jsx
//
// 与 ps_export.jsx 同协议（job.json / done.json / batch.log），但为压感 ramp 改写：
//   1) 每张用 PathItem.strokePath(ToolType.BRUSH, true) —— 官方文档第二参数 simulatePressure
//      （tools/ps_pressure_probe.jsx 实测 PS 2025 接受；单参数 = 假，旧 ps_export 即单参数）；
//   2) 可逐张切笔刷（job.items[].brush_name），可逐张写纹理开关/深度/缩放/亮度/对比度/反相/
//      不透明度/流量/混合模式（job.items[].opts）；
//   3) 纹理图案由 job.texture_path 的 PNG 经「定义图案」注入（名称 job.texture_name），
//      ABR 内嵌 patt 只作备份 —— 与 128A 的实测线路一致；
//   4) 不写笔刷几何、不写动态对象（动态来自 ABR 夹具：bVTy=2 / Mnm）。
//
// job.json 顶层：out_dir / canvas{width,height,dpi} / texture_path / texture_name /
//                abr_path / brush_name(默认) / path{n,x0,y0,x1,y1} / items[]
// items[]：file / brush_name(可选) / stroke("true"|"plain"|"false"，默认 true) /
//          opts{use_texture, texture_scale, texture_depth, brightness, contrast,
//               invert, opacity, flow, blend_kind("char"|"str"), blend_value}
// 产物：out_dir/<file>；done.json 每张写 ok/readback/verify/error；batch.log 全文。
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
    function setOpts(o) {
        var d = new ActionDescriptor();
        var r = new ActionReference();
        r.putClass(sid("paintbrushTool"));
        d.putReference(cid("null"), r);
        d.putObject(cid("T   "), sid("currentToolOptions"), o);
        executeAction(cid("setd"), d, DialogModes.NO);
    }
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
    function selectBrushTool() {
        var d0 = new ActionDescriptor();
        var r0 = new ActionReference();
        r0.putClass(sid("paintbrushTool"));
        d0.putReference(cid("null"), r0);
        executeAction(cid("slct"), d0, DialogModes.NO);
    }
    function applyOpts(j) {
        if (j.opts === null) { return; }
        var o = getOpts();
        var op = j.opts || {};
        var useTex = (op.use_texture === undefined) ? false : !!op.use_texture;
        o.putBoolean(sid("useTexture"), useTex);
        if (useTex) {
            if (op.pattern_name) {
                var tp = new ActionDescriptor();
                tp.putString(sid("name"), op.pattern_name);
                o.putObject(sid("texture"), sid("pattern"), tp);
            } else if (op.use_pattern !== false) {
                var tp0 = new ActionDescriptor();
                tp0.putString(sid("name"), job.texture_name);
                o.putObject(sid("texture"), sid("pattern"), tp0);
            }
            if (op.texture_scale !== undefined) { o.putUnitDouble(sid("textureScale"), cid("#Prc"), op.texture_scale); }
            if (op.brightness !== undefined) { o.putInteger(sid("textureBrightness"), op.brightness); }
            if (op.contrast !== undefined) { o.putInteger(sid("textureContrast"), op.contrast); }
            if (op.texture_depth !== undefined) { o.putUnitDouble(sid("textureDepth"), cid("#Prc"), op.texture_depth); }
            if (op.invert !== undefined) { o.putBoolean(sid("invertTexture"), !!op.invert); }
        }
        // 不透明度/流量必须每张显式写死（缺省 100）：PS 工具状态跨文档保留，
        // 上一张写的 50 会漏到下一张（实测：F-9 base50 组把 hardMix 组带成 opacity=50）
        o.putInteger(sid("opacity"), (op.opacity === undefined) ? 100 : op.opacity);
        o.putInteger(sid("flow"), (op.flow === undefined) ? 100 : op.flow);
        // useTipDynamics / useScatter / smoothing 只有 job 明确要求时才写：
        // 尺寸动态（szVr）依赖 useTipDynamics=true，无条件写死 false 会把尺寸 ramp 关掉（实测坑）
        if (op.use_tip_dynamics !== undefined) { o.putBoolean(sid("useTipDynamics"), !!op.use_tip_dynamics); }
        if (op.use_scatter !== undefined) { o.putBoolean(sid("useScatter"), !!op.use_scatter); }
        if (op.smoothing !== undefined) { o.putBoolean(sid("smoothing"), !!op.smoothing); }
        setOpts(o);
        if (useTex && op.blend_value) {
            var o2 = getOpts();
            var typeId;
            try { typeId = o2.getEnumerationType(sid("textureBlendMode")); }
            catch (e1) { typeId = sid("blendMode"); }
            var valueId = (op.blend_kind === "str") ? sid(op.blend_value) : cid(op.blend_value);
            o2.putEnumerated(sid("textureBlendMode"), typeId, valueId);
            setOpts(o2);
        }
    }
    function readBack() {
        var o = getOpts();
        var line = "useTexture=" + readBool(o, sid("useTexture")) +
                   ";pattern=" + readPatternName(o) +
                   ";scale=" + readUnit(o, sid("textureScale")) +
                   ";depth=" + readUnit(o, sid("textureDepth")) +
                   ";brightness=" + readInt(o, sid("textureBrightness")) +
                   ";contrast=" + readInt(o, sid("textureContrast")) +
                   ";blend=" + readEnumName(o, sid("textureBlendMode")) +
                   ";invert=" + readBool(o, sid("invertTexture")) +
                   ";opacity=" + readInt(o, sid("opacity")) +
                   ";flow=" + readInt(o, sid("flow"));
        try { line += ";diameter=" + readUnit(o.getObjectValue(sid("brush")), sid("diameter")); } catch (e2) {}
        return line;
    }
    function addLinePath(piCfg) {
        var pts = [];
        var n = piCfg.n || 2;
        for (var i = 0; i < n; i++) {
            var t = (n === 1) ? 0 : i / (n - 1);
            var p = new PathPointInfo();
            p.kind = PointKind.CORNERPOINT;
            p.anchor = [piCfg.x0 + (piCfg.x1 - piCfg.x0) * t, piCfg.y0 + (piCfg.y1 - piCfg.y0) * t];
            p.leftDirection = p.anchor;
            p.rightDirection = p.anchor;
            pts.push(p);
        }
        var sub = new SubPathInfo();
        sub.closed = false;
        sub.operation = ShapeOperation.SHAPEADD;
        sub.entireSubPath = pts;
        return app.activeDocument.pathItems.add("bc_ramp_path", [sub]);
    }
    function exportPng(path) {
        var po = new PNGSaveOptions();
        try { po.transparency = true; } catch (e) {}
        app.activeDocument.saveAs(new File(path), po, true, Extension.LOWERCASE);
    }

    var results = [];
    var job = null;
    var abrInfo = "SKIP";
    try {
        app.displayDialogs = DialogModes.NO;
        var jf = new File(jobPath);
        jf.encoding = "UTF-8";
        jf.open("r");
        job = eval("(" + jf.read() + ")");
        jf.close();
        L("job loaded, items=" + job.items.length + ", work=" + WORK);
        while (app.documents.length > 0) { app.documents[0].close(SaveOptions.DONOTSAVECHANGES); }

        if (job.abr_paths) {
            for (var ai = 0; ai < job.abr_paths.length; ai++) {
                try { app.load(new File(job.abr_paths[ai])); abrInfo += "OK:" + job.abr_paths[ai].split("/").pop() + " "; }
                catch (eA) { abrInfo += "FAIL:" + eA.message + " "; }
                L("LOAD_ABR " + ai + " " + abrInfo + " " + job.abr_paths[ai]);
            }
        } else if (job.abr_path) {
            try { app.load(new File(job.abr_path)); abrInfo = "OK"; }
            catch (eA) { abrInfo = "FAIL " + eA.message; }
            L("LOAD_ABR " + abrInfo + " " + job.abr_path);
        }
        // 定义图案（外部 PNG → 预设管理器），名称 = job.texture_name
        if (job.texture_path) {
            var texDoc = app.open(new File(job.texture_path));
            L("texture opened " + texDoc.width + "x" + texDoc.height);
            var dp = new ActionDescriptor();
            dp.putString(cid("Nm  "), job.texture_name);
            executeAction(cid("DfnP"), dp, DialogModes.NO);
            texDoc.close(SaveOptions.DONOTSAVECHANGES);
            L("pattern defined: " + job.texture_name);
        }
        selectBrushTool();
        var col = new SolidColor();
        col.rgb.red = 0; col.rgb.green = 0; col.rgb.blue = 0;
        app.foregroundColor = col;

        for (var i = 0; i < job.items.length; i++) {
            var j = job.items[i];
            var res = { file: j.file, ok: false, error: "", readback: "", brush: "" };
            var doc = null;
            try {
                doc = app.documents.add(job.canvas.width, job.canvas.height, job.canvas.dpi,
                                        "bc_ramp_" + i, NewDocumentMode.RGB, DocumentFill.TRANSPARENT);
                var bn = j.brush_name || job.brush_name;
                if (bn) { selectBrushByName(bn); res.brush = bn; }
                applyOpts(j);
                res.readback = readBack();
                var pi = addLinePath(job.path);
                var stroke = j.stroke || "true";
                if (stroke === "true") { pi.strokePath(ToolType.BRUSH, true); }
                else if (stroke === "false") { pi.strokePath(ToolType.BRUSH, false); }
                else { pi.strokePath(ToolType.BRUSH); }
                app.activeDocument = doc;
                exportPng(job.out_dir + "/" + j.file);
                res.ok = true;
                L("OK " + j.file + " brush=" + res.brush + " stroke=" + stroke);
                L("READBACK " + j.file + " | " + res.readback);
            } catch (e) {
                res.error = e.message + " line " + e.line;
                L("FAIL " + j.file + " : " + res.error);
            }
            try { if (doc) { doc.close(SaveOptions.DONOTSAVECHANGES); } } catch (eB) {}
            results.push(res);
        }
    } catch (e) {
        L("FATAL: " + e.message + " line " + e.line);
        results.push({ file: "", ok: false, error: "FATAL " + e.message, readback: "", brush: "" });
    } finally {
        function clean(s) {
            return String(s).replace(/"/g, "'").replace(/\\/g, "/").replace(/[\r\n]/g, " ");
        }
        try {
            var out = new File(donePath);
            out.encoding = "UTF-8";
            out.open("w");
            var arr = [];
            for (var k = 0; k < results.length; k++) {
                var r = results[k];
                arr.push('{"file":"' + clean(r.file) + '","ok":' + r.ok +
                         ',"error":"' + clean(r.error) + '","readback":"' + clean(r.readback) +
                         '","brush":"' + clean(r.brush) + '"}');
            }
            out.writeln('{"ps_version":"' + clean(app.version) + '","abr":"' + clean(abrInfo) +
                        '","results":[' + arr.join(",") + "]}");
            out.close();
        } catch (eC) {}
        logFile.close();
        try { app.quit(); } catch (eD) {}
    }
})();
