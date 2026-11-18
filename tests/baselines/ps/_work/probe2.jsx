// 探测 2：打开文档 + 选中画笔工具后，能否用 Action Manager 读到/写入画笔纹理参数
#target photoshop
(function () {
    var logPath = "<REPO>/tests/baselines/ps/_work/probe2.log";
    var f = new File(logPath);
    f.encoding = "UTF-8";
    f.open("w");
    function L(s) { f.writeln(String(s)); }
    try {
        L("PS " + app.version);
        var doc = app.documents.add(100, 100, 300, "probe2", NewDocumentMode.RGB, DocumentFill.TRANSPARENT);
        L("doc created: " + doc.name + " " + doc.width + "x" + doc.height);
        // 选中画笔工具
        try {
            var d0 = new ActionDescriptor();
            var r0 = new ActionReference();
            r0.putClass(stringIDToTypeID("brush"));
            d0.putReference(stringIDToTypeID("null"), r0);
            executeAction(stringIDToTypeID("select"), d0, DialogModes.NO);
            L("select brush tool OK, currentTool=" + app.currentTool);
        } catch (e) { L("select brush tool ERR: " + e.message); }
        var ids = {
            brush: stringIDToTypeID("brush"),
            brushClass: charIDToTypeID("Brsh"),
            ordinal: stringIDToTypeID("ordinal"),
            target: stringIDToTypeID("target"),
            tool: stringIDToTypeID("tool"),
            texture: stringIDToTypeID("texture"),
            textureMode: stringIDToTypeID("textureMode"),
            textureDepth: stringIDToTypeID("textureDepth"),
            textureBrightness: stringIDToTypeID("textureBrightness"),
            textureContrast: stringIDToTypeID("textureContrast")
        };
        var variants = [
            ["ref-brush-ordinal-target", function () { var r = new ActionReference(); r.putEnumerated(ids.brush, ids.ordinal, ids.target); return r; }],
            ["ref-brush-class-current", function () { var r = new ActionReference(); r.putClass(ids.brush); r.putEnumerated(charIDToTypeID("currn"), ids.tool, ids.brush); return r; }],
            ["ref-brush-class-target", function () { var r = new ActionReference(); r.putClass(ids.brush); r.putEnumerated(ids.ordinal, ids.target); return r; }]
        ];
        for (var i = 0; i < variants.length; i++) {
            try {
                var r = variants[i][1]();
                var d = executeActionGet(r);
                L("GET " + variants[i][0] + " OK");
                L("  keys: " + d.count + "  || toSource(head): " + String(d.toSource()).slice(0, 1500));
            } catch (e) {
                L("GET " + variants[i][0] + " ERR: " + e.message);
            }
        }
        // app.brushes 集合是否存在
        try {
            L("app.brushes count = " + app.brushes.length);
            for (var b = 0; b < Math.min(5, app.brushes.length); b++) {
                L("  brush[" + b + "] = " + app.brushes[b].name);
            }
        } catch (e) { L("app.brushes ERR: " + e.message); }
        L("PROBE2-OK");
    } catch (e) {
        L("PROBE2-EXCEPTION: " + e.message + " line " + e.line);
    } finally {
        f.close();
        try { app.quit(); } catch (e2) {}
    }
})();
