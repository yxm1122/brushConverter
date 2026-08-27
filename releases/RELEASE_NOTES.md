# brushConverter v1.1.0

纹理映射、笔尖尺寸和旋转映射校准版本。

## 主要变化

- 新增 ABR `patt` 纹理区段解析，按 UUID 关联纹理。
- 将纹理 PNG 以 `type="patterns"` 资源内嵌到 Krita `.kpp`。
- 支持 Krita Photoshop 专用纹理模式：Height、Linear Height、Hard Mix。
- 根据 Photoshop/Krita 定量对照更新亮度和对比度映射。
- 适配 Krita UI 的参数精度：亮度和对比度按两位小数写入，并限制到 Krita 实际范围。
- 非正方形采样笔尖在内嵌前使用白色边框补齐为正方形。
- 笔尖缩放基于宽高较大值，修复 Krita 预设编辑器与绘图区尺寸不一致。
- Photoshop 方向抖动映射到 Krita `RotationValue`；随机度曲线使用 XML 归一化坐标 `0,0;1,1;`。

## 验证

- 真实 ABR 纹理解析、RLE、KPP XML、模式映射、尺寸和旋转回归测试通过。
- 测试套件：35 项通过。
- 已生成 19 个预设的新示例 `.bundle`。

## 已知限制

- Photoshop 与 Krita 的纹理模式算法不同，亮度/对比度映射使用定量样本校准常数，不保证所有笔尖和深度完全等价。
- Photoshop 对比度高于约 +50 时会快速饱和，Krita 对比度范围为 0~2，无法一一表示极端值。
- 双笔刷、颜色动态、湿边和喷嘴仍未映射。
