# 测试与发布验收

2026-09-09，本机完整测试：**45 passed，20 subtests passed**，9.78 秒，退出码 0。
运行环境为 Windows / Python 3.12 / PySide6 6.11.2，实际编码使用清单对应的 LGPL FFmpeg。
测试并未加载多种虚构显卡；硬件分类测试与真实 GPU 验证分别记录。

覆盖显卡系列/架构与驱动选择、多卡及显存限制、下载续传与校验、压缩包路径校验、
TXT/DOCX/PPTX、工程迁移和恢复、音色资源快照、损坏音频识别、取消与重试、
WAV/MP3/MP4 编码、真实 Windows 子进程树停止、Qt 界面流程，以及跨盘工程清单恢复。

- [真实模型合成与两套运行环境](engine-validation.md)
- [PowerPoint、视频计时与冻结 EXE 渲染](media-validation.md)
- [显卡验证范围](compatibility.md)
- [安装器与依赖的构建流程](build-release.md)

冻结程序支持重复运行安装验收：

```powershell
.\CosyVoice-Desktop.exe --verify-installation --report "安装验收.json"
```

它读取当前用户设置，通过隐藏的桌面界面走同一条显卡检测、真实模型合成和视频编码流程。
报告写入指定文件；全部通过返回 0，失败或超时返回 1，报告无法保存返回 2。
测试过程不上传诊断文件。

RTX 30、RTX 40、RTX 50 及笔记本的完整安装与模型验收尚需对应实机。
在开发机限制 PATH 可以验证程序没有调用全局 Python/FFmpeg，不能替代所有干净 Windows 设备的测试。
