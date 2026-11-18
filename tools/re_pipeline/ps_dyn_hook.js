// tools/re_pipeline/ps_dyn_hook.js
// 任务 11A —— Frida 侧插桩脚本（只读观测：不写目标内存、不改目标行为）
//
// 由 REA 的 start_frida_session + load_frida_script 载入（source_kind=file）。
// 观测点（地址来自任务 11 静态报告的 high 档候选；VA - 0x140000000 = RVA）：
//   H1 入口   0x141a939d5 (RVA 0x1a939d5)  描述符 -> 对象：写 +0x10=textureScale(double)
//                                          +0x18=textureContrast +0x1c=textureBrightness
//                                          +0x20=invertTexture +0x21=protectTexture
//   H1 末尾   0x141a93a79 (RVA 0x1a93a79)  上述字段写完之后（add rsp,0x50 之前，rdi 仍有效）
//   H3 入口   0x141a935f0 (RVA 0x1a935f0)  第一个实参 rcx = 目标结构体（函数内 mov rbx,rcx）
//   H3 末尾   0x141a9379b (RVA 0x1a9379b)  深度/最小深度/混合模式都写完之后（rbx 仍有效）
//
// 寄存器获取方式（踩坑后确定，详见报告 §1）：
//   本机这版 Frida 的 Interceptor.attach 回调只传 1 个实参（args），第二形参是 undefined；
//   但约定接口 this.context 可用。故统一用 cpuOf(this, ctx) 兼容两种取法。
//
// 只读保证：只有 Interceptor.attach 与内存读取，没有写内存、没有调用目标函数。

'use strict';

var RVA_H1 = 0x1a939d5;
var RVA_H1_SNAP = 0x1a93a79;
var RVA_H3 = 0x1a935f0;
var RVA_H3_SNAP = 0x1a9379b;
// 工作目录：通用临时目录，不写死本机用户目录；如需改目录，请同时改 ps_dyn_driver.jsx 的 BC_DYN_WORK
var WORK = 'C:/Windows/Temp/bcdyn';
var STEP_FILE = WORK + '/step.txt';
var MAX_MSGS = 1200;

var msgCount = 0;
var dropped = 0;
var diagDone = false;
var lastStruct = null;
var lastSnapSig = '';
var seen = {};
var errSeen = {};

function emit(obj) {
  if (msgCount >= MAX_MSGS) { dropped++; return; }
  msgCount++;
  obj.n = msgCount;
  try { send(obj); } catch (e) {}
}
function emitErrOnce(kind, e) {
  var k = kind + ':' + String(e);
  if (errSeen[k]) { return; }
  errSeen[k] = 1;
  emit({ t: kind, err: String(e) });
}

function cpuOf(self, ctx) {
  if (ctx !== undefined && ctx !== null && typeof ctx.rdi !== 'undefined') { return ctx; }
  if (self !== undefined && self !== null && typeof self.rdi !== 'undefined') { return self; }
  if (self !== undefined && self !== null && self.context !== undefined && self.context !== null) { return self.context; }
  return null;
}

// ---- 步骤标记（Frida 的 File 在构造函数里就打开文件，不要再调 open()）----
var _stepCache = null, _stepAt = 0;
function readStep() {
  var now = Date.now();
  if (_stepCache !== null && (now - _stepAt) < 100) { return _stepCache; }
  _stepAt = now;
  try {
    var f = new File(STEP_FILE, 'r');
    if (!f.exists) { _stepCache = null; return null; }
    var s = String(f.read(4096) || '');
    f.close();
    s = s.replace(/[\r\n\t ]+$/, '');
    _stepCache = s.length ? s : null;
  } catch (e) { _stepCache = null; }
  return _stepCache;
}

function safeBase(p) {
  if (!p || p.isNull()) { return null; }
  try { if (Process.findRangeByAddress(p) === null) { return null; } return p; } catch (e) { return null; }
}
// H1/H2 使用的对象布局（+0x10 scale, +0x18 contrast, +0x1c brightness, +0x20 invert, +0x21 protect）
function snapH1(p) {
  var b = safeBase(p);
  if (b === null) { return null; }
  var o = { addr: b.toString() };
  try { o.scale    = b.add(0x10).readDouble(); } catch (e) {}
  try { o.contrast = b.add(0x18).readS32(); }   catch (e) {}
  try { o.bright   = b.add(0x1c).readS32(); }   catch (e) {}
  try { o.invert   = b.add(0x20).readU8(); }    catch (e) {}
  try { o.protect  = b.add(0x21).readU8(); }    catch (e) {}
  return o;
}
// H3 使用的对象布局（+0x00 useTexture, +0x01 coverage, +0x04 blendMode, +0x08 depth, +0x0c minDepth）
function snapH3(p) {
  var b = safeBase(p);
  if (b === null) { return null; }
  var o = { addr: b.toString() };
  try { o.useTex   = b.add(0x00).readU8(); }   catch (e) {}
  try { o.cov      = b.add(0x01).readU8(); }   catch (e) {}
  try { o.mode     = b.add(0x04).readS32(); }  catch (e) {}
  try { o.depth    = b.add(0x08).readS32(); }  catch (e) {}
  try { o.minDepth = b.add(0x0c).readS32(); }  catch (e) {}
  return o;
}

