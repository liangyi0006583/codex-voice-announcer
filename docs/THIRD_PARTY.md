# 第三方组件

本项目 MIT 许可只覆盖仓库中的原创代码和文档。以下组件在用户电脑上自行提供或下载，不打包进仓库：

- Apple AppKit、AVFoundation、CoreAudio 和系统语音：系统提供，遵循 Apple 对应条款。
- Python 标准库：由用户的 Python 解释器提供。
- [MLX](https://github.com/ml-explore/mlx) 与 [MLX Audio](https://github.com/Blaizzy/mlx-audio)：可选本地推理运行时，遵循上游许可。
- [Qwen3-TTS Base](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-Base)：可选参考克隆模型。
- [MLX 8-bit 模型转换](https://huggingface.co/mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit)：安装工具固定到版本 `e7dd0585652209fa0d7783659aad4e8a324de11c`。
- NumPy、Transformers、Hugging Face Hub 及其依赖：可选工具链，遵循各自许可。
- Codex 桌面应用：另行安装；本工具与 OpenAI 无官方关联。

用户导入的音频不纳入本项目许可。没有随仓库提供任何游戏或电影角色声音、参考素材、合成试听、模型权重、访问令牌或训练数据。
