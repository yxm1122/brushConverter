// 探测 6：找出在 PS 2025 里切换到画笔工具的可行方式，并读取其工具选项描述符
#target photoshop
(function () {
    var logPath = "<REPO>/tests/baselines/ps/_work/probe6.log";
    var f = new File(logPath);
    f.encoding = "UTF-8"; f.open("w");
    function L(s) { f.writeln(String(s)); }
    var doc = null;
    function cid(s) { return charIDToTypeID(s); }
    function sid(s) { return stringIDToTypeID(s); }
    function tool() { try { return String(app.currentTool); } catch (e) { return "?ERR " + e.message; } }
    function trySelectTool(idStr) {
        try {
            var d = new ActionDescriptor();
            var r = new ActionReference();
            r.putClass(sid(idStr));
            d.putReference(cid("null"), r);
            executeAction(cid("slct"), d, DialogModes.NO);
            return "OK";
        } catch (e) { return "ERR " + e.message; }
    }
    try {
        app.displayDialogs = DialogModes.NO;
        doc = app.documents.add(100, 100, 72, "probe6", NewDocumentMode.RGB, DocumentFill.TRANSPARENT);
        L("start tool=" + tool());
        try { app.bringToFront(); L("bringToFront OK"); } catch (e) { L("bringToFront ERR " + e.message); }
        L("after bringToFront tool=" + tool());
        L("event ids: slct=" + cid("slct") + " getd=" + cid("getd") + " setd=" + cid("setd"));
        L("A moveTool: " + trySelectTool("moveTool") + " -> " + tool());
        L("B brushTool: " + trySelectTool("brushTool") + " -> " + tool());
        L("C brush(charID Brsh class): ");
        try {
            var d3 = new ActionDescriptor(); var r3 = new ActionReference();
            r3.putClass(cid("Brsh")); d3.putReference(cid("null"), r3);
            executeAction(cid("slct"), d3, DialogModes.NO);
            L("   OK -> " + tool());
        } catch (e) { L("   ERR " + e.message); }
        // D runMenuItem 方案
        try { app.runMenuItem(sid("brushTool")); L("D runMenuItem brushTool OK -> " + tool()); }
        catch (e) { L("D runMenuItem ERR " + e.message); }
        // E 读工具选项
        try {
            var r5 = new ActionReference();
            r5.putEnumerated(cid("capp"), cid("Ordn"), cid("Trgt"));
            var appDesc = executeActionGet(r5);
            var to = appDesc.getObjectValue(sid("currentToolOptions"));
            L("E currentToolOptions count=" + to.count + " src=" + String(to.toSource()).slice(0, 3000));
        } catch (e) { L("E currentToolOptions ERR " + e.message); }
        L("PROBE6-OK");
    } catch (e) {
        L("PROBE6-EXCEPTION: " + e.message + " line " + e.line);
    } finally {
        f.close();
        try { if (doc) { doc.close(SaveOptions.DONOTSAVECHANGES); } } catch (e2) {}
        try { app.quit(); } catch (e3) {}
    }
})();
