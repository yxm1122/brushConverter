// 探测 15：单笔尖盖章出图 + 带 Alpha 导出 + 线性高度枚举名寻找
#target photoshop
(function () {
    var logPath = "<REPO>/tests/baselines/ps/_work/probe15.log";
    var f = new File(logPath);
    f.encoding = "UTF-8"; f.open("w");
    function L(s) { f.writeln(String(s)); }
    var doc = null, texDoc = null;
    function cid(s) { return charIDToTypeID(s); }
    function sid(s) { return stringIDToTypeID(s); }
    function keyName(k) { try { var s = typeIDToStringID(k); if (s && s.length) return s; } catch (e) {} try { return typeIDToCharID(k); } catch (e) { return "?" + k; } }
    function appDesc() { var r = new ActionReference(); r.putEnumerated(cid("capp"), cid("Ordn"), cid("Trgt")); return executeActionGet(r); }
    function getOpts() { return appDesc().getObjectValue(sid("currentToolOptions")); }
    function setOpts(o) {
        var d = new ActionDescriptor(); var r = new ActionReference();
        r.putClass(sid("paintbrushTool")); d.putReference(cid("null"), r);
        d.putObject(cid("T   "), sid("currentToolOptions"), o);
        executeAction(cid("setd"), d, DialogModes.NO);
    }
    function setMode(modeIdOrName, isStringId) {
        var o = getOpts();
        var typeId = o.getEnumerationType(sid("textureBlendMode"));
        var val = isStringId ? sid(modeIdOrName) : cid(modeIdOrName);
        o.putEnumerated(sid("textureBlendMode"), typeId, val);
        setOpts(o);
        return keyName(getOpts().getEnumerationValue(sid("textureBlendMode")));
    }
    function baseOptions(modeSetter) {
        var o = getOpts();
        o.putBoolean(sid("useTexture"), true);
        var tp = new ActionDescriptor(); tp.putString(sid("name"), "bc_texture_noise64");
        o.putObject(sid("texture"), sid("pattern"), tp);
        o.putUnitDouble(sid("textureScale"), cid("#Prc"), 100);
        o.putInteger(sid("textureBrightness"), 0);
        o.putInteger(sid("textureContrast"), 0);
        o.putUnitDouble(sid("textureDepth"), cid("#Prc"), 100);
        var bd = o.getObjectValue(sid("brush"));
        bd.putUnitDouble(sid("diameter"), cid("#Pxl"), 300);
        bd.putUnitDouble(sid("hardness"), cid("#Prc"), 100);
        bd.putUnitDouble(sid("spacing"), cid("#Prc"), 1000);
        o.putObject(sid("brush"), sid("computedBrush"), bd);
        o.putInteger(sid("opacity"), 100);
        o.putInteger(sid("flow"), 100);
        o.putBoolean(sid("useTipDynamics"), false);
        o.putBoolean(sid("useScatter"), false);
        o.putBoolean(sid("smoothing"), false);
        return o;
    }
    function paintOne() {
        var pts = [];
        var p1 = new PathPointInfo(); p1.kind = PointKind.CORNERPOINT; p1.anchor = [50, 50]; p1.leftDirection = [50, 50]; p1.rightDirection = [50, 50];
        var p2 = new PathPointInfo(); p2.kind = PointKind.CORNERPOINT; p2.anchor = [50.4, 50.4]; p2.leftDirection = [50.4, 50.4]; p2.rightDirection = [50.4, 50.4];
        pts.push(p1); pts.push(p2);
        var sub = new SubPathInfo(); sub.closed = false; sub.operation = ShapeOperation.SHAPEADD; sub.entireSubPath = pts;
        var pi = doc.pathItems.add("stamp", [sub]);
        pi.strokePath(ToolType.BRUSH);
    }
    function exportPng(path) {
        try {
            var po = new PNGSaveOptions();
            try { po.transparency = true; L("set po.transparency=true OK"); } catch (e) { L("po.transparency ERR " + e.message); }
            doc.saveAs(new File(path), po, true, Extension.LOWERCASE);
            L("export saveAs -> " + path);
            return;
        } catch (e) { L("saveAs ERR " + e.message); }
        try {
            var eo = new ExportOptionsSaveForWeb();
            eo.format = SaveDocumentType.PNG;
            eo.PNG8 = false;
            eo.transparency = true;
            doc.exportDocument(new File(path), ExportType.SAVEFORWEB, eo);
            L("export saveForWeb -> " + path);
        } catch (e2) { L("saveForWeb ERR " + e2.message); }
    }
    try {
        app.displayDialogs = DialogModes.NO;
        texDoc = app.open(new File("<REPO>/tests/baselines/ps/_work/texture_noise64.png"));
        var dp = new ActionDescriptor(); dp.putString(cid("Nm  "), "bc_texture_noise64");
        executeAction(cid("DfnP"), dp, DialogModes.NO);
        texDoc.close(SaveOptions.DONOTSAVECHANGES); texDoc = null;
        doc = app.documents.add(100, 100, 72, "probe15", NewDocumentMode.RGB, DocumentFill.TRANSPARENT);
        var d0 = new ActionDescriptor(); var r0 = new ActionReference();
        r0.putClass(sid("paintbrushTool")); d0.putReference(cid("null"), r0);
        executeAction(cid("slct"), d0, DialogModes.NO);
        // 前景色纯黑，保证 RGB 与历史真值图一致
        var col = new SolidColor(); col.rgb.red = 0; col.rgb.green = 0; col.rgb.blue = 0;
        app.foregroundColor = col;
        // 各模式枚举候选
        L("== 枚举候选 ==");
        var cands = [
            ["char", "Sbtr"], ["char", "Mltp"], ["char", "Hght"], ["char", "LnHt"], ["char", "LnHg"],
            ["char", "LHgt"], ["char", "LinH"], ["char", "HgtL"],
            ["str", "subtract"], ["str", "multiply"], ["str", "height"], ["str", "linearHeight"], ["str", "lighten"]
        ];
        for (var i = 0; i < cands.length; i++) {
            try { L("cand " + cands[i][1] + " (" + cands[i][0] + ") -> " + setMode(cands[i][1], cands[i][0] === "str")); }
            catch (e) { L("cand " + cands[i][1] + " ERR " + e.message.replace(/\n/g, " ")); }
        }
        // 出图：减去 d100 b0 c0
        setMode("Sbtr", false);
        var o1 = baseOptions();
        try { setOpts(o1); L("options applied (subtract d100)"); } catch (e) { L("setOpts ERR " + e.message.replace(/\n/g, " ")); }
        try { paintOne(); L("paintOne OK"); } catch (e) { L("paintOne ERR " + e.message); }
        exportPng("<REPO>/tests/baselines/ps/_work/probe15_subtract.png");
        L("PROBE15-OK");
    } catch (e) {
        L("PROBE15-EXCEPTION: " + e.message + " line " + e.line);
    } finally {
        f.close();
        try { if (texDoc) { texDoc.close(SaveOptions.DONOTSAVECHANGES); } } catch (e2) {}
        try { if (doc) { doc.close(SaveOptions.DONOTSAVECHANGES); } } catch (e3) {}
        try { app.quit(); } catch (e4) {}
    }
})();
