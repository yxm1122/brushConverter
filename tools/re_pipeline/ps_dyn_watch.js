// tools/re_pipeline/ps_dyn_watch.js
// 任务 11A 第二阶段 —— 对「纹理参数对象」的 +0x18 / +0x1c 下等效硬件读断点，
// 抓「第一次读到 brightness/contrast 的指令」，也就是真正的消费端（像素）代码。
//
// 做法：Frida 的 MemoryAccessMonitor 是页级守卫（把目标页设成 guard page，任何访问都触发一次
// 异常并由 Frida 回调报告 details.from = 发起访问的指令地址）。所以：
//   1) 钩 H1 (0x141a939d5) 入口拿到对象基址（该对象在一次运行内地址稳定，实测同一值贯穿 14 步）；
//   2) 打开 shape 所在页的监控，只保留 operation=read 且 from 落在 Photoshop.exe 内的访问；
//   3) 只开 3 秒、上限 400 次访问，随后立刻关闭，避免把目标拖慢/拖崩。
// 只读：MemoryAccessMonitor 只改页保护属性并立即恢复，不写目标数据、不调用目标函数。

'use strict';

var RVA_H1 = 0x1a939d5;
var MAX_EVENTS = 400;
var WINDOW_MS = 3000;

var mod = null;
(function () {
  var mods = Process.enumerateModules();
  for (var i = 0; i < mods.length; i++) {
    if (/^photoshop[.]exe$/i.test(mods[i].name)) { mod = mods[i]; return; }
  }
})();

function emit(o) { try { send(o); } catch (e) {} }

if (mod === null) {
  emit({ t: 'fatal', err: 'Photoshop.exe not found' });
} else {
  var base = mod.base;
  var armed = false;
  var done = false;
  var hits = {};      // from -> {rva, op, n}
  var total = 0;
  var structAddr = null;

  function onAccess(details) {
    try {
      for (var i = 0; i < details.length; i++) {
        var d = details[i];
        if (d.operation !== 'read' && d.operation !== 'write') { continue; }
        total++;
        if (total > MAX_EVENTS) { return; }
        var from = d.from;
        var m = null;
        try { m = Process.findModuleByAddress(from); } catch (e) {}
        if (m === null || m.name !== mod.name) { continue; }
        var rva = '0x' + from.sub(m.base).toString(16);
        var key = d.operation + '|' + rva;
        if (!hits[key]) { hits[key] = { rva: rva, op: d.operation, n: 0 }; }
        hits[key].n++;
      }
    } catch (e) {}
  }

  function disarm(why) {
    if (!armed) { return; }
    armed = false;
    try { MemoryAccessMonitor.disable(); } catch (e) { emit({ t: 'disarm-err', err: String(e) }); }
    var list = [];
    for (var k in hits) { list.push(hits[k]); }
    list.sort(function (a, b) { return b.n - a.n; });
    emit({ t: 'watch-result', why: why, struct: structAddr ? structAddr.toString() : null,
           totalAccesses: total, distinctSites: list.length, sites: list.slice(0, 40) });
    done = true;
  }

  function arm(pagePtr) {
    if (armed) { return; }
    try {
      MemoryAccessMonitor.enable([{ base: pagePtr, size: 4096 }], { onAccess: onAccess });
      armed = true;
      emit({ t: 'watch-armed', page: pagePtr.toString(), window_ms: WINDOW_MS });
      setTimeout(function () { disarm('timeout'); }, WINDOW_MS);
    } catch (e) {
      emit({ t: 'watch-arm-fail', err: String(e) });
    }
  }

  // 钩 H1 入口：第一次拿到对象基址后立刻监控「该对象所在页」
  Interceptor.attach(base.add(RVA_H1), {
    onEnter: function (args, ctx) {
      try {
        var c = (ctx && typeof ctx.rdi !== 'undefined') ? ctx : this.context;
        if (!c) { return; }
        var p = c.rdi;
        if (!p || p.isNull()) { return; }
        if (structAddr === null) {
          structAddr = p;
          var page = p.and(ptr('0xfffffffffffff000'));
          emit({ t: 'H1-first', struct: p.toString(), page: page.toString() });
          // 立刻开监控：窗口期内 JSX 正在建文档并打笔，像素链路会真的跑起来
          arm(page);
        }
      } catch (e) { emit({ t: 'enter-err', err: String(e) }); }
    }
  });

  emit({ t: 'armed', base: base.toString(), hooks: ['H1-entry@0x1a939d5'], mode: 'MemoryAccessMonitor' });
  // 兜底：15 秒后无论如何收工
  setTimeout(function () { if (!done) { disarm('fallback-timeout'); } }, 15000);
}
