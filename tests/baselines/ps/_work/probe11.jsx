// 探测 11：找出写入画笔工具选项（含纹理参数）可用的 Action Manager 写法
#target photoshop
(function () {
    var logPath = "<REPO>/tests/baselines/ps/_work/probe11.log";
    var f = new File(logPath);
    f.encoding = "UTF-8"; f.open("w");
    function L(s) { f.writeln(String(s)); }
    var doc = null, texDoc = null;
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
    function readState(tag) {
        try {
            var o = appDesc().getObjectValue(sid("currentToolOptions"));
            var b = o.getObjectValue(sid("brush"));
            L(tag + ": useTexture=" + o.getBoolean(sid("useTexture")) + " b=" + o.getInteger(sid("textureBrightness")) +
              " c=" + o.getInteger(sid("textureContrast")) + " scale=" + o.getUnitDoubleValue(sid("textureScale")) +
              " dia=" + b.getUnitDoubleValue(sid("diameter")) + " texName=" + o.getObjectValue(sid("texture")).getString(sid("name")));
        } catch (e) { L(tag + " readState ERR " + e.message); }
    }
    try {
        app.displayDialogs = DialogModes.NO;
        // 图案定义
        texDoc = app.open(new File("<REPO>/tests/baselines/ps/_work/texture_noise64.png"));
        var dp = new ActionDescriptor(); dp.putString(cid("Nm  "), "bc_texture_noise64");
        executeAction(cid("DfnP"), dp, DialogModes.NO);
        texDoc.close(SaveOptions.DONOTSAVECHANGES); texDoc = null;
        // 取图案 ID：尝试多种引用
        var patID = "", patName = "bc_texture_noise64";
        var refTries = [
            ["class+OrdnTrgt", function () { var r = new ActionReference(); r.putClass(sid("pattern")); r.putEnumerated(cid("Ordn"), cid("Trgt")); return r; }],
            ["pattern+OrdnTrgt", function () { var r = new ActionReference(); r.putEnumerated(sid("pattern"), cid("Ordn"), cid("Trgt")); return r; }],
            ["name", function () { var r = new ActionReference(); r.putName(sid("pattern"), "bc_texture_noise64"); return r; }]
        ];
        for (var i = 0; i < refTries.length; i++) {
            try {
                var pd = executeActionGet(refTries[i][1]());
                var s = "";
                for (var j = 0; j < pd.count; j++) { var k = pd.getKey(j); if (pd.getType(k) == DescValueType.STRINGTYPE) { s += keyName(k) + "=" + pd.getString(k) + " "; if (keyName(k) == "ID") { patID = pd.getString(k); } } }
                L("pattern ref " + refTries[i][0] + " OK: " + s);
            } catch (e) { L("pattern ref " + refTries[i][0] + " ERR " + e.message.replace(/\n/g, " ")); }
        }
        // 新画布 + 画笔工具
        doc = app.documents.add(100, 100, 72, "probe11", NewDocumentMode.RGB, DocumentFill.TRANSPARENT);
        var d0 = new ActionDescriptor(); var r0 = new ActionReference();
        r0.putClass(sid("paintbrushTool")); d0.putReference(cid("null"), r0);
        executeAction(cid("slct"), d0, DialogModes.NO);
        var ad = appDesc();
        var toClass = ad.getObjectType(sid("currentToolOptions"));
        L("currentToolOptions objectClass=" + keyName(toClass) + " raw=" + toClass);
        readState("baseline");
        // 构造要写入的选项：基于当前选项副本
        function makeOpts() {
            var o = appDesc().getObjectValue(sid("currentToolOptions"));
            o.putBoolean(sid("useTexture"), true);
            o.putInteger(sid("textureBrightness"), -25);
            o.putInteger(sid("textureContrast"), 25);
            o.putUnitDouble(sid("textureScale"), cid("#Prc"), 100);
            var tp = new ActionDescriptor();
            tp.putString(sid("name"), patName);
            if (patID) { tp.putString(sid("ID"), patID); }
            o.putObject(sid("texture"), sid("pattern"), tp);
            var bd = o.getObjectValue(sid("brush"));
            bd.putUnitDouble(sid("diameter"), cid("#Pxl"), 200);
            bd.putUnitDouble(sid("hardness"), cid("#Prc"), 100);
            o.putObject(sid("brush"), sid("computedBrush"), bd);
            return o;
        }
        // S1: capp ref + key cid("T   ") + 真实对象类
        try {
            var o1 = makeOpts();
            var d1 = new ActionDescriptor(); var r1 = new ActionReference();
            r1.putEnumerated(cid("capp"), cid("Ordn"), cid("Trgt"));
            d1.putReference(cid("null"), r1);
            d1.putObject(cid("T   "), toClass, o1);
            executeAction(cid("setd"), d1, DialogModes.NO);
            L("S1 OK"); readState("S1");
        } catch (e) { L("S1 ERR " + e.message.replace(/\n/g, " ")); }
        // S2: brush 引用 + cid("Brsh")
        try {
            var o2 = makeOpts();
            var d2 = new ActionDescriptor(); var r2 = new ActionReference();
            r2.putEnumerated(cid("Brsh"), cid("Ordn"), cid("Trgt"));
            d2.putReference(cid("null"), r2);
            var inner = new ActionDescriptor();
            inner.putBoolean(sid("useTexture"), true);
            inner.putInteger(sid("textureBrightness"), -30);
            d2.putObject(cid("T   "), cid("Brsh"), inner);
            executeAction(cid("setd"), d2, DialogModes.NO);
            L("S2 OK"); readState("S2");
        } catch (e) { L("S2 ERR " + e.message.replace(/\n/g, " ")); }
        // S3: capp ref + stringID("to") 键（对照）
        try {
            var o3 = makeOpts();
            var d3 = new ActionDescriptor(); var r3 = new ActionReference();
            r3.putEnumerated(cid("capp"), cid("Ordn"), cid("Trgt"));
            d3.putReference(cid("null"), r3);
            d3.putObject(sid("to"), toClass, o3);
            executeAction(cid("setd"), d3, DialogModes.NO);
            L("S3 OK"); readState("S3");
        } catch (e) { L("S3 ERR " + e.message.replace(/\n/g, " ")); }
        L("PROBE11-OK");
    } catch (e) {
        L("PROBE11-EXCEPTION: " + e.message + " line " + e.line);
    } finally {
        f.close();
        try { if (texDoc) { texDoc.close(SaveOptions.DONOTSAVECHANGES); } } catch (e2) {}
        try { if (doc) { doc.close(SaveOptions.DONOTSAVECHANGES); } } catch (e3) {}
        try { app.quit(); } catch (e4) {}
    }
})();
