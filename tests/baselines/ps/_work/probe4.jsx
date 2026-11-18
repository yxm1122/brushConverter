// 探测 4：确认 -r 脚本启动时机是否导致 Action Manager 不可用；并测试基础 Action 命令
#target photoshop
(function () {
    var logPath = "<REPO>/tests/baselines/ps/_work/probe4.log";
    var f = new File(logPath);
    f.encoding = "UTF-8";
    f.open("w");
    function L(s) { f.writeln(String(s)); }
    var doc = null;
    function cid(s) { return charIDToTypeID(s); }
    function sid(s) { return stringIDToTypeID(s); }
    try {
        app.displayDialogs = DialogModes.NO;
        L("PS " + app.version + " at " + new Date().toString());
        $.sleep(3000);
        L("after sleep 3s: " + new Date().toString() + " currentTool=" + app.currentTool);
        doc = app.documents.add(100, 100, 72, "probe4", NewDocumentMode.RGB, DocumentFill.TRANSPARENT);
        L("doc created");
        // 基础 1：getd 文档描述符
        try {
            var r1 = new ActionReference();
            r1.putEnumerated(cid("Dcmn"), cid("Ordn"), cid("Trgt"));
            var d1 = executeActionGet(r1);
            L("GET document OK count=" + d1.count + " toSource.head=" + String(d1.toSource()).slice(0, 300));
        } catch (e) { L("GET document ERR: " + e.message); }
        // 基础 2：选择全部（Action Manager 事件 setd + 选择类）
        try {
            doc.selection.selectAll();
            L("DOM selectAll OK selectionBounds=" + doc.selection.bounds);
        } catch (e) { L("DOM selectAll ERR: " + e.message); }
        // 基础 3：选择画笔工具
        try {
            var d3 = new ActionDescriptor();
            var r3 = new ActionReference();
            r3.putClass(cid("Brsh"));
            d3.putReference(cid("null"), r3);
            executeAction(cid("slct"), d3, DialogModes.NO);
            L("slct brush OK tool=" + app.currentTool);
        } catch (e) { L("slct brush ERR: " + e.message); }
        // 基础 4：再次尝试读画笔描述符
        try {
            var r4 = new ActionReference();
            r4.putEnumerated(cid("Brsh"), cid("Ordn"), cid("Trgt"));
            var d4 = executeActionGet(r4);
            L("GET brush OK count=" + d4.count + " toSource.head=" + String(d4.toSource()).slice(0, 1200));
        } catch (e) { L("GET brush ERR: " + e.message); }
        // 基础 5：选中画笔后再选工具（对照）
        try {
            var d5 = new ActionDescriptor();
            var r5 = new ActionReference();
            r5.putClass(cid("Brsh"));
            d5.putReference(cid("null"), r5);
            executeAction(cid("slct"), d5, DialogModes.NO);
            L("slct brush 2nd OK tool=" + app.currentTool);
        } catch (e) { L("slct brush 2nd ERR: " + e.message); }
        L("PROBE4-OK");
    } catch (e) {
        L("PROBE4-EXCEPTION: " + e.message + " line " + e.line);
    } finally {
        f.close();
        try { if (doc) { doc.close(SaveOptions.DONOTSAVECHANGES); } } catch (e2) { }
        try { app.quit(); } catch (e3) { }
    }
})();