function bt(context, depth) {
  var out = [];
  try {
    var frames = Thread.backtrace(context, Backtracer.FUZZY);
    for (var i = 0; i < frames.length && out.length < depth; i++) {
      var a = frames[i];
      var m = null;
      try { m = Process.findModuleByAddress(a); } catch (e) {}
      if (m === null) { continue; }
      out.push(m.name + '+0x' + a.sub(m.base).toString(16));
    }
  } catch (e) {}
  return out;
}

function attachAt(base, rva, label, onEnter) {
  var a = base.add(rva);
  try {
    Interceptor.attach(a, { onEnter: onEnter });
    emit({ t: 'hook-ok', label: label, addr: a.toString(), rva: '0x' + rva.toString(16) });
    return true;
  } catch (e) {
    emit({ t: 'hook-fail', label: label, addr: a.toString(), err: String(e) });
    return false;
  }
}

function moduleBase() {
  var mods = Process.enumerateModules();
  for (var i = 0; i < mods.length; i++) {
    if (/^photoshop[.]exe$/i.test(mods[i].name)) { return mods[i]; }
  }
  return null;
}

var mod = moduleBase();
if (mod === null) {
  emit({ t: 'fatal', err: 'Photoshop.exe module not found' });
} else {
  var base = mod.base;
  emit({ t: 'hello', pid: Process.id, module: mod.name, base: base.toString(),
         size: mod.size, arch: Process.arch, pointerSize: Process.pointerSize });

  // H1 入口：记录 rdi（对象基址）+ 调用栈（可补出静态扫不到的间接调用者）
  attachAt(base, RVA_H1, 'H1-entry', function (args, ctx) {
    try {
      var c = cpuOf(this, ctx);
      if (c === null) { if (!diagDone) { diagDone = true; emit({ t: 'diag-nocpu', argc: arguments.length }); } return; }
      if (!diagDone) {
        diagDone = true;
        emit({ t: 'diag', argc: arguments.length, hasThisCtx: (this && this.context) ? true : false,
               rdi: c.rdi.toString() });
      }
      lastStruct = c.rdi;
      var stack = bt(c, 5);
      var st = readStep();
      var sig = 'H1|' + stack.join('|') + '|' + st;
      if (!seen[sig]) {
        seen[sig] = 1;
        emit({ t: 'H1-hit', step: st, rdi: c.rdi.toString(), rax: c.rax.toString(),
               rbx: c.rbx.toString(), r14: c.r14.toString(), bt: stack });
      }
    } catch (e) { emitErrOnce('H1-err', e); }
  });

  // H3 入口：第一个实参 rcx 是目标结构体（函数内 mov rbx, rcx），rdx 是描述符
  attachAt(base, RVA_H3, 'H3-entry', function (args, ctx) {
    try {
      var c = cpuOf(this, ctx);
      if (c === null) { return; }
      var stack = bt(c, 5);
      var st = readStep();
      var sig = 'H3|' + stack.join('|') + '|' + st;
      if (!seen[sig]) {
        seen[sig] = 1;
        emit({ t: 'H3-hit', step: st, rcx_struct: c.rcx.toString(), rdx_desc: c.rdx.toString(),
               rbx: c.rbx.toString(), bt: stack });
      }
    } catch (e) { emitErrOnce('H3-err', e); }
  });

  // H1 末尾：五个字段已写入
  attachAt(base, RVA_H1_SNAP, 'H1-snapshot', function (args, ctx) {
    try {
      var c = cpuOf(this, ctx);
      if (c === null) { return; }
      var s = snapH1(c.rdi);
      if (s === null) { return; }
      s.t = 'snapH1';
      s.step = readStep();
      var sig = [s.addr, s.contrast, s.bright, s.scale, s.invert, s.protect, s.step].join(',');
      if (sig === lastSnapSig) { return; }
      lastSnapSig = sig;
      emit(s);
    } catch (e) { emitErrOnce('snapH1-err', e); }
  });

  // H3 末尾：深度/最小深度/混合模式已写入；此处 rbx 仍指向结构体
  attachAt(base, RVA_H3_SNAP, 'H3-snapshot', function (args, ctx) {
    try {
      var c = cpuOf(this, ctx);
      if (c === null) { return; }
      var s = snapH3(c.rbx);
      if (s === null) { return; }
      s.t = 'snapH3';
      s.step = readStep();
      var sig = [s.addr, s.mode, s.depth, s.minDepth, s.cov, s.useTex, s.step].join(',');
      if (sig === lastSnapSig) { return; }
      lastSnapSig = sig;
      emit(s);
    } catch (e) { emitErrOnce('snapH3-err', e); }
  });

  // 轮询采样 H1 那个对象：contrast 由 H1 末尾的尾跳调用写入，固定快照点（0x1a93a79）
  // 早于该写入，因此只能靠轮询在写入完成后取到；brightness 两条路都能取到，可互为校验。
  var lastSampleSig = '';
  setInterval(function () {
    try {
      if (lastStruct === null) { return; }
      var s = snapH1(lastStruct);
      if (s === null) { return; }
      var st = readStep();
      var sig = [s.addr, s.contrast, s.bright, s.scale, s.invert, s.protect, st].join(',');
      if (sig === lastSampleSig) { return; }
      lastSampleSig = sig;
      s.t = 'sampleH1';
      s.step = st;
      emit(s);
    } catch (e) { emitErrOnce('sample-err', e); }
  }, 200);

  emit({ t: 'armed' });

  try {
    var gf = new File(WORK + '/go.flag', 'w');
    gf.write('go');
    gf.close();
    emit({ t: 'go', flag: WORK + '/go.flag' });
  } catch (e) {
    emit({ t: 'go-fail', err: String(e) });
  }
}
