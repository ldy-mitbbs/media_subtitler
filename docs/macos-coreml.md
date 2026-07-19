# CoreML 编码器加速（Apple Silicon 可选）

`scripts/setup-macos.sh` 默认安装的 whisper.cpp（Homebrew bottle 或你自己
`cmake -DGGML_METAL=ON -DGGML_ACCELERATE=ON` 编译的版本）走的是 **Metal / GPU**
路径。两者构建配置基本等价 —— Homebrew formula 并没有关掉 Metal 或 Accelerate，
它只是让这些选项保持默认值，而在 Apple Silicon 上这些默认值本来就是 `ON`。

也就是说：**单纯"从源码编译"并不会更快**。

唯一在原理上可能更快的选项是 CoreML 编码器（把 encoder 放到 Neural Engine 上
跑）。网上常见的说法是 encoder 能快 2-3 倍。

> ⚠️ **本仓库实测结论：在 M 系列 + macOS 26 + large-v3-turbo 上，CoreML 比
> Metal 慢约 40%。** 具体数字见下方「实测对比」。请不要为了 CoreML 去装
> 完整版 Xcode。本文保留下来，是为了记录这个否定结论和复现方法。

## 前置条件：必须装完整版 Xcode

⚠️ **只装 Command Line Tools 是不够的。** 模型转换的最后一步要用 `coremlc`
把 `.mlpackage` 编译成 `.mlmodelc`，而这个工具只随**完整版 Xcode** 分发。
只有 CLT 的机器会在转换快结束时报错：

```
xcrun: error: unable to find utility "coremlc", not a developer tool or in PATH
```

先确认：

```bash
xcode-select -p          # 若输出 /Library/Developer/CommandLineTools 则不够
xcrun --find coremlc     # 能找到才可以继续
```

装法（约 10-17GB，从 App Store 或 developer.apple.com 下载 Xcode 后）：

```bash
sudo xcode-select -s /Applications/Xcode.app/Contents/Developer
sudo xcodebuild -license accept
```

## 实测对比：CoreML 反而更慢

同一台机器、同一个 605 秒音频、同一个 `ggml-large-v3-turbo.bin`，
`whisper-cli -t 8`，各跑 3 次；CoreML 的第 1 次跑（ANE kernel 首次编译）已排除：

| 构建 | encode | 总计 | 实时倍率 |
| --- | --- | --- | --- |
| Metal（默认） | **5546 ms** | **7788 ms** | **78x** |
| CoreML | 7812 ms | 10875 ms | 56x |

- encoder：**0.71x**（慢 40%）
- 端到端：**0.72x**
- 2 小时电影：93 秒 → 129 秒

CoreML 确实生效了（启动日志有 `loading Core ML model` 和 `COREML = 1`），
它只是**在这个组合下更慢**。合理的解释是：Neural Engine 的设计目标是低功耗，
峰值吞吐并不如新款 M 系列的 GPU；而 `large-v3-turbo` 的 encoder 又足够大，
在 Metal 上能把 GPU 喂满。网上那些 2-3 倍的说法多来自较早的芯片和较小的模型。

### 结论

**不要为了 CoreML 装完整版 Xcode。** 默认的 Metal 路径（Homebrew bottle 或
`scripts/setup-macos.sh` 装的那份）就是这台机器上最快的选择。

转写本来也不是瓶颈：605 秒音频 7.8 秒跑完（78x 实时），30 分钟一集约 23 秒，
2 小时电影约 93 秒。真正花时间的是**翻译**（DeepSeek API，几百条字幕，通常几
分钟）。要提速就去调 `TRANSLATION_CHUNK_SIZE` 或换更快的翻译模型。

### 什么情况下也许还值得再测一次

- 换了芯片代际（尤其是 ANE 大改的机型）。
- 换成**更小的模型**（`base` / `small`）—— 小模型在 ANE 上的相对表现通常更好。
- whisper.cpp 的 CoreML 后端有大的更新。

复现方法见下方「步骤」，测完请回来更新上面这张表。

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
之后会走缓存。

**不要用 `otool -L .../whisper-cli | grep -i coreml` 来判断**，这个检查两头都会错：

- 如果构建目录名里带 `coreml`（例如 `build-coreml/`），`otool` 报错时输出的路径
  本身就含 `coreml`，会**假阳性**；
- 默认是动态库构建，`whisper-cli` 只直接链接 `libwhisper.1.dylib`，CoreML 在
  `whisper-cli -> libwhisper -> libwhisper.coreml.dylib -> CoreML.framework`
  这条链的更深处，直接查 CLI 会**假阴性**。

用运行时输出判断才可靠：

```bash
./build/bin/whisper-cli -m models/ggml-large-v3-turbo.bin -f samples/jfk.wav -nt 2>&1 \
  | grep -iE "core ?ml|COREML"
```

启用成功时会看到：

```
whisper_init_state: loading Core ML model from '.../ggml-large-v3-turbo-encoder.mlmodelc'
whisper_init_state: Core ML model loaded
system_info: ... WHISPER : COREML = 1 | ...
```
