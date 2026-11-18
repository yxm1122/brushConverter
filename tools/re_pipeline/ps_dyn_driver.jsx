// tools/re_pipeline/ps_dyn_driver.jsx
// 任务 11A 动态追踪 —— Photoshop 侧「等待-执行」实验驱动
//
// 运行方式（由外部 shell 启动，见 docs/研究报告/ps-逆向-动态.md §2）：
//   "<LOCAL_APP_DIR>/PS2025/Adobe Photoshop 2025/Photoshop.exe" -r <REPO>/tools/re_pipeline/ps_dyn_driver.jsx
//
// 设计要点：
//   1) 启动后先写 ready.txt，再轮询等待 go.flag（最多 180 秒）。外部插桩方（REA/Frida）
//      可以先完成附身、下钩，然后由插桩脚本创建 go.flag 放行实验 —— 保证「先插桩、后改参数」。
//   2) 每一步先把意图写进 step.txt（phase=begin），再写参数、读回真实值、打一笔（触发纹理像素合成），
//      之后写 step.txt（phase=painted）。插桩方每次命中都会带上当时 step.txt 的内容，
//      从而实现「第几步操作 ↔ 观测到的寄存器/内存值」逐条对齐。
//   3) 每条子操作单独 try/catch 并记录「消息 + 行号」，出问题能一眼定位是哪一步的哪个调用。
//   4) 只读纪律：不改 PS 安装目录、不写注册表、不改持久设置；参数只改本进程的工具选项，
//      进程退出即失效。
//   5) 复用 tools/ps_export.jsx 已验证的「putClass(paintbrushTool)+putObject(currentToolOptions)」写法。
#target photoshop

