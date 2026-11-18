// 探测 3：用 Action Manager 读取/写入画笔纹理设置（TxFl 类）
#target photoshop
(function () {
    var logPath = "<REPO>/tests/baselines/ps/_work/probe3.log";
    var f = new File(logPath);
    f.encoding = "UTF-8";
    f.open("w");
    function L(s) { f.writeln(String(s)); }
    var doc = null;
    try {
        app.displayDialogs = DialogModes.NO;
        L("PS " + app.version);
        doc = app.documents.add(100, 100, 72, "probe3", NewDocumentMode.RGB, DocumentFill.TRANSPARENT);
        L("doc " + doc.name);
        // 1) 选中画笔工具（事件 slct，类 Brsh）
        try {
            var d0 = new ActionDescriptor();
            var r0 = new ActionReference();
            r0.putClass(charIDToTypeID("Brsh"));
            d0.putReference(charIDToTypeID("null"), r0);
            executeAction(charIDToTypeID("slct"), d0, DialogModes.NO);
            L("slct brush OK tool=" + app.currentTool);
        } catch (e) { L("slct brush ERR: " + e.message); }
        // 2) 取画笔纹理描述符（类 TxFl）
        function dumpRef(label, build) {
            try {
                var d = executeActionGet(build());
                L("GET " + label + " OK count=" + d.count);
                L("  " + String(d.toSource()).slice(0, 2000));
                return d;
            } catch (e) {
                L("GET " + label + " ERR: " + e.message);
                return null;
            }
        }
        var tx1 = dumpRef("Brsh/currn/TxFl", function () { var r = new ActionReference(); r.putEnumerated(charIDToTypeID("Brsh"), charIDToTypeID("currn"), charIDToTypeID("TxFl")); return r; });
        var tx2 = dumpRef("TxFl/Ordn/Trgt", function () { var r = new ActionReference(); r.putEnumerated(charIDToTypeID("TxFl"), charIDToTypeID("Ordn"), charIDToTypeID("Trgt")); return r; });
        var tx3 = dumpRef("class TxFl", function () { var r = new ActionReference(); r.putClass(charIDToTypeID("TxFl")); return r; });
        dumpRef("Brsh/Ordn/Trgt", function () { var r = new ActionReference(); r.putEnumerated(charIDToTypeID("Brsh"), charIDToTypeID("Ordn"), charIDToTypeID("Trgt")); return r; });
        // 3) 尝试写入：Brgh=-50、Cntr=25、Dpth=60、Txtm=multiply
        try {
            var dset = new ActionDescriptor();
            var rset = new ActionReference();
            rset.putEnumerated(charIDToTypeID("Brsh"), charIDToTypeID("currn"), charIDToTypeID("TxFl"));
            dset.putReference(charIDToTypeID("null"), rset);
            var dval = new ActionDescriptor();
            dval.putInteger(charIDToTypeID("Brgh"), -50);
            dval.putInteger(charIDToTypeID("Cntr"), 25);
            dval.putInteger(charIDToTypeID("Dpth"), 60);
            dset.putObject(charIDToTypeID("T   "), charIDToTypeID("TxFl"), dval);
            executeAction(charIDToTypeID("setd"), dset, DialogModes.NO);
            L("SET Brgh/Cntr/Dpth OK");
        } catch (e) { L("SET ERR: " + e.message); }
        // 4) 再读一次验证
        dumpRef("AFTER-SET Brsh/currn/TxFl", function () { var r = new ActionReference(); r.putEnumerated(charIDToTypeID("Brsh"), charIDToTypeID("currn"), charIDToTypeID("TxFl")); return r; });
        L("PROBE3-OK");
    } catch (e) {
        L("PROBE3-EXCEPTION: " + e.message + " line " + e.line);
    } finally {
        f.close();
        try { if (doc) { doc.close(SaveOptions.DONOTSAVECHANGES); } } catch (e2) { }
        try { app.quit(); } catch (e3) { }
    }
})();
