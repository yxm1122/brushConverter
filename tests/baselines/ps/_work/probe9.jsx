// 探测 9：逐键展开工具选项描述符（toSource 在 PS2025 返回 ({})）；并正确创建路径做描边测试
#target photoshop
(function () {
    var logPath = "<REPO>/tests/baselines/ps/_work/probe9.log";
    var f = new File(logPath);
    f.encoding = "UTF-8"; f.open("w");
    function L(s) { f.writeln(String(s)); }
    var doc = null;
    function cid(s) { return charIDToTypeID(s); }
    function sid(s) { return stringIDToTypeID(s); }
    function keyName(k) {
        try { var s = typeIDToStringID(k); if (s && s.length) { return s; } } catch (e) {}
        try { return typeIDToCharID(k); } catch (e) { return "?" + k; }
    }
    function describe(desc, depth, maxDepth) {
        // 逐键展开 ActionDescriptor；不依赖 toSource（PS2025 下返回 "({})"）
        var out = [];
        var pad = "";
        for (var q = 0; q < depth; q++) { pad += "  "; }
        var n = desc.count;
        for (var i = 0; i < n; i++) {
            var k = desc.getKey(i);
            var t = desc.getType(k);
            var name = keyName(k);
            var line = pad + name + " (type=" + t + ")";
            try {
                if (t == DescValueType.OBJECTTYPE) {
                    line += " objClass=" + keyName(desc.getObjectType(k));
                    out.push(line);
                    if (depth < maxDepth) { out = out.concat(describe(desc.getObjectValue(k), depth + 1, maxDepth)); }
                } else if (t == DescValueType.INTEGERTYPE) { line += " = " + desc.getInteger(k); out.push(line); }
                else if (t == DescValueType.DOUBLETYPE) { line += " = " + desc.getDouble(k); out.push(line); }
                else if (t == DescValueType.BOOLEANTYPE) { line += " = " + desc.getBoolean(k); out.push(line); }
                else if (t == DescValueType.STRINGTYPE) { line += " = " + desc.getString(k); out.push(line); }
                else if (t == DescValueType.ENUMERATEDTYPE) { line += " = " + keyName(desc.getEnumerationValue(k)); out.push(line); }
                else if (t == DescValueType.UNITDOUBLE) { line += " = " + desc.getUnitDoubleValue(k) + " unit=" + keyName(desc.getUnitDoubleType(k)); out.push(line); }
                else if (t == DescValueType.LISTTYPE) { line += " (list count=" + desc.getList(k).count + ")"; out.push(line); }
                else if (t == DescValueType.RAWTYPE) { line += " (raw)"; out.push(line); }
                else if (t == DescValueType.REFERENCETYPE) { line += " (reference)"; out.push(line); }
                else { line += " (其它)"; out.push(line); }
            } catch (e) { out.push(line + " [读取失败: " + e.message + "]"); }
        }
        return out;
    }
    try {
        app.displayDialogs = DialogModes.NO;
        doc = app.documents.add(100, 100, 72, "probe9", NewDocumentMode.RGB, DocumentFill.TRANSPARENT);
        var d = new ActionDescriptor(); var r = new ActionReference();
        r.putClass(sid("paintbrushTool")); d.putReference(cid("null"), r);
        executeAction(cid("slct"), d, DialogModes.NO);
        L("tool=" + app.currentTool);
        var ad = new ActionReference(); ad.putEnumerated(cid("capp"), cid("Ordn"), cid("Trgt"));
        var appd = executeActionGet(ad);
        var to = appd.getObjectValue(sid("currentToolOptions"));
        L("toolOptions count=" + to.count);
        var lines = describe(to, 0, 2);
        L("OPTIONS-BEGIN");
        for (var i = 0; i < lines.length; i++) { L(lines[i]); }
        L("OPTIONS-END total=" + lines.length);
        // 正确的路径描边：用 SubPathInfo 构造一条竖线
        try {
            var pts = [];
            for (var j = 0; j < 2; j++) {
                var p = new PathPointInfo();
                p.kind = PointKind.CORNERPOINT;
                p.anchor = [50, j * 100];
                p.leftDirection = [50, j * 100];
                p.rightDirection = [50, j * 100];
                pts.push(p);
            }
            var sub = new SubPathInfo();
            sub.closed = false;
            sub.operation = ShapeOperation.SHAPEADD;
            sub.entireSubPath = pts;
            var pathItem = doc.pathItems.add("strokeTest", [sub]);
            pathItem.strokePath(ToolType.BRUSH);
            L("strokePath OK" + (pathItem ? "" : " (pathItem 空)"));
        } catch (e) { L("strokePath ERR " + e.message + " line " + e.line); }
        L("PROBE9-OK");
    } catch (e) {
        L("PROBE9-EXCEPTION: " + e.message + " line " + e.line);
    } finally {
        f.close();
        try { if (doc) { doc.close(SaveOptions.DONOTSAVECHANGES); } } catch (e2) {}
        try { app.quit(); } catch (e3) {}
    }
})();
