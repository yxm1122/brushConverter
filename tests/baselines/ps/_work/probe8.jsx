// 探测 8：选中 paintbrushTool 后导出完整工具选项描述符；并测试路径描边出图
#target photoshop
(function () {
    var logPath = "<REPO>/tests/baselines/ps/_work/probe8.log";
    var f = new File(logPath);
    f.encoding = "UTF-8"; f.open("w");
    function L(s) { f.writeln(String(s)); }
    var doc = null;
    function cid(s) { return charIDToTypeID(s); }
    function sid(s) { return stringIDToTypeID(s); }
    function appDesc() {
        var r = new ActionReference(); r.putEnumerated(cid("capp"), cid("Ordn"), cid("Trgt"));
        return executeActionGet(r);
    }
    try {
        app.displayDialogs = DialogModes.NO;
        doc = app.documents.add(100, 100, 72, "probe8", NewDocumentMode.RGB, DocumentFill.TRANSPARENT);
        // 选中画笔工具
        try {
            var d = new ActionDescriptor(); var r = new ActionReference();
            r.putClass(sid("paintbrushTool")); d.putReference(cid("null"), r);
            executeAction(cid("slct"), d, DialogModes.NO);
            L("tool=" + app.currentTool);
        } catch (e) { L("slct paintbrushTool ERR " + e.message); }
        // 完整工具选项
        try {
            var to = appDesc().getObjectValue(sid("currentToolOptions"));
            L("toolOptions count=" + to.count + " len=" + String(to.toSource()).length);
            L("TOOL-OPTIONS-BEGIN");
            L(String(to.toSource()));
            L("TOOL-OPTIONS-END");
        } catch (e) { L("toolOptions ERR " + e.message); }
        // 路径描边测试：画一条贯穿画布的路径，用当前画笔描边
        try {
            var p = doc.pathItems.add("strokeTest", []);
            var pt1 = p.pathPoints.add();
            pt1.anchor = [50, 0]; pt1.leftDirection = [50, 0]; pt1.rightDirection = [50, 0];
            var pt2 = p.pathPoints.add();
            pt2.anchor = [50, 100]; pt2.leftDirection = [50, 100]; pt2.rightDirection = [50, 100];
            p.strokePath(ToolType.BRUSH);
            L("strokePath OK");
        } catch (e) { L("strokePath ERR " + e.message); }
        L("PROBE8-OK");
    } catch (e) {
        L("PROBE8-EXCEPTION: " + e.message + " line " + e.line);
    } finally {
        f.close();
        try { if (doc) { doc.close(SaveOptions.DONOTSAVECHANGES); } } catch (e2) {}
        try { app.quit(); } catch (e3) {}
    }
})();
