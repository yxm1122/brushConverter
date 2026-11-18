// 探测 12：再试多种“写入画笔设置”的写法，判断 PS2025 是否允许脚本修改画笔工具选项
#target photoshop
(function () {
    var logPath = "<REPO>/tests/baselines/ps/_work/probe12.log";
    var f = new File(logPath);
    f.encoding = "UTF-8"; f.open("w");
    function L(s) { f.writeln(String(s)); }
    var doc = null;
    function cid(s) { return charIDToTypeID(s); }
    function sid(s) { return stringIDToTypeID(s); }
    function keyName(k) { try { var s = typeIDToStringID(k); if (s && s.length) return s; } catch (e) {} try { return typeIDToCharID(k); } catch (e) { return "?" + k; } }
    function appDesc() { var r = new ActionReference(); r.putEnumerated(cid("capp"), cid("Ordn"), cid("Trgt")); return executeActionGet(r); }
    function state(tag) {
        try {
            var o = appDesc().getObjectValue(sid("currentToolOptions"));
            L(tag + " opacity=" + o.getInteger(sid("opacity")) + " flow=" + o.getInteger(sid("flow")) +
              " useTexture=" + o.getBoolean(sid("useTexture")) + " b=" + o.getInteger(sid("textureBrightness")) +
              " c=" + o.getInteger(sid("textureContrast")) + " dia=" + o.getObjectValue(sid("brush")).getUnitDoubleValue(sid("diameter")));
        } catch (e) { L(tag + " state ERR " + e.message); }
    }
    try {
        app.displayDialogs = DialogModes.NO;
        doc = app.documents.add(100, 100, 72, "probe12", NewDocumentMode.RGB, DocumentFill.TRANSPARENT);
        var d0 = new ActionDescriptor(); var r0 = new ActionReference();
        r0.putClass(sid("paintbrushTool")); d0.putReference(cid("null"), r0);
        executeAction(cid("slct"), d0, DialogModes.NO);
        state("baseline");
        function trySet(label, build) {
            try { executeAction(cid("setd"), build(), DialogModes.NO); L(label + " OK"); state(label); }
            catch (e) { L(label + " ERR " + e.message.replace(/\n/g, " ")); }
        }
        // A: 画笔引用 + 类 Brsh + 只设不透明度（判断 setd 是否影响工具选项）
        trySet("A-brushref-opacity", function () {
            var d = new ActionDescriptor(); var r = new ActionReference();
            r.putEnumerated(cid("Brsh"), cid("Ordn"), cid("Trgt"));
            d.putReference(cid("null"), r);
            var inner = new ActionDescriptor();
            inner.putInteger(cid("Opct"), 40);
            d.putObject(cid("T   "), cid("Brsh"), inner);
            return d;
        });
        // B: 画笔引用 + 类 Brsh + 纹理 charID 键
        trySet("B-brushref-texture-charIDs", function () {
            var d = new ActionDescriptor(); var r = new ActionReference();
            r.putEnumerated(cid("Brsh"), cid("Ordn"), cid("Trgt"));
            d.putReference(cid("null"), r);
            var inner = new ActionDescriptor();
            inner.putBoolean(cid("useT"), true);
            inner.putInteger(cid("Brgh"), -25);
            inner.putInteger(cid("Cntr"), 25);
            inner.putInteger(cid("Dpth"), 60);
            d.putObject(cid("T   "), cid("Brsh"), inner);
            return d;
        });
        // C: 工具类引用 paintbrushTool + 类 currentToolOptions + 完整选项
        trySet("C-toolclass-fullopts", function () {
            var o = appDesc().getObjectValue(sid("currentToolOptions"));
            o.putInteger(sid("opacity"), 30);
            var d = new ActionDescriptor(); var r = new ActionReference();
            r.putClass(sid("paintbrushTool"));
            d.putReference(cid("null"), r);
            d.putObject(cid("T   "), sid("currentToolOptions"), o);
            return d;
        });
        // D: brush 引用 current/tool/Brsh + 类 Brsh
        trySet("D-brush-current-tool", function () {
            var d = new ActionDescriptor(); var r = new ActionReference();
            r.putEnumerated(cid("Brsh"), cid("currn"), cid("tool"));
            d.putReference(cid("null"), r);
            var inner = new ActionDescriptor();
            inner.putInteger(sid("opacity"), 20);
            d.putObject(cid("T   "), cid("Brsh"), inner);
            return d;
        });
        // E: 载入 ABR 画笔组，看看是否可行
        try {
            app.load(new File("<REPO>/tests/定量测试专用.abr"));
            L("E load ABR OK");
        } catch (e) { L("E load ABR ERR " + e.message.replace(/\n/g, " ")); }
        state("after-ABR");
        L("PROBE12-OK");
    } catch (e) {
        L("PROBE12-EXCEPTION: " + e.message + " line " + e.line);
    } finally {
        f.close();
        try { if (doc) { doc.close(SaveOptions.DONOTSAVECHANGES); } } catch (e2) {}
        try { app.quit(); } catch (e3) {}
    }
})();