(function () {
    // 仓库根 / 工作目录：环境变量优先（BRUSHCONVERTER_REPO / BC_DYN_WORK），默认值不含身份信息
    var REPO = $.getenv("BRUSHCONVERTER_REPO");
    if (!REPO) { try { REPO = File($.fileName).parent.parent.parent.fsName; } catch (e) { REPO = "."; } }
    var WORK = $.getenv("BC_DYN_WORK") || "C:/Windows/Temp/bcdyn";
    var TEXTURE = REPO + "/tests/baselines/ps/_work/texture_noise64.png";
    var TEXTURE_NAME = "bc_texture_noise64_dyn";

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

    // ---- 目录与日志 ----
    var wf = new Folder(WORK);
    if (!wf.exists) { wf.create(); }
    var LOGF = new File(WORK + "/driver.log");
    LOGF.encoding = "UTF-8";
    LOGF.open("w");
    function L(s) {
        LOGF.writeln(String(s));
        try { LOGF.close(); LOGF.open("a"); } catch (e) {}
    }
    function writeFile(name, text) {
        var f = new File(WORK + "/" + name);
        f.encoding = "UTF-8";
        f.open("w"); f.writeln(text); f.close();
    }
    // 子操作探针：成功/失败都记一条，失败带消息与行号
    function attempt(tag, fn, verbose) {
        try { var v = fn(); if (verbose) { L("  ok   " + tag); } return { ok: true, v: v }; }
        catch (e) { L("  FAIL " + tag + " : " + e.message + " line " + e.line); return { ok: false, err: String(e.message) }; }
    }

    // ---- 实验步骤表（唯一真相来源；改这里就等于改实验）----
    var STEPS = [
        { id: 1,  b:   0, c:   0, d: 100, m: "Mltp", kind: "char", note: "基线：亮度0 对比度0 深度100 正片叠底" },
        { id: 2,  b:  50, c:   0, d: 100, m: "Mltp", kind: "char", note: "只改亮度 +50" },
        { id: 3,  b: -50, c:   0, d: 100, m: "Mltp", kind: "char", note: "只改亮度 -50" },
        { id: 4,  b: 150, c:   0, d: 100, m: "Mltp", kind: "char", note: "亮度上极值 +150" },
        { id: 5,  b:-150, c:   0, d: 100, m: "Mltp", kind: "char", note: "亮度下极值 -150" },
        { id: 6,  b:   0, c:  50, d: 100, m: "Mltp", kind: "char", note: "只改对比度 +50" },
        { id: 7,  b:   0, c: -50, d: 100, m: "Mltp", kind: "char", note: "只改对比度 -50" },
        { id: 8,  b:   0, c: 100, d: 100, m: "Mltp", kind: "char", note: "对比度上极值 +100" },
        { id: 9,  b:   0, c:-100, d: 100, m: "Mltp", kind: "char", note: "对比度下极值 -100" },
        { id: 10, b:   0, c:   0, d:  60, m: "Mltp", kind: "char", note: "只改深度 100->60（验证 cl>0 clamp 与深度分支）" },
        { id: 11, b:   0, c:   0, d: 101, m: "Mltp", kind: "char", note: "深度 101 越界（静态预测被 clamp 到 100）" },
        { id: 12, b:   0, c:   0, d: 100, m: "linearHeight", kind: "str", note: "模式改线性高度（stringID 形式）" },
        { id: 13, b:   0, c:   0, d: 100, m: "Hght", kind: "char", note: "模式改高度（charID Hght）" },
        { id: 14, b:   0, c:   0, d: 100, m: "Sbtr", kind: "char", note: "模式改减去（charID Sbtr）" }
    ];

    function applyBase(st, verbose) {
        var r1 = attempt("getOpts", function () { return getOpts(); }, verbose);
        if (!r1.ok) { return false; }
        var o = r1.v;
        attempt("putBoolean useTexture", function () { o.putBoolean(sid("useTexture"), true); }, verbose);
        attempt("putObject texture/pattern", function () {
            var tp = new ActionDescriptor();
            tp.putString(sid("name"), TEXTURE_NAME);
            o.putObject(sid("texture"), sid("pattern"), tp);
        }, verbose);
        attempt("putUnitDouble textureScale", function () { o.putUnitDouble(sid("textureScale"), cid("#Prc"), 100); }, verbose);
        attempt("putInteger textureBrightness", function () { o.putInteger(sid("textureBrightness"), st.b); }, verbose);
        attempt("putInteger textureContrast", function () { o.putInteger(sid("textureContrast"), st.c); }, verbose);
        attempt("putUnitDouble textureDepth", function () { o.putUnitDouble(sid("textureDepth"), cid("#Prc"), st.d); }, verbose);
        attempt("brush object", function () {
            var bd = o.getObjectValue(sid("brush"));
            bd.putUnitDouble(sid("diameter"), cid("#Pxl"), 300);
            bd.putUnitDouble(sid("hardness"), cid("#Prc"), 100);
            bd.putUnitDouble(sid("spacing"), cid("#Prc"), 1000);
            o.putObject(sid("brush"), sid("computedBrush"), bd);
        }, verbose);
        attempt("putInteger opacity/flow", function () { o.putInteger(sid("opacity"), 100); o.putInteger(sid("flow"), 100); }, verbose);
        attempt("putBoolean switches", function () {
            o.putBoolean(sid("useTipDynamics"), false);
            o.putBoolean(sid("useScatter"), false);
            o.putBoolean(sid("smoothing"), false);
        }, verbose);
        var r2 = attempt("setOpts (executeAction setd)", function () { setOpts(o); }, verbose);
        return r2.ok;
    }

    function applyMode(st, verbose) {
        var r1 = attempt("getOpts(mode)", function () { return getOpts(); }, verbose);
        if (!r1.ok) { return null; }
        var o = r1.v;
        var typeId;
        try { typeId = o.getEnumerationType(sid("textureBlendMode")); }
        catch (e) { typeId = sid("blendMode"); }
        var valueId = (st.kind === "str") ? sid(st.m) : cid(st.m);
        var r2 = attempt("putEnumerated textureBlendMode=" + st.m, function () {
            o.putEnumerated(sid("textureBlendMode"), typeId, valueId);
        }, verbose);
        if (!r2.ok) { return null; }
        attempt("setOpts(mode)", function () { setOpts(o); }, verbose);
        var r3 = attempt("readback mode", function () {
            return keyName(getOpts().getEnumerationValue(sid("textureBlendMode")));
        }, verbose);
        return r3.ok ? r3.v : null;
    }

    // 稳健取数：同一键在不同 PS 状态下可能是单位浮点 / 双精度 / 整数，逐种尝试
    function numOf(o, key) {
        try { return o.getUnitDoubleValue(key); } catch (e1) {}
        try { return o.getDouble(key); } catch (e2) {}
        try { return o.getInteger(key); } catch (e3) {}
        return null;
    }
    function readBack(verbose) {
        var r1 = attempt("getOpts(rb)", function () { return getOpts(); }, false);
        var r = { b: null, c: null, d: null, mode: "", useTex: null, scale: null };
        if (!r1.ok) { return r; }
        var o = r1.v;
        var a = attempt("rb brightness", function () { return o.getInteger(sid("textureBrightness")); }, verbose);
        if (a.ok) { r.b = a.v; }
        var b = attempt("rb contrast", function () { return o.getInteger(sid("textureContrast")); }, verbose);
        if (b.ok) { r.c = b.v; }
        r.d = numOf(o, sid("textureDepth"));
        r.scale = numOf(o, sid("textureScale"));
        var e = attempt("rb useTexture", function () { return o.getBoolean(sid("useTexture")); }, verbose);
        if (e.ok) { r.useTex = e.v; }
        var f = attempt("rb mode", function () { return keyName(o.getEnumerationValue(sid("textureBlendMode"))); }, verbose);
        if (f.ok) { r.mode = f.v; }
        return r;
    }

    function paint(tag) {
        var pts = [];
        for (var k = 0; k < 2; k++) {
            var p = new PathPointInfo();
            p.kind = PointKind.CORNERPOINT;
            p.anchor = [50 + k * 0.4, 50 + k * 0.4];
            p.leftDirection = p.anchor;
            p.rightDirection = p.anchor;
            pts.push(p);
        }
        var sub = new SubPathInfo();
        sub.closed = false;
        sub.operation = ShapeOperation.SHAPEADD;
        sub.entireSubPath = pts;
        var pi = doc.pathItems.add(tag, [sub]);
        pi.strokePath(ToolType.BRUSH);
        try { doc.selection.deselect(); } catch (e) {}
    }

    var doc = null;
    try {
        app.displayDialogs = DialogModes.NO;
        L("driver start");
        // 0) 先逐条验证 charID 是否合法（这是上一轮「非法参数 line 25」的定位手段）
        var ids = ["capp", "Ordn", "Trgt", "null", "T   ", "Nm  ", "#Prc", "#Pxl", "setd", "slct", "DfnP"];
        for (var q = 0; q < ids.length; q++) {
            attempt("charIDToTypeID(\"" + ids[q] + "\")", (function (x) {
                return function () { return charIDToTypeID(x); };
            })(ids[q]), true);
        }
        var sids = ["paintbrushTool", "currentToolOptions", "useTexture", "texture", "pattern", "name",
                    "textureScale", "textureBrightness", "textureContrast", "textureDepth",
                    "brush", "computedBrush", "diameter", "hardness", "spacing",
                    "opacity", "flow", "useTipDynamics", "useScatter", "smoothing",
                    "textureBlendMode", "blendMode"];
        for (var q2 = 0; q2 < sids.length; q2++) {
            attempt("stringIDToTypeID(\"" + sids[q2] + "\")", (function (x) {
                return function () { return stringIDToTypeID(x); };
            })(sids[q2]), true);
        }
        // 1) 纹理图案
        var texDoc = app.open(new File(TEXTURE));
        L("texture opened " + texDoc.width + "x" + texDoc.height);
        var dp = new ActionDescriptor();
        dp.putString(cid("Nm  "), TEXTURE_NAME);
        executeAction(cid("DfnP"), dp, DialogModes.NO);
        texDoc.close(SaveOptions.DONOTSAVECHANGES);
        L("pattern defined: " + TEXTURE_NAME);
        // 2) 选画笔工具、黑色前景
        var d0 = new ActionDescriptor();
        var r0 = new ActionReference();
        r0.putClass(sid("paintbrushTool"));
        d0.putReference(cid("null"), r0);
        executeAction(cid("slct"), d0, DialogModes.NO);
        var col = new SolidColor();
        col.rgb.red = 0; col.rgb.green = 0; col.rgb.blue = 0;
        app.foregroundColor = col;
        L("tool=" + app.currentTool);

        // 3) 通知外部已就绪，等 go.flag
        writeFile("ready.txt", "ready " + (new Date()).toString());
        L("waiting for go.flag ...");
        var waited = 0;
        while (waited < 180000) {
            if (new File(WORK + "/go.flag").exists) { break; }
            $.sleep(250); waited += 250;
        }
        L("go after " + waited + " ms");

        // 4) 逐步骤执行
        for (var i = 0; i < STEPS.length; i++) {
            var st = STEPS[i];
            var tag = "step" + (st.id < 10 ? "0" : "") + st.id;
            var verbose = (i === 0);
            writeFile("step.txt", tag + "|phase=begin|b=" + st.b + "|c=" + st.c + "|d=" + st.d + "|m=" + st.m);
            L(tag + " " + st.note);
            try {
                if (doc) { try { doc.close(SaveOptions.DONOTSAVECHANGES); } catch (e1) {} doc = null; }
                attempt("documents.add", function () {
                    doc = app.documents.add(100, 100, 72, "bcdyn_" + st.id, NewDocumentMode.RGB, DocumentFill.TRANSPARENT);
                }, verbose);
                var baseOk = applyBase(st, verbose);
                var modeRead = applyMode(st, verbose);
                var rb = readBack(verbose);
                attempt("paint#1", function () { paint("stamp_" + st.id); }, verbose);
                attempt("paint#2", function () { paint("stamp2_" + st.id); }, verbose);
                L(tag + " readback b=" + rb.b + " c=" + rb.c + " d=" + rb.d +
                  " scale=" + rb.scale + " useTex=" + rb.useTex +
                  " mode=" + rb.mode + " modeRead=" + modeRead + " baseOk=" + baseOk);
                writeFile("step.txt", tag + "|phase=painted|b=" + st.b + "|c=" + st.c +
                          "|d=" + st.d + "|m=" + st.m + "|rb_b=" + rb.b + "|rb_c=" + rb.c +
                          "|rb_d=" + rb.d + "|rb_mode=" + rb.mode);
            } catch (e) {
                L(tag + " OUTER FAIL " + e.message + " line " + e.line);
            }
            $.sleep(2500);
        }
        writeFile("done.txt", "done steps=" + STEPS.length);
        L("ALL DONE");
    } catch (e) {
        L("FATAL: " + e.message + " line " + e.line);
        writeFile("done.txt", "fatal " + e.message);
    } finally {
        try { if (doc) { doc.close(SaveOptions.DONOTSAVECHANGES); } } catch (e2) {}
        try { LOGF.close(); } catch (e3) {}
        $.sleep(2000);
        try { app.quit(); } catch (e4) {}
    }
})();
