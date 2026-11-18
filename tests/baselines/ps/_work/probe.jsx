// 探测脚本（临时）：验证 Photoshop -r 能执行 JSX，并试探 Action Manager 是否暴露画笔纹理参数
#target photoshop
(function () {
    var logPath = "<REPO>/tests/baselines/ps/_work/probe.log";
    var f = new File(logPath);
    f.encoding = "UTF-8";
    f.open("w");
    function L(s) { f.writeln(String(s)); }
    try {
        L("PS name=" + app.name + " version=" + app.version);
        L("currentTool=" + app.currentTool);
        L("docs=" + app.documents.length);
        var tries = [];
        function tryGet(label, build) {
            try {
                var r = build();
                var d = executeActionGet(r);
                tries.push(label + " OK: " + d.toSource());
            } catch (e) {
                tries.push(label + " ERR: " + e.message);
            }
        }
        tryGet("brush-ordinal-target", function () {
            var r = new ActionReference();
            r.putEnumerated(stringIDToTypeID("brush"), stringIDToTypeID("ordinal"), stringIDToTypeID("target"));
            return r;
        });
        tryGet("tool-options-current", function () {
            var r = new ActionReference();
            r.putEnumerated(stringIDToTypeID("tool"), stringIDToTypeID("ordinal"), stringIDToTypeID("target"));
            return r;
        });
        for (var i = 0; i < tries.length; i++) { L(tries[i]); }
        var ids = ["brush", "texture", "textureMode", "textureDepth", "textureBrightness", "textureContrast", "txtr"];
        for (var j = 0; j < ids.length; j++) {
            try { L("stringID " + ids[j] + " = " + stringIDToTypeID(ids[j])); }
            catch (e) { L("stringID " + ids[j] + " ERR " + e.message); }
        }
        L("SMOKE-OK");
    } catch (e) {
        L("SMOKE-EXCEPTION: " + e.message + " line " + e.line);
    } finally {
        f.close();
        app.quit();
    }
})();
