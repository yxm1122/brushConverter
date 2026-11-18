// 探测 7：确认画笔工具在 Action Manager 里的类名，并完整导出工具选项描述符
#target photoshop
(function () {
    var logPath = "<REPO>/tests/baselines/ps/_work/probe7.log";
    var f = new File(logPath);
    f.encoding = "UTF-8"; f.open("w");
    function L(s) { f.writeln(String(s)); }
    var doc = null;
    function cid(s) { return charIDToTypeID(s); }
    function sid(s) { return stringIDToTypeID(s); }
    function tool() { try { return String(app.currentTool); } catch (e) { return "?ERR"; } }
    function trySelect(idStr) {
        try {
            var d = new ActionDescriptor();
            var r = new ActionReference();
            r.putClass(sid(idStr));
            d.putReference(cid("null"), r);
            executeAction(cid("slct"), d, DialogModes.NO);
            return "OK";
        } catch (e) { return "ERR " + e.message.replace(/\n/g, " | "); }
    }
    try {
        app.displayDialogs = DialogModes.NO;
        doc = app.documents.add(100, 100, 72, "probe7", NewDocumentMode.RGB, DocumentFill.TRANSPARENT);
        var cands = ["moveTool", "paintbrushTool", "pencilTool", "eraserTool", "brushTool"];
        for (var i = 0; i < cands.length; i++) {
            L("cand " + cands[i] + " sid=" + sid(cands[i]) + " -> " + trySelect(cands[i]) + " tool=" + tool());
        }
        // 完整工具选项
        try {
            var r5 = new ActionReference();
            r5.putEnumerated(cid("capp"), cid("Ordn"), cid("Trgt"));
            var appDesc = executeActionGet(r5);
            var to = appDesc.getObjectValue(sid("currentToolOptions"));
            L("toSource len=" + String(to.toSource()).length);
            L("TOOL-OPTIONS-BEGIN");
            L(String(to.toSource()));
            L("TOOL-OPTIONS-END");
        } catch (e) { L("toolOptions ERR " + e.message); }
        L("PROBE7-OK");
    } catch (e) {
        L("PROBE7-EXCEPTION: " + e.message + " line " + e.line);
    } finally {
        f.close();
        try { if (doc) { doc.close(SaveOptions.DONOTSAVECHANGES); } } catch (e2) {}
        try { app.quit(); } catch (e3) {}
    }
})();
