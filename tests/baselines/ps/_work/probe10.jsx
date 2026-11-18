// 探测 10：定义自制纹理图案 -> 写入画笔纹理参数 -> 路径描边 -> 导出 PNG
#target photoshop
(function () {
    var logPath = "<REPO>/tests/baselines/ps/_work/probe10.log";
    var f = new File(logPath);
    f.encoding = "UTF-8"; f.open("w");
    function L(s) { f.writeln(String(s)); }
    var paintDoc = null, texDoc = null;
    function cid(s) { return charIDToTypeID(s); }
    function sid(s) { return stringIDToTypeID(s); }
    function keyName(k) {
        try { var s = typeIDToStringID(k); if (s && s.length) { return s; } } catch (e) {}
        try { return typeIDToCharID(k); } catch (e) { return "?" + k; }
    }
    function getOptions() {
        var r = new ActionReference(); r.putEnumerated(cid("capp"), cid("Ordn"), cid("Trgt"));
        return executeActionGet(r).getObjectValue(sid("currentToolOptions"));
    }
    function applyOptions(opts) {
        var d = new ActionDescriptor();
        var r = new ActionReference(); r.putEnumerated(cid("capp"), cid("Ordn"), cid("Trgt"));
        d.putReference(cid("null"), r);
        d.putObject(sid("to"), sid("currentToolOptions"), opts);
        executeAction(cid("setd"), d, DialogModes.NO);
    }
    function dumpKeys(opts, label) {
        var n = opts.count, names = [];
        for (var i = 0; i < n; i++) { names.push(keyName(opts.getKey(i))); }
        L(label + " keys(" + n + ")=" + names.join(","));
    }
    try {
        app.displayDialogs = DialogModes.NO;
        // 1) 打开自制纹理，定义为图案
        texDoc = app.open(new File("<REPO>/tests/baselines/ps/_work/texture_noise64.png"));
        L("texDoc opened " + texDoc.width + "x" + texDoc.height);
        try {
            var dp = new ActionDescriptor();
            dp.putString(cid("Nm  "), "bc_texture_noise64");
            executeAction(cid("DfnP"), dp, DialogModes.NO);
            L("definePattern OK");
        } catch (e) { L("definePattern ERR " + e.message); }
        // 读当前图案描述符
        var patName = "", patID = "";
        try {
            var pr = new ActionReference();
            pr.putClass(sid("pattern"));
            pr.putEnumerated(cid("Ordn"), cid("Trgt"));
            var pd = executeActionGet(pr);
            for (var i = 0; i < pd.count; i++) {
                var k = pd.getKey(i), kn = keyName(k);
                if (pd.getType(k) == DescValueType.STRINGTYPE) {
                    L("pattern." + kn + " = " + pd.getString(k));
                    if (kn == "name") { patName = pd.getString(k); }
                    if (kn == "ID") { patID = pd.getString(k); }
                }
            }
        } catch (e) { L("get pattern ERR " + e.message); }
        texDoc.close(SaveOptions.DONOTSAVECHANGES); texDoc = null;
        // 2) 新建画布并选中画笔
        paintDoc = app.documents.add(100, 100, 72, "probe10", NewDocumentMode.RGB, DocumentFill.TRANSPARENT);
        var d0 = new ActionDescriptor(); var r0 = new ActionReference();
        r0.putClass(sid("paintbrushTool")); d0.putReference(cid("null"), r0);
        executeAction(cid("slct"), d0, DialogModes.NO);
        L("tool=" + app.currentTool);
        // 3) 读选项并写入纹理设置
        var opts = getOptions();
        dumpKeys(opts, "BEFORE");
        opts.putBoolean(sid("useTexture"), true);
        opts.putInteger(sid("textureBrightness"), -25);
        opts.putInteger(sid("textureContrast"), 25);
        opts.putBoolean(sid("invertTexture"), false);
        opts.putBoolean(sid("protectTexture"), false);
        opts.putUnitDouble(sid("textureScale"), cid("#Prc"), 100);
        if (patName) {
            var tp = new ActionDescriptor();
            tp.putString(sid("name"), patName);
            tp.putString(sid("ID"), patID);
            opts.putObject(sid("texture"), sid("pattern"), tp);
            L("texture object set to " + patName + " / " + patID);
        }
        // 画笔实体参数：大直径、硬边、无间距、无动态、不透明度 100
        try {
            var bd = opts.getObjectValue(sid("brush"));
            bd.putUnitDouble(sid("diameter"), cid("#Pxl"), 200);
            bd.putUnitDouble(sid("hardness"), cid("#Prc"), 100);
            bd.putUnitDouble(sid("spacing"), cid("#Prc"), 1);
            opts.putObject(sid("brush"), sid("computedBrush"), bd);
            L("brush params set");
        } catch (e) { L("brush params ERR " + e.message); }
        opts.putInteger(sid("opacity"), 100);
        opts.putInteger(sid("flow"), 100);
        opts.putBoolean(sid("useTipDynamics"), false);
        opts.putBoolean(sid("useScatter"), false);
        opts.putBoolean(sid("smoothing"), false);
        try {
            applyOptions(opts);
            L("applyOptions OK");
        } catch (e) { L("applyOptions ERR " + e.message); }
        // 4) 读回
        try {
            var opts2 = getOptions();
            dumpKeys(opts2, "AFTER");
            L("after.useTexture=" + opts2.getBoolean(sid("useTexture")));
            L("after.textureBrightness=" + opts2.getInteger(sid("textureBrightness")));
            L("after.textureContrast=" + opts2.getInteger(sid("textureContrast")));
            L("after.textureScale=" + opts2.getUnitDoubleValue(sid("textureScale")));
            var bd2 = opts2.getObjectValue(sid("brush"));
            L("after.brush.diameter=" + bd2.getUnitDoubleValue(sid("diameter")) + " hardness=" + bd2.getUnitDoubleValue(sid("hardness")));
            var tp2 = opts2.getObjectValue(sid("texture"));
            L("after.texture.name=" + tp2.getString(sid("name")));
        } catch (e) { L("readback ERR " + e.message); }
        // 5) 路径描边 + 导出
        try {
            var pts = [];
            for (var j = 0; j < 2; j++) {
                var p = new PathPointInfo();
                p.kind = PointKind.CORNERPOINT;
                p.anchor = [50, j * 99];
                p.leftDirection = [50, j * 99];
                p.rightDirection = [50, j * 99];
                pts.push(p);
            }
            var sub = new SubPathInfo();
            sub.closed = false; sub.operation = ShapeOperation.SHAPEADD; sub.entireSubPath = pts;
            var pi = paintDoc.pathItems.add("strokeTest", [sub]);
            pi.strokePath(ToolType.BRUSH);
            L("strokePath OK");
        } catch (e) { L("strokePath ERR " + e.message + " line " + e.line); }
        try {
            var pngOpts = new PNGSaveOptions();
            paintDoc.saveAs(new File("<REPO>/tests/baselines/ps/_work/probe10_out.png"), pngOpts, true, Extension.LOWERCASE);
            L("export OK");
        } catch (e) { L("export ERR " + e.message); }
        L("PROBE10-OK");
    } catch (e) {
        L("PROBE10-EXCEPTION: " + e.message + " line " + e.line);
    } finally {
        f.close();
        try { if (texDoc) { texDoc.close(SaveOptions.DONOTSAVECHANGES); } } catch (e2) {}
        try { if (paintDoc) { paintDoc.close(SaveOptions.DONOTSAVECHANGES); } } catch (e3) {}
        try { app.quit(); } catch (e4) {}
    }
})();
