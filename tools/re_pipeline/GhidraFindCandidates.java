// GhidraFindCandidates.java —— 在 Ghidra 12.2 里按「已知字符串地址」收集函数级交叉引用。
//
// 用途（任务 11）：pe_scan.py / native_xref.py 已经用字节级扫描定位了字符串的虚拟地址，
// 本脚本把这些地址交给 Ghidra 的引用数据库，输出「引用该字符串的函数」清单，
// 用于与免反汇编路径的结果互相印证。
//
// 参数（-postScript 后面依次传入）：
//   1) 输出 TSV 路径（绝对路径，UTF-8）
//   2..n) 目标虚拟地址（十六进制，可带 0x 前缀）
//
// 输出：
//   - 首部若干行 "#" 注释：程序名、镜像基址、函数总数、符号总数、语言/编译器规格；
//   - 之后每行一条： 目标地址 \t 引用地址 \t 引用类型 \t 所属函数入口 \t 函数名 \t 函数体大小
//   无 -- 引用（No references）时也写一行，便于统计为 0。
//
// 只读：脚本不修改程序字节，只读取引用/函数表；不保存程序（除非外部要求）。
//@category REA

import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionIterator;
import ghidra.program.model.mem.MemoryBlock;
import ghidra.program.model.symbol.Reference;
import ghidra.program.model.symbol.ReferenceIterator;
import ghidra.program.model.symbol.Symbol;
import ghidra.program.model.symbol.SymbolIterator;

import java.io.BufferedWriter;
import java.io.File;
import java.io.FileOutputStream;
import java.io.OutputStreamWriter;
import java.nio.charset.StandardCharsets;
import java.util.LinkedHashMap;
import java.util.Map;

public class GhidraFindCandidates extends GhidraScript {

    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length < 2) {
            println("[GhidraFindCandidates] 用法：<输出TSV> <地址1> [地址2 ...]");
            return;
        }
        File outFile = new File(args[0]);
        if (outFile.getParentFile() != null) {
            outFile.getParentFile().mkdirs();
        }

        // ---- 程序概况（用于报告里的「符号数量」等具体数字）----
        int funcCount = 0;
        FunctionIterator fit = currentProgram.getFunctionManager().getFunctions(true);
        while (fit.hasNext()) {
            fit.next();
            funcCount++;
        }
        int symCount = 0;
        SymbolIterator sit = currentProgram.getSymbolTable().getAllSymbols(true);
        while (sit.hasNext()) {
            Symbol s = sit.next();
            if (s == null) {
                break;
            }
            symCount++;
        }
        StringBuilder head = new StringBuilder();
        head.append("# program\t").append(currentProgram.getName()).append("\n");
        head.append("# image_base\t").append(currentProgram.getImageBase()).append("\n");
        head.append("# language\t").append(currentProgram.getLanguageID()).append("\n");
        head.append("# compiler_spec\t").append(currentProgram.getCompilerSpec().getCompilerSpecID()).append("\n");
        head.append("# function_count\t").append(funcCount).append("\n");
        head.append("# symbol_count\t").append(symCount).append("\n");
        for (MemoryBlock b : currentProgram.getMemory().getBlocks()) {
            head.append("# block\t").append(b.getName()).append("\t")
                .append(b.getStart()).append("\t").append(b.getEnd()).append("\t")
                .append("exec=").append(b.isExecute()).append("\n");
        }
        println("[GhidraFindCandidates] 函数总数=" + funcCount + " 符号总数=" + symCount);

        // ---- 逐地址收集引用 ----
        try (BufferedWriter w = new BufferedWriter(new OutputStreamWriter(
                new FileOutputStream(outFile), StandardCharsets.UTF_8))) {
            w.write(head.toString());
            w.write("#target_va\treferer_va\tref_type\tfunc_entry\tfunc_name\tfunc_size\tfunc_ref_count\n");
            for (int i = 1; i < args.length; i++) {
                String hex = args[i].trim();
                if (hex.isEmpty()) {
                    continue;
                }
                Address target;
                try {
                    target = toAddr(hex);
                } catch (Exception e) {
                    w.write(hex + "\t<非法地址>\t\t\t\t0\t0\n");
                    continue;
                }
                // 统计每个函数对目标地址的引用次数
                Map<String, Integer> perFunc = new LinkedHashMap<String, Integer>();
                Map<String, String[]> funcInfo = new LinkedHashMap<String, String[]>();
                int total = 0;
                ReferenceIterator refs = currentProgram.getReferenceManager().getReferencesTo(target);
                while (refs.hasNext()) {
                    Reference r = refs.next();
                    if (r == null) {
                        break;
                    }
                    total++;
                    Address from = r.getFromAddress();
                    Function f = currentProgram.getFunctionManager().getFunctionContaining(from);
                    String key = (f == null) ? "<no-function>" : f.getEntryPoint().toString();
                    String fname = (f == null) ? "<no-function>" : f.getName();
                    long fsize = (f == null) ? 0 : f.getBody().getNumAddresses();
                    perFunc.put(key, Integer.valueOf(perFunc.getOrDefault(key, 0).intValue() + 1));
                    funcInfo.put(key, new String[] { fname, Long.toString(fsize), r.getReferenceType().toString() });
                }
                if (total == 0) {
                    w.write(hex + "\t<无引用>\t\t\t\t0\t0\n");
                    println("[GhidraFindCandidates] " + hex + " 无引用");
                    continue;
                }
                for (Map.Entry<String, Integer> e : perFunc.entrySet()) {
                    String[] info = funcInfo.get(e.getKey());
                    w.write(hex + "\t" + e.getKey() + "\t" + info[2] + "\t" + e.getKey() + "\t"
                            + info[0] + "\t" + info[1] + "\t" + e.getValue() + "\n");
                }
                println("[GhidraFindCandidates] " + hex + " 引用总数=" + total + " 涉及函数=" + perFunc.size());
            }
        }
        println("[GhidraFindCandidates] 已写出 " + outFile.getAbsolutePath());
    }
}
