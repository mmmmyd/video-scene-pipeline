# 测试输入视频

## 块状水池

`pool/` 包含用户提供的同一块状水池的三个视角原始视频：

- `块状水池1.mp4`
- `块状水池2.mp4`
- `块状水池3.mp4`

文件按原样保存，没有压缩或重新编码。项目根目录 `project.json` 已引用这三个相对路径；配置模型服务、环境变量及 Blender 后，在项目根目录执行 `python start.py` 即可开始。

## 复杂台阶

`complex_steps/` 包含三个原始视频，均按原样保存，没有压缩或重新编码：

- `复杂台阶.mp4`
- `复杂台阶_内.mp4`
- `复杂台阶_外.mp4`

测试这个场景时，将根目录 `project.json` 的 `input.videos` 替换为：

```json
[
  "test_inputs/complex_steps/复杂台阶.mp4",
  "test_inputs/complex_steps/复杂台阶_内.mp4",
  "test_inputs/complex_steps/复杂台阶_外.mp4"
]
```

同时将 `output.directory` 设为新的输出目录，例如 `outputs/complex_steps`，然后仍用 `python start.py` 运行。

这些视频是实际场景的输入样本，并非已通过重建质量验收的基准结果。`validation/example_delivery.zip` 是软件流程验证样例，使用的是另一组软件测试素材，不能当作上述视频的重建输出。
