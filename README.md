# Codex Voice Announcer · Codex 语音播报

macOS 桌面播报工具。当 Codex 任务等待人工批准、回答、选择，或出现失败、未读结果时，读出任务名称和具体内容。

使用 Swift 原生控制窗口、macOS 系统语音和 Python 后台监听。可选 Apple Silicon 本地语音克隆。**默认不使用云端语音接口，不需要 API 密钥。**

## 功能

- 监听 Codex 本机任务，补播未加载聊天的未读结果，事件去重。
- 系统中文女声；预置守望者、红皇后、蓝染三种参考音色，可再导入三组自己的音色。
- 语速、音调、清晰正式 / 柔和自然 / 沉稳坚定语气预设。
- 角色语音按句分批合成，完整缓存后连续播放；支持播放中调节速度和音高。
- 每批音频在本机校验实际语言、内容及重复；不合格则使用系统中文备用播报。
- 缩短异常长静音，仅在设置变化时更新音频参数；播放中断不自动从头重复。
- 音频输出切换后重新配置播放器，从原位置继续。
- 暂停、停止、重播、打开对应任务；登录时启动后台。

## 安装

基础版需要 macOS 13+、Python 3.9+、Xcode Command Line Tools，以及已安装并登录的 Codex 桌面应用。基础版无需第三方 Python 包。

```sh
git clone https://github.com/liangyi0006583/codex-voice-announcer.git
cd codex-voice-announcer
xcode-select --install  # 已安装 Command Line Tools 时跳过
python3 scripts/install.py
```

桌面会生成 `Codex Voice Announcer.app`。双击打开控制窗口。使用固定、长期可用的 Python 解释器安装；不要使用即将删除的临时环境。可以用 `--python` 指定后台解释器。

这是从源码构建的未签名应用，不提供开发者签名或公证发行包。

## 角色音色与自定义音色（可选）

需要 Apple Silicon、支持所用 MLX 版本的 macOS 和 Python 3.12+。语音生成和校验模型权重合计约 3.6 GB，不含依赖与缓存；语音生成会使用数 GB 内存。低内存设备建议使用系统语音。

```sh
python3.12 scripts/setup_neural.py
# 模型装好后可直接在音色菜单选择三种预置音色
# 导入其他自己的音色时：
python3 scripts/import_reference.py --slot 1 --audio /path/to/your-reference.wav --text '参考音频逐字稿' --language Chinese
```

在窗口音色菜单选择“自定义参考音色一”。另外两组音色使用 `--slot 2` / `--slot 3`。音频应为清晰单人讲话，并有准确逐字稿。仅导入你拥有或获准使用的音频。

模型安装会下载公开依赖、固定版本的 Qwen3-TTS 与 Whisper 权重；实际语音合成及内容校验均显式离线，不需要 ffmpeg。升级旧版本后请重新运行 `scripts/setup_neural.py`，补齐校验依赖和模型。

仓库包含三段角色参考片段及三段通用中文试听；不包含完整音频档案、模型权重、私人参考音频、真实任务播报或诊断录音。第三方参考素材和衍生试听不适用代码的 MIT 许可，来源与权利说明见 [声音素材说明](voices/NOTICE.md)，文件校验值见 [素材清单](voices/manifest.json)。

试听：[守望者](samples/warden-zh.wav) · [红皇后](samples/red-queen-zh.wav) · [蓝染](samples/aizen-zh.wav)

参考音色由模型合成，不是游戏或电影官方发布的声音模型。跨语言中文播报使用仅提取音色的方式；同语种高级用法可选择 `--clone-mode icl`。自定义参考目录、逐字稿和试听输出均保存在本机。

## 卸载与隐私

```sh
python3 scripts/uninstall.py
```

停止后台，移除登录启动，将桌面应用移到废纸篓。个人设置、历史和参考音频保留在 `~/Library/Application Support/CodexVoiceAnnouncer/`，方便恢复。

任务历史和状态仅在本机保存，不进入仓库。程序只读 Codex 数据，**不替用户批准操作、开始任务或标记已读**。已知密钥形态会在语音和记录中脱敏，但这不是全面的数据防泄漏系统；公开日志前仍应自行检查。更多说明见 [隐私边界](docs/PRIVACY.md)。

## 兼容性边界

本工具使用 Codex 桌面的内部本机 IPC 和 SQLite 记录格式，**不是 OpenAI 官方产品，也不是稳定的公开 API**。目前兼容读取的格式包括线程流版本 11，以及 `state_5.sqlite`、`thread_history_1.sqlite` 和 `codex-dev.db`。Codex 更新可能要求适配；不兼容时窗口会显示连接或读取错误。

目录可包含已连接执行主机的任务；未读回补仅覆盖本机聊天。不保证看到未连接主机或所有云端聊天。测试中的输出切换为模拟恢复；真实蓝牙连接、断开仍需在各设备上验证。

参考克隆模型不支持任意情绪指令；语气预设调整音调、节奏和已有停顿。长正文按完整句子分批生成，降低内存峰值；增加的语言校验会延长开始前等待时间。速度、音高在自定义语音播放中生效，停顿调整从下一条合成开始生效。系统语音参数从下一条播报生效。

## 开发与测试

```sh
python3 -m unittest discover -s tests -p test_monitor.py
python3 -m unittest discover -s tests -p test_install.py
python3 scripts/build.py
```

macOS 音频测试需要 NumPy。默认仅测试离线变速、变调；设置环境变量才使用真实输出设备，测试音频由代码生成：

```sh
python3 -m pip install numpy
python3 tests/test_audio.py
CODEX_VOICE_AUDIO_TESTS=1 python3 tests/test_audio.py
# 安装可选神经语音依赖后：
CODEX_VOICE_AUDIO_TESTS=1 python3 tests/test_neural.py
```

提交前运行：

```sh
python3 scripts/check_publish.py --staged
```

扫描不会输出密钥原文。它检查索引中的实际文件内容，阻止本机用户路径、常见凭据、个人运行数据和模型进入 Git。只有素材清单内六个明确允许的 WAV 可上传，并检查格式、大小、元数据和 SHA-256；其他音频仍被阻止。扫描不能代替对音频内容的人工审查。

## 许可

代码采用 [MIT](LICENSE)。第三方依赖、模型、参考片段和衍生音色试听不包含在代码 MIT 许可中。相关来源见 [第三方说明](docs/THIRD_PARTY.md)。

## 临时音频清理

正常播报生成的 WAV 和分段校验音频会在播放结束、正常停止或异常退出时由临时目录上下文清理。强制杀进程、系统崩溃等未执行清理代码的情形可能残留，不能保证即时删除。显式导出的试听、预置素材、用户参考文件、模型和诊断录音不会自动删除。仓库提供的样本只使用通用测试文案。
