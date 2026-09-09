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

## 冻结 EXE 的真实自检

最终 PyInstaller EXE 已于同日通过隐藏安装自检，退出码 0、complete=true。
启动 PATH 仅保留 Windows 与 System32；推理组件位于含中文和空格的独立目录。
RTX 2070 Super 实际 CUDA 运算及模型合成成功，生成 24 kHz、11.96 秒语音，
随后通过 h264_nvenc 编码成 359 帧、11.966667 秒视频。
首次模型加载约 91.96 秒、合成约 19.08 秒；并行安装和打包期间的时间不作为性能基准。

修复了两类冻结环境冲突：构建工具从其他软件 PATH 收集错误 ICU DLL，以及
推理 Python 3.10 提前查找到界面 Python 3.12 扩展。构建 PATH 和工作进程依赖查找现分别隔离，
并通过实际 QProcess、控制线程失败和完整模型回归验证。正常退出未再出现应用程序错误。

公开仓库 Windows CI 已通过：
[Desktop tests](https://github.com/doudou03015/CosyVoice-Desktop/actions/runs/34333729520)。

## 安装器验收

NSIS 安装器在普通 Windows 管理员账户完成真实安装/卸载。
目标目录包含中文及空格，3,072 个安装文件逐个 SHA-256 与冻结源相同；
安装与卸载退出码均为 0。卸载移除本软件文件，同时保留测试加入的非安装拥有文件。
安装器 SHA-256 为 `7e21807d044618a6307a6f1030976168d5fc4a83c9cc73dd07e65b35af83935a`。
