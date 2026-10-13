# Codex Voice Announcer · Codex 语音播报

macOS 桌面播报工具。当 Codex 任务等待人工批准、回答、选择，或出现失败、未读结果时，读出任务名称和具体内容。

使用 Swift 原生控制窗口、macOS 系统语音和 Python 后台监听。可选 Apple Silicon 本地语音克隆。**默认不使用云端语音接口，不需要 API 密钥。**

## 功能

- 监听 Codex 本机任务，补播未加载聊天的未读结果，事件去重。
- 系统中文女声；可导入两组自己的参考音色。
- 语速、音调、清晰正式 / 柔和自然 / 沉稳坚定语气预设。
- 自定义语音先完整合成，再连续播放；支持播放中调节速度和音高。
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

## 自定义音色（可选）

需要 Apple Silicon、支持所用 MLX 版本的 macOS 和 Python 3.12+。模型约占 3.1 GB 磁盘；语音生成会使用数 GB 内存。低内存设备建议使用系统语音。

```sh
python3.12 scripts/setup_neural.py
python3 scripts/import_reference.py --slot 1 --audio /path/to/your-reference.wav --text '参考音频逐字稿'
```

在窗口音色菜单选择“自定义参考音色一”。第二组音色使用 `--slot 2`。音频应为清晰单人讲话，并有准确逐字稿。仅导入你拥有或获准使用的音频。

模型安装需要下载公开依赖和模型权重；实际语音合成显式离线。仓库不提供游戏 / 电影原声、声音样本、合成试听或模型权重。代码的 MIT 许可不覆盖你自行下载的模型和导入的音频。

## 卸载与隐私

```sh
python3 scripts/uninstall.py
```

停止后台，移除登录启动，将桌面应用移到废纸篓。个人设置、历史和参考音频保留在 `~/Library/Application Support/CodexVoiceAnnouncer/`，方便恢复。

任务历史和状态仅在本机保存，不进入仓库。程序只读 Codex 数据，**不替用户批准操作、开始任务或标记已读**。已知密钥形态会在语音和记录中脱敏，但这不是全面的数据防泄漏系统；公开日志前仍应自行检查。更多说明见 [隐私边界](docs/PRIVACY.md)。

## 兼容性边界

本工具使用 Codex 桌面的内部本机 IPC 和 SQLite 记录格式，**不是 OpenAI 官方产品，也不是稳定的公开 API**。目前兼容读取的格式包括线程流版本 11，以及 `state_5.sqlite`、`thread_history_1.sqlite` 和 `codex-dev.db`。Codex 更新可能要求适配；不兼容时窗口会显示连接或读取错误。

目录可包含已连接执行主机的任务；未读回补仅覆盖本机聊天。不保证看到未连接主机或所有云端聊天。测试中的输出切换为模拟恢复；真实蓝牙连接、断开仍需在各设备上验证。

参考克隆模型不支持任意情绪指令；语气预设调整音调、节奏和已有停顿。速度、音高在自定义语音播放中生效，停顿调整从下一条合成开始生效。系统语音参数从下一条播报生效。

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

扫描不会输出密钥原文。它检查索引中的实际文件内容，阻止本机用户路径、常见凭据、个人运行数据、音频及模型文件进入 Git；不能替代人工复核。

## 许可

[MIT](LICENSE)。第三方依赖、模型与用户音频遵守各自许可。相关来源见 [第三方说明](docs/THIRD_PARTY.md)。
