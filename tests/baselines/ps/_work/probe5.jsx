// 探测 5：用 stringID("brushTool") 选中画笔工具，再从应用描述符读 currentToolOptions
#target photoshop
(function () {
    var logPath = "<REPO>/tests/baselines/ps/_work/probe5.log";
    var f = new File(logPath);
    f.encoding = "UTF-8"; f.open("w");
    function L(s) { f.writeln(String(s)); }
    var doc = null;
    function cid(s) { return charIDToTypeID(s); }
    function sid(s) { return stringIDToTypeID(s); }
    function getAppDesc() {
        var r = new ActionReference();
        r.putEnumerated(cid("capp"), cid("Ordn"), cid("Trgt"));
        return executeActionGet(r);
    }
    try {
        app.displayDialogs = DialogModes.NO;
        doc = app.documents.add(100, 100, 72, "probe5", NewDocumentMode.RGB, DocumentFill.TRANSPARENT);
        // 1) 用 stringID 选中画笔工具
        try {
            var d = new ActionDescriptor();
            var r = new ActionReference();
            r.putClass(sid("brushTool"));
            d.putReference(cid("null"), r);
            executeAction(cid("slct"), d, DialogModes.NO);
            L("slct brushTool OK -> currentTool=" + app.currentTool);
        } catch (e) { L("slct brushTool ERR: " + e.message); }
        // 2) 读应用描述符与 currentToolOptions
        try {
            var appDesc = getAppDesc();
            L("capp count=" + appDesc.count);
            var to = appDesc.getObjectValue(sid("currentToolOptions"));
            L("currentToolOptions count=" + to.count);
            var src = String(to.toSource());
            L("TOOL-OPTIONS-SOURCE-BEGIN");
            L(src);
            L("TOOL-OPTIONS-SOURCE-END len=" + src.length);
        } catch (e) { L("currentToolOptions ERR: " + e.message); }
        // 3) 尝试直接读画笔类描述符
        try {
            var r2 = new ActionReference();
            r2.putClass(sid("brush"));
            var d2 = executeActionGet(r2);
            L("GET class brush OK count=" + d2.count + " src=" + String(d2.toSource()).slice(0, 800));
        } catch (e) { L("GET class brush ERR: " + e.message); }
        // 4) 列出能用的 brush 相关 stringID
        var names = ["brushTool", "brush", "currentToolOptions", "texture", "textureMode", "textureDepth",
                     "textureBrightness", "textureContrast", "texturePattern", "brushSize", "brushDiameter"];
        for (var i = 0; i < names.length; i++) {
            try { L("sid " + names[i] + " = " + sid(names[i])); } catch (e) { L("sid " + names[i] + " ERR"); }
        }
        L("PROBE5-OK");
    } catch (e) {
        L("PROBE5-EXCEPTION: " + e.message + " line " + e.line);
    } finally {
        f.close();
        try { if (doc) { doc.close(SaveOptions.DONOTSAVECHANGES); } } catch (e2) {}
        try { app.quit(); } catch (e3) {}
    }
})();
