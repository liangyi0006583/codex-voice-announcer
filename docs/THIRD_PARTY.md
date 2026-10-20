# 第三方组件

本项目 MIT 许可只覆盖仓库中的原创代码和文档。以下软件组件在用户电脑上自行提供或下载，不打包进仓库；短音频素材单列于文末：

- Apple AppKit、AVFoundation、CoreAudio 和系统语音：系统提供，遵循 Apple 对应条款。
- Python 标准库：由用户的 Python 解释器提供。
- [MLX](https://github.com/ml-explore/mlx) 与 [MLX Audio](https://github.com/Blaizzy/mlx-audio)：可选本地推理运行时，遵循上游许可。
- [Qwen3-TTS Base](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-Base)：可选参考克隆模型。
- [MLX 8-bit 模型转换](https://huggingface.co/mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit)：安装工具固定到版本 `e7dd0585652209fa0d7783659aad4e8a324de11c`。
- NumPy、Transformers、Hugging Face Hub 及其依赖：可选工具链，遵循各自许可。
- Codex 桌面应用：另行安装；本工具与 OpenAI 无官方关联。

声音素材：仓库包含三段角色参考片段及三段通用中文合成试听。来源、归属及使用边界见 [素材说明](../voices/NOTICE.md)。它们不纳入代码 MIT 许可；没有提供完整声音档案、模型权重、访问令牌、实际任务音频或私人训练数据。

- [MLX Whisper](https://github.com/ml-explore/mlx-examples/tree/main/whisper)：本地自动语音识别；Whisper small MLX 固定版本为 `45f3915923c7a79a5a5b5a7d909d39aeb0e5630e`。
- OpenCC：校验前统一繁简文字，避免同一句话因识别字形不同而被误拒绝。
