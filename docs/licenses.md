# 组件分发与来源

本软件的 Apache-2.0 许可不替代各组件许可。发布附件保留许可证、构建说明和适用的对应源码。

| 内容 | 来源与许可 | 分发方式 |
|---|---|---|
| 桌面源码 / CosyVoice | 本仓库及 QwenAudio/CosyVoice，Apache-2.0 | 保留历史、LICENSE、NOTICE |
| 模型 | FunAudioLLM/Fun-CosyVoice3-0.5B-2512，模型卡 Apache-2.0 | 从官方固定修订下载并逐文件校验 |
| Qt / PySide6 / Shiboken | Qt Project，所用模块按 LGPLv3 等对应许可 | 动态库目录式打包，附许可与对应源码来源 |
| FFmpeg | BtbN 固定版本 LGPL shared 构建 | 组件清单直接下载原始发布附件，不重新封装或转售该二进制 |
| Python / PyTorch / CUDA 依赖 | 各上游发布包 | 运行组件保留包内许可证和再分发通知 |
| FLEURS 两段参考录音 | Google 与 FLEURS 贡献者，CC-BY-4.0 | 附署名、样本修订、来源、格式修改说明 |
| AISHELL-3 四段参考录音 | Beijing Shell Shell Technology Co., Ltd. 与作者，Apache-2.0 | 附官方样例出处与完整许可 |

Qt 组件采用动态链接，未使用库替换限制；允许用户替换兼容 Qt 动态库、重新构建应用并调试修改。
对应源码与构建资料以每次发布的组件清单和源码附件为准，不以无版本的通用主页代替。
FFmpeg 不使用 GPL 的 libx264 构建；H.264 编码采用 NVIDIA NVENC 或 Windows Media Foundation。

音色库的 `manifest.json` 和各条目 `来源.json` 是逐条署名记录。
参考音频仅进行了明确记录的格式转换；文件名“试听_合成”是模型生成示例。
临时录音、自建音色和用户工程不进入源码及安装包。

官方资料：[Qt LGPL obligations](https://www.qt.io/development/open-source-lgpl-obligations)、
[FFmpeg legal](https://ffmpeg.org/legal.html)、
[CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/)。
