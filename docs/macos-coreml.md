# CoreML 编码器加速（Apple Silicon 可选）

`scripts/setup-macos.sh` 默认安装的 whisper.cpp（Homebrew bottle 或你自己
`cmake -DGGML_METAL=ON -DGGML_ACCELERATE=ON` 编译的版本）走的是 **Metal / GPU**
路径。两者构建配置基本等价 —— Homebrew formula 并没有关掉 Metal 或 Accelerate，
它只是让这些选项保持默认值，而在 Apple Silicon 上这些默认值本来就是 `ON`。

也就是说：**单纯"从源码编译"并不会更快**。唯一真正值得折腾的从源码构建选项是
CoreML 编码器 —— 它把 encoder 放到 Neural Engine 上跑，encoder 大约能快 2-3 倍，
而 encoder 正是转写耗时的主要部分。

## 何时值得做

- 你经常处理**长视频**（>30 分钟），转写时间是瓶颈。
- 你愿意为**每个模型**做一次约 5-10 分钟的一次性转换。

如果只是偶尔跑几个短片，Metal 路径已经够快，不必折腾。

## 步骤

```bash
# 1. 转换模型需要的一次性依赖（建议单独的临时 venv，别污染项目 .venv）
python3.11 -m venv /tmp/coreml-venv
source /tmp/coreml-venv/bin/activate
pip install "torch<2.5" "coremltools>=7.0" openai-whisper ane_transformers

# 2. 生成 .mlmodelc（在 whisper.cpp checkout 里执行）
cd ~/code/whisper.cpp
./models/generate-coreml-model.sh large-v3-turbo
deactivate

# 3. 重新编译，打开 CoreML
cmake -S . -B build \
  -DCMAKE_BUILD_TYPE=Release \
  -DGGML_METAL=ON \
  -DGGML_ACCELERATE=ON \
  -DWHISPER_COREML=ON \
  -DWHISPER_COREML_ALLOW_FALLBACK=ON
cmake --build build --config Release -j "$(sysctl -n hw.logicalcpu)"
```

`WHISPER_COREML_ALLOW_FALLBACK=ON` 很重要：如果某个模型没有对应的 `.mlmodelc`，
它会自动回退到 Metal 路径，而不是直接报错。

## 让本项目用上它

`.mlmodelc` 必须和 ggml 模型放在同一个目录、且文件名前缀一致，例如：

```
~/code/whisper.cpp/models/ggml-large-v3-turbo.bin
~/code/whisper.cpp/models/ggml-large-v3-turbo-encoder.mlmodelc
```

然后在 `settings.json` 里指向这套自编译的二进制和模型：

```json
{
  "WHISPER_CPP_COMMAND": "/Users/<you>/code/whisper.cpp/build/bin/whisper-cli",
  "WHISPER_CPP_MODEL_PATH": "/Users/<you>/code/whisper.cpp/models/ggml-large-v3-turbo.bin"
}
```

`scripts/setup-macos.sh` 会自动探测 `~/code/whisper.cpp/build/bin/whisper-cli`
并复用它，所以重跑安装脚本不会把这套配置覆盖掉。

## 验证

第一次用 CoreML 跑某个模型时，加载会明显变慢（系统在编译 ANE kernel，属于正常现象），
之后会走缓存。确认确实启用了：

```bash
otool -L build/bin/whisper-cli | grep -i coreml   # 应该有 CoreML.framework
```

转写时 stderr 里会打印 `whisper_init_state: loading Core ML model from ...`。
