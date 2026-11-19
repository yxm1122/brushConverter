// tools/ps_export.jsx —— Photoshop 侧批量出图脚本（任务 05 基准 + PS 侧实验矩阵）
//
// 运行方式（由 tools/ps_probe.py 调用）：
//   "<LOCAL_APP_DIR>/PS2025/Adobe Photoshop 2025/Photoshop.exe" -r <REPO>/tools/ps_export.jsx
//
// WORK 目录用环境变量 BRUSHCONVERTER_PS_WORK 指定（job.json / done.json / batch.log 都在这里）：
//   * 任务 05 基准：<REPO>/tests/baselines/ps/_work
//   * PS 侧实验矩阵：<REPO>/tests/baselines/exp/ps/_work
// 未设置时回落到任务 05 老路径（向后兼容）。
//
// job.json 字段（新增字段全部可选，老清单不加也能跑）：
//   顶层：out_dir / texture_path / texture_name / texture_scale / canvas / brush
//         abr_path（可选：加载 .abr 笔刷库）+ brush_name（可选：按名选中那支笔）
//   每张：file / mode_kind(char|str) / mode_value / depth / brightness / contrast
//         use_texture（默认 true；false = 参考图）
//         texture_scale（按图覆盖顶层）
//         invert_texture（默认 false；实验矩阵为 true，对应 ABR 源纹理反相）
//         mode_key（可选：multiply / linearHeight，用于回读比对）
//
// 实验口径（与 Krita 侧一致）：只用 tests/定量测试专用.abr 里那支计算笔刷，
// 不换笔、不改笔刷几何（Dmtr=200/硬度100）；纹理用 research/测试用纹理.png 的原始字节
// 定义图案（图案名 job.texture_name），并显式写 invertTexture=true。
//
// 每张出完回读：图案名 / useTexture / invertTexture / scale / depth / brightness /
// contrast / blendMode，写进 done.json 并在 batch.log 留 READBACK 原文。
//
// 关键实现（都来自实测，改动前先读 docs/PS真值自动化说明.md）：
//   1) 画笔工具类名 paintbrushTool；写工具选项用 putClass(paintbrushTool)+putObject(cid("T   "), currentToolOptions)；
//   2) textureBlendMode / textureDepth 在 useTexture=true 之后才出现，分两阶段写；
//   3) 纹理图案按名字引用；4) 「线性高度」只能用 stringIDToTypeID("linearHeight")；
//   5) .abr 用 app.load(File) 加载，再按名选中笔刷（putName(cid("Brsh"), name) + slct）。
#target photoshop
(function () {
    var REPO = $.getenv("BRUSHCONVERTER_REPO");
    if (!REPO) { try { REPO = File($.fileName).parent.parent.fsName; } catch (e) { REPO = "."; } }
    var WORK = $.getenv("BRUSHCONVERTER_PS_WORK");
    if (!WORK) { WORK = REPO + "/tests/baselines/ps/_work"; }
    var jobPath = WORK + "/job.json";
    var donePath = WORK + "/done.json";

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
    function useTexOf(j) { return (j.use_texture === undefined) ? true : !!j.use_texture; }
    function invertOf(j) { return (j.invert_texture === undefined) ? false : !!j.invert_texture; }
    function scaleOf(j) { return (j.texture_scale === undefined) ? job.texture_scale : j.texture_scale; }
    function selectBrushByName(name) {
        var d = new ActionDescriptor();
        var r = new ActionReference();
        r.putName(cid("Brsh"), name);
        d.putReference(cid("null"), r);
        executeAction(cid("slct"), d, DialogModes.NO);
    }
    function brushReadback() {
        var o = getOpts();
        var b = o.getObjectValue(sid("brush"));
        return {
            diameter: readUnit(b, sid("diameter")),
            hardness: readUnit(b, sid("hardness")),
            spacing: readUnit(b, sid("spacing")),
            roundness: readUnit(b, sid("roundness"))
        };
    }

    var logFile = new File(WORK + "/batch.log");
    logFile.encoding = "UTF-8";
    logFile.open("w");
    function L(s) { logFile.writeln(String(s)); }

    var job = null;
    var doc = null;
    var results = [];
    var brushInfo = { abr_loaded: "SKIP", brush_selected: "SKIP", diameter: "", hardness: "", spacing: "", roundness: "" };
    try {
        app.displayDialogs = DialogModes.NO;
        var jf = new File(jobPath);
        jf.encoding = "UTF-8";
        jf.open("r");
        var jobText = jf.read();
        jf.close();
        job = eval("(" + jobText + ")");
        L("job loaded, count=" + job.jobs.length + ", work=" + WORK);

        // 0) 实验通路：加载 ABR 笔刷库并按名选笔（任务 05 老清单没有这两个字段，跳过）
        if (job.abr_path) {
            try {
                app.load(new File(job.abr_path));
                brushInfo.abr_loaded = "OK";
            } catch (e0) {
                brushInfo.abr_loaded = "FAIL " + e0.message;
            }
            L("LOAD_ABR " + brushInfo.abr_loaded + " " + job.abr_path);
        }
        // 选中画笔工具
        var d0 = new ActionDescriptor();
        var r0 = new ActionReference();
        r0.putClass(sid("paintbrushTool"));
        d0.putReference(cid("null"), r0);
        executeAction(cid("slct"), d0, DialogModes.NO);
        if (job.brush_name) {
            try {
                selectBrushByName(job.brush_name);
                brushInfo.brush_selected = "OK " + job.brush_name;
            } catch (e1) {
                brushInfo.brush_selected = "FAIL " + e1.message;
            }
            L("SELECT_BRUSH " + brushInfo.brush_selected);
        }
        var bi = brushReadback();
        brushInfo.diameter = bi.diameter; brushInfo.hardness = bi.hardness;
        brushInfo.spacing = bi.spacing; brushInfo.roundness = bi.roundness;
        L("BRUSH_READBACK diameter=" + bi.diameter + " hardness=" + bi.hardness +
          " spacing=" + bi.spacing + " roundness=" + bi.roundness);
        var col = new SolidColor();
        col.rgb.red = 0; col.rgb.green = 0; col.rgb.blue = 0;
        app.foregroundColor = col;
        L("tool=" + app.currentTool + " foreground=black");

        // 定义纹理图案（按 job.texture_path 的原始字节；PS 会打开该 PNG 后定义为图案）
        var texDoc = app.open(new File(job.texture_path));
        L("texture opened " + texDoc.width + "x" + texDoc.height + " path=" + job.texture_path);
        var dp = new ActionDescriptor();
        dp.putString(cid("Nm  "), job.texture_name);
        executeAction(cid("DfnP"), dp, DialogModes.NO);
        texDoc.close(SaveOptions.DONOTSAVECHANGES);
        L("pattern defined: " + job.texture_name);

        // 一阶段：纹理开关 + 图案/缩放/亮度/对比度/深度/反相 + 不透明度/流量/动态（不改笔刷几何）
        function applyBase(j) {
            var o = getOpts();
            var useTex = useTexOf(j);
            o.putBoolean(sid("useTexture"), useTex);
            if (useTex) {
                var tp = new ActionDescriptor();
                tp.putString(sid("name"), job.texture_name);
                o.putObject(sid("texture"), sid("pattern"), tp);
                o.putUnitDouble(sid("textureScale"), cid("#Prc"), scaleOf(j));
                o.putInteger(sid("textureBrightness"), j.brightness);
                o.putInteger(sid("textureContrast"), j.contrast);
                o.putUnitDouble(sid("textureDepth"), cid("#Prc"), j.depth);
                o.putBoolean(sid("invertTexture"), invertOf(j));
            }
            o.putInteger(sid("opacity"), 100);
            o.putInteger(sid("flow"), 100);
            o.putBoolean(sid("useTipDynamics"), false);
            o.putBoolean(sid("useScatter"), false);
            o.putBoolean(sid("smoothing"), false);
            setOpts(o);
        }
        // 二阶段：写纹理混合模式（此时该键才存在）
        function applyMode(j) {
            if (!useTexOf(j)) { return "none"; }
            var o = getOpts();
            var typeId;
            try { typeId = o.getEnumerationType(sid("textureBlendMode")); }
            catch (e) { typeId = sid("blendMode"); }
            var valueId = (j.mode_kind === "str") ? sid(j.mode_value) : cid(j.mode_value);
            o.putEnumerated(sid("textureBlendMode"), typeId, valueId);
            setOpts(o);
            return keyName(getOpts().getEnumerationValue(sid("textureBlendMode")));
        }
        function readBack() {
            var o = getOpts();
            return {
                useTexture: readBool(o, sid("useTexture")),
                invert: readBool(o, sid("invertTexture")),
                pattern: readPatternName(o),
                scale: readUnit(o, sid("textureScale")),
                depth: readUnit(o, sid("textureDepth")),
                brightness: readInt(o, sid("textureBrightness")),
                contrast: readInt(o, sid("textureContrast")),
                blend: readEnumName(o, sid("textureBlendMode"))
            };
        }
        function rbString(r) {
            return "useTexture=" + r.useTexture + ";invert=" + r.invert + ";pattern=" + r.pattern +
                   ";scale=" + r.scale + ";depth=" + r.depth + ";brightness=" + r.brightness +
                   ";contrast=" + r.contrast + ";blend=" + r.blend;
        }
        function rbExpected(j) {
            if (!useTexOf(j)) { return "useTexture=false"; }
            return "useTexture=true;invert=" + invertOf(j) + ";pattern=" + job.texture_name +
                   ";scale=" + scaleOf(j) + ";depth=" + j.depth + ";brightness=" + j.brightness +
                   ";contrast=" + j.contrast + ";blend=" + j.mode_key;
        }
        function verifyRb(j, r) {
            if (j.mode_key === undefined) { return true; }
            if (!useTexOf(j)) { return r.useTexture === "false" || r.useTexture === "ABSENT"; }
            if (r.useTexture !== "true") { return false; }
            if (r.invert !== String(invertOf(j))) { return false; }
            if (r.pattern !== job.texture_name) { return false; }
            if (Math.abs(parseFloat(r.scale) - scaleOf(j)) > 1e-6) { return false; }
            if (Math.abs(parseFloat(r.depth) - j.depth) > 1e-6) { return false; }
            if (String(parseInt(r.brightness, 10)) !== String(j.brightness)) { return false; }
            if (String(parseInt(r.contrast, 10)) !== String(j.contrast)) { return false; }
            if (String(r.blend).toLowerCase() !== String(j.mode_key).toLowerCase()) { return false; }
            return true;
        }

        function paintOne() {
            var pts = [];
            for (var i = 0; i < 2; i++) {
                var p = new PathPointInfo();
                p.kind = PointKind.CORNERPOINT;
                p.anchor = [job.canvas.width / 2 + i * 0.4, job.canvas.height / 2 + i * 0.4];
                p.leftDirection = p.anchor;
                p.rightDirection = p.anchor;
                pts.push(p);
            }
            var sub = new SubPathInfo();
            sub.closed = false;
            sub.operation = ShapeOperation.SHAPEADD;
            sub.entireSubPath = pts;
            var pi = doc.pathItems.add("stamp", [sub]);
            pi.strokePath(ToolType.BRUSH);
        }
        function exportPng(path) {
            var po = new PNGSaveOptions();
            try { po.transparency = true; } catch (e) {}
            doc.saveAs(new File(path), po, true, Extension.LOWERCASE);
        }

        for (var i = 0; i < job.jobs.length; i++) {
            var j = job.jobs[i];
            var res = { file: j.file, ok: false, mode_read: "", error: "", readback: "", expected: "", verify: false };
            try {
                if (doc) { try { doc.close(SaveOptions.DONOTSAVECHANGES); } catch (e1) {} doc = null; }
                doc = app.documents.add(job.canvas.width, job.canvas.height, job.canvas.dpi,
                                        "bc_" + i, NewDocumentMode.RGB, DocumentFill.TRANSPARENT);
                applyBase(j);
                res.mode_read = applyMode(j);
                var rb = readBack();
                res.readback = rbString(rb);
                res.expected = rbExpected(j);
                res.verify = verifyRb(j, rb);
                paintOne();
                exportPng(job.out_dir + "/" + j.file);
                res.ok = true;
                L("OK " + j.file + " mode=" + res.mode_read + " verify=" + res.verify);
                L("READBACK " + j.file + " | " + res.readback);
            } catch (e) {
                res.error = e.message + " line " + e.line;
                L("FAIL " + j.file + " : " + res.error.replace(/\n/g, " "));
            }
            results.push(res);
        }
    } catch (e) {
        L("FATAL: " + e.message + " line " + e.line);
        results.push({ file: "", ok: false, mode_read: "", error: "FATAL " + e.message,
                       readback: "", expected: "", verify: false });
    } finally {
        try { if (doc) { doc.close(SaveOptions.DONOTSAVECHANGES); } } catch (e2) {}
        function cleanStr(s) {
            return String(s).replace(/"/g, "'").replace(/\\/g, "/").replace(/[\r\n]/g, " ");
        }
        try {
            var out = new File(donePath);
            out.encoding = "UTF-8";
            out.open("w");
            var arr = [];
            for (var k = 0; k < results.length; k++) {
                var r = results[k];
                arr.push('{"file":"' + cleanStr(r.file) + '","ok":' + r.ok +
                         ',"mode_read":"' + cleanStr(r.mode_read) + '","error":"' + cleanStr(r.error) +
                         '","readback":"' + cleanStr(r.readback) + '","expected":"' + cleanStr(r.expected) +
                         '","verify":' + r.verify + '}');
            }
            var brushJson = '{"abr_loaded":"' + cleanStr(brushInfo.abr_loaded) +
                            '","brush_selected":"' + cleanStr(brushInfo.brush_selected) +
                            '","diameter":"' + cleanStr(brushInfo.diameter) +
                            '","hardness":"' + cleanStr(brushInfo.hardness) +
                            '","spacing":"' + cleanStr(brushInfo.spacing) +
                            '","roundness":"' + cleanStr(brushInfo.roundness) + '"}';
            out.writeln('{"ps_version":"' + cleanStr(app.version) + '","brush":' + brushJson +
                        ',"results":[' + arr.join(",") + "]}");
            out.close();
        } catch (e3) {}
        logFile.close();
        try { app.quit(); } catch (e4) {}
    }
})();
