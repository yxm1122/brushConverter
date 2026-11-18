// 探测 14：读出纹理混合模式/深度的关键值类型，并逐个尝试写入各模式
#target photoshop
(function () {
    var logPath = "<REPO>/tests/baselines/ps/_work/probe14.log";
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
    function describeKey(o, keyStr) {
        try {
            var k = sid(keyStr);
            var t = o.getType(k);
            var line = keyStr + " type=" + t;
            if (t == DescValueType.ENUMERATEDTYPE) { line += " enumType=" + keyName(o.getEnumerationType(k)) + " value=" + keyName(o.getEnumerationValue(k)); }
            else if (t == DescValueType.INTEGERTYPE) { line += " value=" + o.getInteger(k); }
            else if (t == DescValueType.DOUBLETYPE) { line += " value=" + o.getDouble(k); }
            else if (t == DescValueType.UNITDOUBLE) { line += " value=" + o.getUnitDoubleValue(k) + " unit=" + keyName(o.getUnitDoubleType(k)); }
            else if (t == DescValueType.BOOLEANTYPE) { line += " value=" + o.getBoolean(k); }
            else if (t == DescValueType.STRINGTYPE) { line += " value=" + o.getString(k); }
            return line;
        } catch (e) { return keyStr + " READ-ERR " + e.message; }
    }
    try {
        app.displayDialogs = DialogModes.NO;
        texDoc = app.open(new File("<REPO>/tests/baselines/ps/_work/texture_noise64.png"));
        var dp = new ActionDescriptor(); dp.putString(cid("Nm  "), "bc_texture_noise64");
        executeAction(cid("DfnP"), dp, DialogModes.NO);
        texDoc.close(SaveOptions.DONOTSAVECHANGES); texDoc = null;
        doc = app.documents.add(100, 100, 72, "probe14", NewDocumentMode.RGB, DocumentFill.TRANSPARENT);
        var d0 = new ActionDescriptor(); var r0 = new ActionReference();
        r0.putClass(sid("paintbrushTool")); d0.putReference(cid("null"), r0);
        executeAction(cid("slct"), d0, DialogModes.NO);
        // 打开纹理并写基本参数
        var o = getOpts();
        o.putBoolean(sid("useTexture"), true);
        var tp = new ActionDescriptor(); tp.putString(sid("name"), "bc_texture_noise64");
        o.putObject(sid("texture"), sid("pattern"), tp);
        o.putUnitDouble(sid("textureScale"), cid("#Prc"), 100);
        setOpts(o);
        var o2 = getOpts();
        L("== 纹理相关键 ==");
        var keys = ["textureBlendMode", "textureDepth", "minimumDepth", "textClickPoint", "interpretation", "textureDepthDynamics", "textureScale", "textureBrightness", "textureContrast"];
        for (var i = 0; i < keys.length; i++) { L(describeKey(o2, keys[i])); }
        // 尝试写 textureBlendMode
        var modes = [["subtract", "Sbtr"], ["multiply", "Mltp"], ["height", "Hght"], ["linearHeight", "LnHt"], ["darken", "Drkn"], ["linearBurn", "LBrn"]];
        for (var m = 0; m < modes.length; m++) {
            try {
                var oo = getOpts();
                var typeId = oo.getEnumerationType(sid("textureBlendMode"));
                oo.putEnumerated(sid("textureBlendMode"), typeId, cid(modes[m][1]));
                setOpts(oo);
                var chk = getOpts();
                L("SET-MODE " + modes[m][0] + " -> " + keyName(chk.getEnumerationValue(sid("textureBlendMode"))) + " (" + keyName(chk.getEnumerationType(sid("textureBlendMode"))) + ")");
            } catch (e) { L("SET-MODE " + modes[m][0] + " ERR " + e.message.replace(/\n/g, " ")); }
        }
        // 尝试写 textureDepth / minimumDepth
        for (var dv = 0; dv < 2; dv++) {
            var val = dv === 0 ? 60 : 10;
            try {
                var od = getOpts();
                var t2 = od.getType(sid("textureDepth"));
                if (t2 == DescValueType.INTEGERTYPE) { od.putInteger(sid("textureDepth"), val); }
                else { od.putUnitDouble(sid("textureDepth"), od.getUnitDoubleType(sid("textureDepth")), val); }
                setOpts(od);
                var chk2 = getOpts();
                L("SET-DEPTH " + val + " -> " + describeKey(chk2, "textureDepth"));
            } catch (e) { L("SET-DEPTH " + val + " ERR " + e.message.replace(/\n/g, " ")); }
        }
        L("PROBE14-OK");
    } catch (e) {
        L("PROBE14-EXCEPTION: " + e.message + " line " + e.line);
    } finally {
        f.close();
        try { if (texDoc) { texDoc.close(SaveOptions.DONOTSAVECHANGES); } } catch (e2) {}
        try { if (doc) { doc.close(SaveOptions.DONOTSAVECHANGES); } } catch (e3) {}
        try { app.quit(); } catch (e4) {}
    }
})();
