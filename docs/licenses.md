# 组件分发与来源

本软件的 Apache-2.0 许可不替代各组件许可。发布附件保留许可证、构建说明和适用的对应源码。

| 内容 | 来源与许可 | 分发方式 |
|---|---|---|
| 桌面源码 / CosyVoice | 本仓库及 QwenAudio/CosyVoice，Apache-2.0 | 保留历史、LICENSE、NOTICE |
| 模型 | FunAudioLLM/Fun-CosyVoice3-0.5B-2512，模型卡 Apache-2.0 | 从官方固定修订下载并逐文件校验 |
| WeText 中英文 TN FST（四个） | ModelScope pengzhendong/wetext，仓库声明 Apache-2.0 | 固定修订、原始字节随软件内置，保留完整许可、署名和逐文件 SHA-256 |
| Qt / PySide6 / Shiboken | Qt Project，所用模块按 LGPLv3 等对应许可 | 动态库目录式打包，附许可与对应源码来源 |
| FFmpeg | BtbN 固定版本 LGPL shared 构建 | 组件清单直接下载原始发布附件，不重新封装或转售该二进制 |
| Python / PyTorch / CUDA 依赖 | 各上游发布包 | 运行组件保留包内许可证和再分发通知 |
| FLEURS 两段参考录音 | Google 与 FLEURS 贡献者，CC-BY-4.0 | 附署名、样本修订、来源、格式修改说明 |
| AISHELL-3 四段参考录音 | Beijing Shell Shell Technology Co., Ltd. 与作者，Apache-2.0 | 附官方样例出处与完整许可 |

Qt 组件采用动态链接，未使用库替换限制；允许用户替换兼容 Qt 动态库、重新构建应用并调试修改。
对应源码与构建资料以每次发布的组件清单和源码附件为准，不以无版本的通用主页代替。
FFmpeg 不使用 GPL 的 libx264 构建；H.264 编码采用 NVIDIA NVENC 或 Windows Media Foundation。

WeText 资源固定为 `67928a15fac1e769e23189c53648a65dea206a39`，仅包含中英文
`tn/tagger.fst` 和 `tn/verbalizer.fst`，共 11,369,544 字节。它们与既有组件清单
中的四个文件完全相同，未重新编译或修改。来源为
[ModelScope 官方模型仓库](https://modelscope.cn/models/pengzhendong/wetext)，
并非此前误写的同名 Hugging Face 地址。安装这些资源无需额外网络下载。
完整许可和精确来源证据位于 `desktop_app/assets/wetext/`；构建会验证四个原始
校验值，并拒绝额外文件。许可声明来自官方仓库元数据，固定版本文件的大小与
校验值另由官方 SDK 文件清单核对；两类证据在 `PROVENANCE.json` 中分别记录。
这些通知同时进入安装包、源码包和第三方许可证附件。

音色库的 `manifest.json` 和各条目 `来源.json` 是逐条署名记录。
参考音频仅进行了明确记录的格式转换；文件名“试听_合成”是模型生成示例。
临时录音、自建音色和用户工程不进入源码及安装包。

2026-09-14 新选的龙婉 Longwan、龙书 Longshu、龙橙 longcheng 仅保存在本机
用户数据目录的 `voices/local-presets`。官网演示页说明学术展示用途，并称部分
示例来自互联网；网站仓库的通用 MIT 许可尚不足以确认这三段音频的具体再分发权限。
因此它们的原始录音、转换录音和本机合成示例均不进入 Git 或公开安装包。
不能套用本项目代码的 Apache-2.0 许可。取得明确覆盖实际用途和再分发的许可后，
才可用相同稳定 ID 加入精确打包白名单，并附相应许可和署名。
样本地址与当前验收状态见 [音色管理与录音](voice-management.md)。

官方资料：[Qt LGPL obligations](https://www.qt.io/development/open-source-lgpl-obligations)、
[FFmpeg legal](https://ffmpeg.org/legal.html)、
[CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/)。
