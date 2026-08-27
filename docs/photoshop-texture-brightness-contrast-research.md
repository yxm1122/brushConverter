# Photoshop 纹理亮度/对比度底层实现调研

## 结论摘要

- Adobe 官方只公开控件范围与行为说明，没有公开 Photoshop 纹理引擎或 Brightness/Contrast 的精确源代码。
- Photoshop 的亮度范围确认为 -150..150，对比度范围确认为 -50..100；这与新版 Brightness/Contrast 调整功能的控件范围一致，但不能据此证明纹理引擎复用了调整图层算法。
- Krita 的纹理实现已确认是独立算法：先减 brightness，再以 0.5 为中心乘 contrast。它不是 Photoshop 调整图层公式。
- “Use Legacy”在 Adobe 文档中被说明为简单平移像素；新版则不是简单平移，通常还会保护/重新分布高光和阴影。因此当前不能把 Photoshop 新版调整图层公式直接当作 ABR 纹理公式。

## 已确认的外部资料

- Adobe 官方 Brightness/Contrast 文档：<https://helpx.adobe.com/photoshop/using/apply-brightness-contrast-adjustment.html>。确认范围 -150..150 / -50..100，并区分 Use Legacy 与新版行为。
- Adobe 创建纹理笔刷文档：<https://helpx.adobe.com/photoshop/using/creating-textured-brushes.html>。确认纹理设置支持 Brightness/Contrast 调整，但未公开计算公式。
- Krita 纹理文档：<https://docs.krita.org/zh_CN/reference_manual/brushes/brush_settings/texture.html>。说明亮度/对比度是独立滤镜，并强调不同纹理模式可能需要不同数值。
- Krita 源码 `KisTextureMaskInfo::recalculateMask()`：brightness 是减法，contrast 是围绕 0.5 的乘法。

## Photoshop 新版调整图层假设

可作为候选模型的常见形式是对归一化像素 x 做线性变换：

```text
x1 = x + B                 # 或以 B/150 为参数的受限亮度变换
x2 = (x1 - 0.5) * C + 0.5
clip(x2, 0, 1)
```

但 Photoshop 新版 Brightness/Contrast 的实际实现可能包含：

- 亮度先改变中间调而非单纯平移；
- 对高光/阴影进行保护或重新映射；
- 对比度滑块采用非线性曲线或查找表；
- 8-bit/16-bit、色彩空间、Gamma/线性空间差异；
- 纹理模式内部可能先调整纹理亮度，再将纹理与笔尖 alpha 合成。

所以仅凭控件范围相同，无法推出精确公式。

## 当前项目中的经验映射

当前实现使用用户提供的控件范围和 Krita 源码参数范围：

- 普通模式亮度：`round(0.10 - PS亮度/250, 2)`；Linear Height Photoshop：`round(0.30 - PS亮度/250, 2)`；限制到 Krita [-1,1]。
- 普通模式对比度使用 Photoshop 因子；Linear Height Photoshop 使用其倒数；最终限制到 Krita [0,2]，并按两位小数序列化。
- Krita 运行时再执行 `maskValue -= brightness` 与 `((maskValue - 0.5) * contrast) + 0.5`。

这保证了范围、零点和 Krita 内部数值语义一致，但仍属于经验映射，不是 Photoshop 公式的证明。

## 手动 KPP 样本的证据

`tests/大怪兽-软铅【用数位板】_复制.kpp` 中观察到：

- `TexturingMode=15`（Linear Height Photoshop）；
- `Brightness=-0.06`；
- `Contrast=1.14`。

对应原 ABR 软铅参数约为 brightness=-27、contrast=-17。由于该 KPP 是手动粗调结果，只能说明合理数量级和方向，不能唯一确定公式。若将其作为两点校准：

- brightness 的比例约为 0.06/27 ≈ 0.0022，而非简单的 1/150 ≈ 0.0067；
- contrast=-17 对应 1.14，与当前按负区间映射得到 0.66 方向相反；
- 这表明手动 KPP 很可能是视觉粗调，或 Photoshop/Krita 对比度语义方向不同，不能直接拟合为精确公式。

## 建议的精确验证方案

要真正反推出 Photoshop 纹理公式，最可靠的方式不是调整图层，而是制作受控样本：

1. 在 Photoshop 中使用同一张 0/64/128/192/255 灰阶纹理。
2. 固定笔尖、深度=100%、无动态、无反相、模式分别选择 Linear Height、Height、Subtract、Multiply。
3. 只改变 Brightness，分别记录 -150、-100、-50、0、50、100、150 的输出纹理或笔画 alpha。
4. 固定 Brightness=0，只改变 Contrast，记录 -50、0、25、50、75、100。
5. 由输出灰阶点拟合曲线：先判断是否为线性变换，再判断是否有 gamma、S 曲线、阴影/高光保护或模式相关分支。
6. 对比同参数的新版 Brightness/Contrast 调整图层。如果输出完全一致，才能确认用户的假设。

目前项目缺少 Photoshop 批量导出这些中间结果的能力，因此本轮只能确认控件范围、Krita 公式和 KPP 经验值，不能声称已经得到 Photoshop 的精确底层公式。