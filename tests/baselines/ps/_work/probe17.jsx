// 探测 17：两阶段写入（先启用纹理再写模式），单笔尖盖章并按带 Alpha 的 PNG 导出
#target photoshop
(function () {
    var logPath = "<REPO>/tests/baselines/ps/_work/probe17.log";
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
    function applyBase(bright, contrast, depth) {
        // 第一阶段：启用纹理并写入数值参数
        var o = getOpts();
        o.putBoolean(sid("useTexture"), true);
        var tp = new ActionDescriptor(); tp.putString(sid("name"), "bc_texture_noise64");
        o.putObject(sid("texture"), sid("pattern"), tp);
        o.putUnitDouble(sid("textureScale"), cid("#Prc"), 100);
        o.putInteger(sid("textureBrightness"), bright);
        o.putInteger(sid("textureContrast"), contrast);
        o.putUnitDouble(sid("textureDepth"), cid("#Prc"), depth);
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
        setOpts(o);
    }
    function applyMode(modeChar, modeName) {
        // 第二阶段：纹理已启用，此时才能读到 textureBlendMode 的枚举类型
        var o = getOpts();
        var typeId;
        try { typeId = o.getEnumerationType(sid("textureBlendMode")); }
        catch (e) { L("getEnumerationType ERR " + e.message); typeId = sid("blendMode"); }
        o.putEnumerated(sid("textureBlendMode"), typeId, cid(modeChar));
        setOpts(o);
        return keyName(getOpts().getEnumerationValue(sid("textureBlendMode")));
    }
    function freshDoc(name) {
        if (doc) { try { doc.close(SaveOptions.DONOTSAVECHANGES); } catch (e) {} doc = null; }
        doc = app.documents.add(100, 100, 72, name, NewDocumentMode.RGB, DocumentFill.TRANSPARENT);
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
        var po = new PNGSaveOptions();
        try { po.transparency = true; } catch (e) { L("po.transparency ERR " + e.message); }
        try { doc.saveAs(new File(path), po, true, Extension.LOWERCASE); L("export " + path); }
        catch (e) { L("saveAs ERR " + e.message); }
    }
    try {
        app.displayDialogs = DialogModes.NO;
        texDoc = app.open(new File("<REPO>/tests/baselines/ps/_work/texture_noise64.png"));
        var dp = new ActionDescriptor(); dp.putString(cid("Nm  "), "bc_texture_noise64");
        executeAction(cid("DfnP"), dp, DialogModes.NO);
        texDoc.close(SaveOptions.DONOTSAVECHANGES); texDoc = null;
        freshDoc("probe17");
        var d0 = new ActionDescriptor(); var r0 = new ActionReference();
        r0.putClass(sid("paintbrushTool")); d0.putReference(cid("null"), r0);
        executeAction(cid("slct"), d0, DialogModes.NO);
        L("tool=" + app.currentTool);
        var col = new SolidColor(); col.rgb.red = 0; col.rgb.green = 0; col.rgb.blue = 0;
        app.foregroundColor = col;
        L("foreground set");
        applyBase(0, 0, 100);
        L("base applied");
        // 枚举候选（第二阶段之后可以安全读取/写入）
        var cands = [["Sbtr", "subtract"], ["Mltp", "multiply"], ["Hght", "height"], ["LnHt", "linearHeight"],
                     ["LnHg", "linearHeight2"], ["LinH", "linearHeight3"], ["Ovrl", "overlay"]];
        for (var i = 0; i < cands.length; i++) {
            try { L("cand " + cands[i][0] + " (" + cands[i][1] + ") -> " + applyMode(cands[i][0], cands[i][1])); }
            catch (e) { L("cand " + cands[i][0] + " ERR " + e.message.replace(/\n/g, " ")); }
        }
        // 出两张
        applyBase(0, 0, 100); applyMode("Sbtr"); paintOne();
        L("mode now=" + keyName(getOpts().getEnumerationValue(sid("textureBlendMode"))));
        exportPng("<REPO>/tests/baselines/ps/_work/probe17_subtract.png");
        freshDoc("probe17b");
        applyBase(0, 0, 100); applyMode("Mltp"); paintOne();
        exportPng("<REPO>/tests/baselines/ps/_work/probe17_multiply.png");
        L("PROBE17-OK");
    } catch (e) {
        L("PROBE17-EXCEPTION: " + e.message + " line " + e.line);
    } finally {
        f.close();
        try { if (texDoc) { texDoc.close(SaveOptions.DONOTSAVECHANGES); } } catch (e2) {}
        try { if (doc) { doc.close(SaveOptions.DONOTSAVECHANGES); } } catch (e3) {}
        try { app.quit(); } catch (e4) {}
    }
})();
