// 探测 13：用正确写法（工具类引用）写入画笔纹理参数并出图，同时确认纹理模式/深度键是否存在
#target photoshop
(function () {
    var logPath = "<REPO>/tests/baselines/ps/_work/probe13.log";
    var f = new File(logPath);
    f.encoding = "UTF-8"; f.open("w");
    function L(s) { f.writeln(String(s)); }
    var doc = null, texDoc = null;
    function cid(s) { return charIDToTypeID(s); }
    function sid(s) { return stringIDToTypeID(s); }
    function keyName(k) { try { var s = typeIDToStringID(k); if (s && s.length) return s; } catch (e) {} try { return typeIDToCharID(k); } catch (e) { return "?" + k; } }
    function appDesc() { var r = new ActionReference(); r.putEnumerated(cid("capp"), cid("Ordn"), cid("Trgt")); return executeActionGet(r); }
    function getOpts() { return appDesc().getObjectValue(sid("currentToolOptions")); }
    function setOpts(opts) {
        var d = new ActionDescriptor(); var r = new ActionReference();
        r.putClass(sid("paintbrushTool"));
        d.putReference(cid("null"), r);
        d.putObject(cid("T   "), sid("currentToolOptions"), opts);
        executeAction(cid("setd"), d, DialogModes.NO);
    }
    function quick(tag) {
        try {
            var o = getOpts();
            var b = o.getObjectValue(sid("brush"));
            var t = o.getObjectValue(sid("texture"));
            L(tag + " useTex=" + o.getBoolean(sid("useTexture")) + " b=" + o.getInteger(sid("textureBrightness")) +
              " c=" + o.getInteger(sid("textureContrast")) + " scale=" + o.getUnitDoubleValue(sid("textureScale")) +
              " dia=" + b.getUnitDoubleValue(sid("diameter")) + " hard=" + b.getUnitDoubleValue(sid("hardness")) +
              " tex=" + t.getString(sid("name")));
        } catch (e) { L(tag + " quick ERR " + e.message); }
    }
    try {
        app.displayDialogs = DialogModes.NO;
        // 1) 定义图案并取 ID
        texDoc = app.open(new File("<REPO>/tests/baselines/ps/_work/texture_noise64.png"));
        var dp = new ActionDescriptor(); dp.putString(cid("Nm  "), "bc_texture_noise64");
        executeAction(cid("DfnP"), dp, DialogModes.NO);
        texDoc.close(SaveOptions.DONOTSAVECHANGES); texDoc = null;
        var patID = "";
        var tries = [
            ["Ptrn-name", function () { var r = new ActionReference(); r.putName(cid("Ptrn"), "bc_texture_noise64"); return r; }],
            ["Ptrn-OrdnTrgt", function () { var r = new ActionReference(); r.putEnumerated(cid("Ptrn"), cid("Ordn"), cid("Trgt")); return r; }],
            ["Ptrn-class-OrdnTrgt", function () { var r = new ActionReference(); r.putClass(cid("Ptrn")); r.putEnumerated(cid("Ordn"), cid("Trgt")); return r; }]
        ];
        for (var i = 0; i < tries.length; i++) {
            try {
                var pd = executeActionGet(tries[i][1]());
                var s = "";
                for (var j = 0; j < pd.count; j++) { var k = pd.getKey(j); if (pd.getType(k) == DescValueType.STRINGTYPE) { s += keyName(k) + "=" + pd.getString(k) + " "; if (keyName(k) == "ID") { patID = pd.getString(k); } } }
                L("pattern " + tries[i][0] + " OK: " + s);
            } catch (e) { L("pattern " + tries[i][0] + " ERR " + e.message.replace(/\n/g, " ")); }
        }
        // 2) 画布 + 画笔
        doc = app.documents.add(100, 100, 72, "probe13", NewDocumentMode.RGB, DocumentFill.TRANSPARENT);
        var d0 = new ActionDescriptor(); var r0 = new ActionReference();
        r0.putClass(sid("paintbrushTool")); d0.putReference(cid("null"), r0);
        executeAction(cid("slct"), d0, DialogModes.NO);
        quick("baseline");
        // 3) 写入纹理参数
        var opts = getOpts();
        opts.putBoolean(sid("useTexture"), true);
        opts.putInteger(sid("textureBrightness"), 0);
        opts.putInteger(sid("textureContrast"), 0);
        opts.putUnitDouble(sid("textureScale"), cid("#Prc"), 100);
        opts.putBoolean(sid("invertTexture"), false);
        opts.putBoolean(sid("protectTexture"), false);
        var tp = new ActionDescriptor();
        tp.putString(sid("name"), "bc_texture_noise64");
        if (patID) { tp.putString(sid("ID"), patID); L("using patID=" + patID); }
        opts.putObject(sid("texture"), sid("pattern"), tp);
        var bd = opts.getObjectValue(sid("brush"));
        bd.putUnitDouble(sid("diameter"), cid("#Pxl"), 200);
        bd.putUnitDouble(sid("hardness"), cid("#Prc"), 100);
        bd.putUnitDouble(sid("spacing"), cid("#Prc"), 1);
        opts.putObject(sid("brush"), sid("computedBrush"), bd);
        opts.putInteger(sid("opacity"), 100);
        opts.putInteger(sid("flow"), 100);
        opts.putBoolean(sid("useTipDynamics"), false);
        opts.putBoolean(sid("useScatter"), false);
        opts.putBoolean(sid("smoothing"), false);
        try { setOpts(opts); L("setOpts OK"); } catch (e) { L("setOpts ERR " + e.message.replace(/\n/g, " ")); }
        quick("after-set");
        // 4) 重新枚举键名，看 useTexture=true 后是否多出 textureMode / textureDepth
        try {
            var o2 = getOpts(); var names = [];
            for (var q = 0; q < o2.count; q++) { names.push(keyName(o2.getKey(q))); }
            L("AFTER-KEYS(" + o2.count + ")=" + names.join(","));
        } catch (e) { L("enum keys ERR " + e.message); }
        // 5) 描边
        try {
            var pts = [];
            for (var m = 0; m < 2; m++) {
                var p = new PathPointInfo();
                p.kind = PointKind.CORNERPOINT;
                p.anchor = [50, m * 99]; p.leftDirection = [50, m * 99]; p.rightDirection = [50, m * 99];
                pts.push(p);
            }
            var sub = new SubPathInfo(); sub.closed = false; sub.operation = ShapeOperation.SHAPEADD; sub.entireSubPath = pts;
            var pi = doc.pathItems.add("stroke", [sub]);
            pi.strokePath(ToolType.BRUSH);
            L("strokePath OK");
        } catch (e) { L("strokePath ERR " + e.message); }
        // 6) 导出
        try {
            doc.saveAs(new File("<REPO>/tests/baselines/ps/_work/probe13_out.png"), new PNGSaveOptions(), true, Extension.LOWERCASE);
            L("export OK");
        } catch (e) { L("export ERR " + e.message); }
        L("PROBE13-OK");
    } catch (e) {
        L("PROBE13-EXCEPTION: " + e.message + " line " + e.line);
    } finally {
        f.close();
        try { if (texDoc) { texDoc.close(SaveOptions.DONOTSAVECHANGES); } } catch (e2) {}
        try { if (doc) { doc.close(SaveOptions.DONOTSAVECHANGES); } } catch (e3) {}
        try { app.quit(); } catch (e4) {}
    }
})();
