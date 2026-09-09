# 显卡兼容与验收

本页区分“选择了正确运行包”和“已完成实际模型验收”。显卡名称本身不能证明可运行。

界面基于 Qt 6.11，系统目标为 Windows 10 1809 及以上、Windows 11 的 x64 版本；
基础系统范围来自 [Qt 官方支持平台](https://doc.qt.io/qt-6/supported-platforms.html)。

| 系列 / 型号 | 显存条件 | 运行包 | 当前证据 |
|---|---|---|---|
| RTX 2070 Super | 8 GB | Python 3.10 / torch、torchaudio 2.3.1 / CUDA 12.1 | 现有引擎已实测；桌面版验收记录持续补充 |
| 其他 RTX 20 | 8 GB 及以上 | cu121 | 目标兼容，待实机验证 |
| RTX 30 | 8 GB 及以上 | cu121 | 目标兼容，待实机验证 |
| RTX 4070 Ti Super | 16 GB | cu121 | 目标兼容，待实机验证 |
| 其他 RTX 40 | 8 GB 及以上 | cu121 | 目标兼容，待实机验证 |
| RTX 50 | 8 GB 及以上 | Python 3.10 / torch、torchaudio 2.7.1 / CUDA 12.8 | 目标兼容，待实际 Blackwell 模型合成验证 |
| 笔记本 RTX 20/30/40/50 | 8 GB 及以上，架构和驱动符合 | 按架构选择 | 待代表性笔记本实机验证 |
| 4/6 GB、GTX、AMD、Intel、CPU | — | — | 不在首版范围 |

运行包按显卡计算能力选择：7.5、8.6、8.9 使用 cu121；12.0 使用 cu128。
Windows 驱动最低检测线分别为 527.41 和 570.65；检测通过后仍须执行真实 CUDA 运算、
短句模型合成及视频编码自检。首次启动以及驱动、显卡或运行包改变后重新自检。

8 GB 档位允许驱动预留造成的小幅容量差异，不会把 6 GB 设备认作受支持。
默认选择可用显存最多的合格设备，也可在设置中明确指定；显存不足保留任务供重试。

## 证据

- [文档与视频验收](media-validation.md)：20 页 PowerPoint、NVENC/Media Foundation、音视频时间对齐。
- 自动化硬件场景：`tests_desktop/test_gpu.py`，只证明分类和选择逻辑。
- 正式版发布前仍需：4070 Ti Super、至少一款 RTX 30、一款 RTX 50、一台 8 GB 笔记本完成
  安装、长文本、多页、取消重试与编码验收。RTX 50 必须实际合成，不能用导入 torch 替代。

参考：[NVIDIA 计算能力表](https://developer.nvidia.com/cuda/gpus)、
[PyTorch 固定版本](https://pytorch.org/get-started/previous-versions/)、
[CUDA 12.8 发行说明](https://docs.nvidia.com/cuda/archive/12.8.0/cuda-toolkit-release-notes/index.html)。
