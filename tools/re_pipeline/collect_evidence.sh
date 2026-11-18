#!/usr/bin/env bash
# collect_evidence.sh —— 任务 11 证据采集一键脚本（只读，不改 PS 安装目录）
# 逐条执行取证命令并把「命令原文 + 原始输出」落到 docs/研究报告/附件/证据汇总-原始输出尾部.txt
set -u
cd "$(dirname "$0")/../.." || exit 1
# 环境变量优先（BC_PY / BC_PS_EXE），本机默认值兜底（不含身份信息；换机器时用环境变量覆盖）
PY="${BC_PY:-D:/program/anaconda3/envs/brushConverter/python.exe}"
EXE="${BC_PS_EXE:-D:/program/PS2025/Adobe Photoshop 2025/Photoshop.exe}"
OUT="docs/研究报告/附件/证据汇总-原始输出尾部.txt"
: > "$OUT"

run() {
  echo "" >> "$OUT"
  echo "================================================================" >> "$OUT"
  echo "\$ $*" >> "$OUT"
  echo "----------------------------------------------------------------" >> "$OUT"
  eval "$@" >> "$OUT" 2>&1
}

run "\"$PY\" tools/re_pipeline/pe_scan.py --exe \"$EXE\" --needles 'Linear Height,Hard Mix,Linear Dodge,blend_linear_height,blend_hard_mix,textureBrightness,textureContrast,textureBlendMode' --out 'docs/研究报告/附件/ps_string_hits.json'"
run "\"$PY\" tools/re_pipeline/native_xref.py refs --exe \"$EXE\" --needles 'Linear Height,Hard Mix,Linear Dodge,blend_linear_height,blend_hard_mix,blend_height,blend_multiply,blend_normal,blend_pass_through,blend_custom_table,linearHeight,hardMix,linearDodge,Blend Modes,Multiply,Subtract' --out 'docs/研究报告/附件/ps_xref_blend.json'"
run "\"$PY\" tools/re_pipeline/native_xref.py refs --exe \"$EXE\" --needles 'textureBrightness,textureContrast,textureBlendMode,textureDepth,textureScale,invertTexture,protectTexture,useTexture,textureCoverage,minimumDepth,textureDepthDynamics' --out 'docs/研究报告/附件/ps_xref_texture.json'"
run "\"$PY\" tools/re_pipeline/native_xref.py range --exe \"$EXE\" --start 0x141a93000 --end 0x141a96800"
run "\"$PY\" tools/re_pipeline/native_xref.py callers --exe \"$EXE\" --funcs 0x141a935f0,0x141a96230,0x141a8b470,0x141a8e060"
run "\"$PY\" tools/re_pipeline/native_xref.py disasm --exe \"$EXE\" --func 0x141b94750 --limit 12"
run "\"$PY\" tools/re_pipeline/native_xref.py disasm --exe \"$EXE\" --func 0x141a939d5 --limit 12"
run "\"$PY\" tools/re_pipeline/native_xref.py dump --exe \"$EXE\" --start 140046240 --end 140046440"
echo "已写出 $OUT"
tail -30 "$OUT"
