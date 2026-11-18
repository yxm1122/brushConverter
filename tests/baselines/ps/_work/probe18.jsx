// 探测 18：继续寻找“线性高度(Linear Height)”纹理混合模式的枚举值
#target photoshop
(function () {
    var logPath = "<REPO>/tests/baselines/ps/_work/probe18.log";
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
    function tryEnum(kind, val) {
        var o = getOpts();
        var typeId = o.getEnumerationType(sid("textureBlendMode"));
        var v = (kind === "str") ? sid(val) : cid(val);
        o.putEnumerated(sid("textureBlendMode"), typeId, v);
        setOpts(o);
        var back = getOpts();
        return keyName(back.getEnumerationValue(sid("textureBlendMode"))) +
               " [storedId=" + back.getEnumerationValue(sid("textureBlendMode")) + " expectId=" + v + "]";
    }
    try {
        app.displayDialogs = DialogModes.NO;
        texDoc = app.open(new File("<REPO>/tests/baselines/ps/_work/texture_noise64.png"));
        var dp = new ActionDescriptor(); dp.putString(cid("Nm  "), "bc_texture_noise64");
        executeAction(cid("DfnP"), dp, DialogModes.NO);
        texDoc.close(SaveOptions.DONOTSAVECHANGES); texDoc = null;
        doc = app.documents.add(100, 100, 72, "probe18", NewDocumentMode.RGB, DocumentFill.TRANSPARENT);
        var d0 = new ActionDescriptor(); var r0 = new ActionReference();
        r0.putClass(sid("paintbrushTool")); d0.putReference(cid("null"), r0);
        executeAction(cid("slct"), d0, DialogModes.NO);
        // 启用纹理
        var o = getOpts();
        o.putBoolean(sid("useTexture"), true);
        var tp = new ActionDescriptor(); tp.putString(sid("name"), "bc_texture_noise64");
        o.putObject(sid("texture"), sid("pattern"), tp);
        setOpts(o);
        L("texture enabled, current mode=" + keyName(getOpts().getEnumerationValue(sid("textureBlendMode"))));
        var cands = [
            ["str", "linearHeight"], ["str", "heightLinear"], ["str", "linear height"],
            ["char", "LHgt"], ["char", "HgtL"], ["char", "HLin"], ["char", "LnH "],
            ["char", "Lnhg"], ["char", "LinH"], ["char", "Hgt2"], ["char", "HtLr"],
            ["char", "Drkn"], ["char", "LBrn"], ["char", "SftL"], ["char", "HrdL"]
        ];
        for (var i = 0; i < cands.length; i++) {
            try { L("cand " + cands[i][0] + " " + cands[i][1] + " -> " + tryEnum(cands[i][0], cands[i][1])); }
            catch (e) { L("cand " + cands[i][0] + " " + cands[i][1] + " ERR " + e.message.replace(/\n/g, " ")); }
        }
        L("PROBE18-OK");
    } catch (e) {
        L("PROBE18-EXCEPTION: " + e.message + " line " + e.line);
    } finally {
        f.close();
        try { if (texDoc) { texDoc.close(SaveOptions.DONOTSAVECHANGES); } } catch (e2) {}
        try { if (doc) { doc.close(SaveOptions.DONOTSAVECHANGES); } } catch (e3) {}
        try { app.quit(); } catch (e4) {}
    }
})();
