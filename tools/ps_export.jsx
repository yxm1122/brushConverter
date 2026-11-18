// tools/ps_export.jsx —— Photoshop 侧批量出图脚本（任务 05）
//
// 运行方式（由 tools/ps_probe.py 调用）：
//   "<LOCAL_APP_DIR>/PS2025/Adobe Photoshop 2025/Photoshop.exe" -r <REPO>/tools/ps_export.jsx
// 脚本从 tests/baselines/ps/_work/job.json 读任务清单，逐条：建 100x100 透明画布 →
// 选中画笔工具 → 写入纹理参数（图案/缩放/亮度/对比度/深度/混合模式）→ 用路径描边打
// 一个笔尖 → 导出 RGBA PNG。全部完成后写 _work/done.json 并退出 Photoshop。
//
// 关键实现说明（都来自实测，改动前先读 docs/PS真值自动化说明.md）：
//   1) 选画笔工具：Action Manager 里画笔工具的类名是 paintbrushTool（不是 brushTool / Brsh）。
//   2) 写工具选项：引用必须是 putClass(paintbrushTool) + putObject(cid("T   "), currentToolOptions)；
//      用 capp 引用会报「命令"设置"当前不可用」。
//   3) textureBlendMode / textureDepth 只有在 useTexture=true 之后才出现在描述符里，
//      故分两阶段写入：先启用纹理并写数值，再写混合模式。
//   4) 纹理图案按名字引用，PS 会自行解析，不需要图案 ID。
#target photoshop
(function () {
    // 仓库根：环境变量优先（BRUSHCONVERTER_REPO），其次按脚本自身位置推导（不写死本机绝对路径）
    var REPO = $.getenv("BRUSHCONVERTER_REPO");
    if (!REPO) { try { REPO = File($.fileName).parent.parent.fsName; } catch (e) { REPO = "."; } }
    var WORK = REPO + "/tests/baselines/ps/_work";
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

    var logFile = new File(WORK + "/batch.log");
    logFile.encoding = "UTF-8";
    logFile.open("w");
    function L(s) { logFile.writeln(String(s)); }

    var doc = null;
    var results = [];
    try {
        app.displayDialogs = DialogModes.NO;
        var jf = new File(jobPath);
        jf.encoding = "UTF-8";
        jf.open("r");
        var jobText = jf.read();
        jf.close();
        var job = eval("(" + jobText + ")");
        L("job loaded, count=" + job.jobs.length);

        var texDoc = app.open(new File(job.texture_path));
        L("texture opened " + texDoc.width + "x" + texDoc.height);
        var dp = new ActionDescriptor();
        dp.putString(cid("Nm  "), job.texture_name);
        executeAction(cid("DfnP"), dp, DialogModes.NO);
        texDoc.close(SaveOptions.DONOTSAVECHANGES);
        L("pattern defined: " + job.texture_name);

        var d0 = new ActionDescriptor();
        var r0 = new ActionReference();
        r0.putClass(sid("paintbrushTool"));
        d0.putReference(cid("null"), r0);
        executeAction(cid("slct"), d0, DialogModes.NO);
        var col = new SolidColor();
        col.rgb.red = 0; col.rgb.green = 0; col.rgb.blue = 0;
        app.foregroundColor = col;
        L("tool=" + app.currentTool + " foreground=black");

        function applyBase(j) {
            var o = getOpts();
            o.putBoolean(sid("useTexture"), true);
            var tp = new ActionDescriptor();
            tp.putString(sid("name"), job.texture_name);
            o.putObject(sid("texture"), sid("pattern"), tp);
            o.putUnitDouble(sid("textureScale"), cid("#Prc"), job.texture_scale);
            o.putInteger(sid("textureBrightness"), j.brightness);
            o.putInteger(sid("textureContrast"), j.contrast);
            o.putUnitDouble(sid("textureDepth"), cid("#Prc"), j.depth);
            var bd = o.getObjectValue(sid("brush"));
            bd.putUnitDouble(sid("diameter"), cid("#Pxl"), job.brush.diameter);
            bd.putUnitDouble(sid("hardness"), cid("#Prc"), job.brush.hardness);
            bd.putUnitDouble(sid("spacing"), cid("#Prc"), job.brush.spacing);
            o.putObject(sid("brush"), sid("computedBrush"), bd);
            o.putInteger(sid("opacity"), 100);
            o.putInteger(sid("flow"), 100);
            o.putBoolean(sid("useTipDynamics"), false);
            o.putBoolean(sid("useScatter"), false);
            o.putBoolean(sid("smoothing"), false);
            setOpts(o);
        }
        // 二阶段：写纹理混合模式（此时该键才存在）。
        // 模式值有两种写法：charID（Sbtr/Mltp/Hght 等）与 stringID（linearHeight）。
        // 实测「线性高度」只有 stringID 形式生效；charID 候选 LnHt/LnHg/LinH/LHgt/HgtL 等全部被忽略。
        function applyMode(j) {
            var o = getOpts();
            var typeId;
            try { typeId = o.getEnumerationType(sid("textureBlendMode")); }
            catch (e) { typeId = sid("blendMode"); }
            var valueId = (j.mode_kind === "str") ? sid(j.mode_value) : cid(j.mode_value);
            o.putEnumerated(sid("textureBlendMode"), typeId, valueId);
            setOpts(o);
            return keyName(getOpts().getEnumerationValue(sid("textureBlendMode")));
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
            var res = { file: j.file, ok: false, mode_read: "", error: "" };
            try {
                if (doc) { try { doc.close(SaveOptions.DONOTSAVECHANGES); } catch (e1) {} doc = null; }
                doc = app.documents.add(job.canvas.width, job.canvas.height, job.canvas.dpi,
                                        "bc_" + i, NewDocumentMode.RGB, DocumentFill.TRANSPARENT);
                applyBase(j);
                res.mode_read = applyMode(j);
                paintOne();
                exportPng(job.out_dir + "/" + j.file);
                res.ok = true;
                L("OK " + j.file + " mode=" + res.mode_read);
            } catch (e) {
                res.error = e.message + " line " + e.line;
                L("FAIL " + j.file + " : " + res.error.replace(/\n/g, " "));
            }
            results.push(res);
        }
    } catch (e) {
        L("FATAL: " + e.message + " line " + e.line);
        results.push({ file: "", ok: false, mode_read: "", error: "FATAL " + e.message });
    } finally {
        try { if (doc) { doc.close(SaveOptions.DONOTSAVECHANGES); } } catch (e2) {}
        try {
            var out = new File(donePath);
            out.encoding = "UTF-8";
            out.open("w");
            var arr = [];
            for (var k = 0; k < results.length; k++) {
                var r = results[k];
                arr.push('{"file":"' + r.file + '","ok":' + r.ok + ',"mode_read":"' + r.mode_read +
                         '","error":"' + String(r.error).replace(/"/g, "'").replace(/\\/g, "/") + '"}');
            }
            out.writeln('{"results":[' + arr.join(",") + "]}");
            out.close();
        } catch (e3) {}
        logFile.close();
        try { app.quit(); } catch (e4) {}
    }
})();
