# Windows 发布构建

版本：0.1.0-alpha.1。GUI 使用 Python 3.12、PySide6；CUDA 运行包使用独立的 CPython 3.10.21。构建不需要运行模型。

所有下载、依赖环境、构建目录和日志须在当前用户系统 Temp 的本次构建专用目录内；不要在同步盘或源码树写构建中间文件。

## GUI

1. 获取源码及 `third_party/Matcha-TTS` 子模块；安装 64 位 Python 3.12。
2. 在专用 Temp 目录建立 GUI venv，执行 `python -m pip install -r requirements-gui-locked.txt`。此清单记录本次实际使用的全部版本。
3. 确保 `packaging/component-manifest.json` 对应已经校验的运行包；其发布 URL 必须与将上传的附件名称一致。参考音色仅包含清单列出的录音及许可。
4. 解压 NSIS 3.11 至专用 Temp 目录。用 PowerShell 执行：

```powershell
& .\packaging\build_desktop.ps1 -Python "$buildTemp\gui-env\Scripts\python.exe" -Stage "$buildTemp\desktop-build" -MakeNSIS "$buildTemp\nsis\nsis-3.11\makensis.exe"
```

产生 onedir 程序、便携 ZIP 和用户目录安装程序。安装程序不要求管理员权限；卸载仅删除安装清单中的文件，保留另外生成或放入的用户文件。用户设置、组件及项目默认位于用户数据目录。

构建脚本和 spec 将 DLL 搜索 PATH 限定为 GUI Python、PySide6 和 Windows。Qt 使用 Windows 自带的无版本后缀 ICU API；不要打包其他软件的 ICU。已排除误从 Poppler 搜到的 ICU78，并使用所选 PySide6 发行包内同一版本的 MSVC DLL，避免开发机全局环境污染发布程序。

## CUDA 运行包

首个 alpha 使用已验证安装中的发行包文件引导构建：真实 CPython 目录、site-packages，以及包安装到 `Library` 和 `share` 的数据。没有复制 venv 控制文件、Scripts 中的启动器或 Junction。

复建不依赖原作者的 E 盘安装：下载 [Astral python-build-standalone CPython 3.10.21 / 20260901 Windows x86_64 install_only](https://github.com/astral-sh/python-build-standalone/releases/download/20260901/cpython-3.10.21%2B20260901-x86_64-pc-windows-msvc-install_only.tar.gz) 并解压，确认 `python.exe` 和 `Lib` 在同一目录。文件大小 39,291,615 字节，官方资产 SHA256 为 `b3edb1dacad300c7117ce4d6df97cd2e411afc0d173db43c224f904deb6253b5`。发行包及其许可证须保留。需要能构建 pyworld 等源码分发包的 Visual C++ 工具链。

```powershell
& .\packaging\build_runtime_clean.ps1 -Runtime cu121 -PythonDistribution "$buildTemp\cpython310\python" -Stage "$buildTemp\runtime121-build"
& .\packaging\build_runtime_clean.ps1 -Runtime cu128 -PythonDistribution "$buildTemp\cpython310\python" -Stage "$buildTemp\runtime128-build"
```

脚本从 `requirements-runtime-cu121.txt` 或 `requirements-runtime-cu128.txt` 安装固定版本。CUDA 12.1 使用 torch/torchaudio 2.3.1；CUDA 12.8 使用 2.7.1。完整 pip check 和 DLL 导入检查必须通过。Windows 的 Intel MKL/OpenMP/TBB DLL 安装在 Python 根目录的 `Library/bin`，不能仅保存 site-packages，否则 `torch/lib/shm.dll` 将因依赖缺失而无法加载。

`build_components.py --stage ... --package cu121` 对真实文件逐个计算 SHA256、压缩并按 1900 MiB 分片；每片低于 GitHub Release 的 2 GiB 限制。组件清单同时记录压缩片与解压文件哈希。不要手写假哈希，也不要把模型或运行环境加入 Git 历史。

## 模型与媒体组件

模型清单固定官方仓库提交，包含主模型、BlankEN 文件及 wetext 中英文 FST。导入已有模型时逐个校验；允许旧安装的 wetext 位于模型同级目录。

FFmpeg 由应用直接下载固定日期的 BtbN LGPL 共享构建，校验清单 SHA256。项目不重新托管 FFmpeg 二进制。该构建提供 h264_nvenc、h264_mf 和 libmp3lame；启动时仍应实际测试编码器是否可用。

## 发布检查

运行 `pytest tests_desktop`。另在无 Python 的 Windows 电脑安装并测试：安装向导、下载断点续传与取消、已有模型导入、GPU 选择、引擎短句自检、生成和导出。RTX 50 系列需在对应硬件执行 CUDA 12.8 实际推理测试；当前开发机 RTX 2070 Super 不能代替该硬件验证。

发布附件包括安装程序、便携包、两个 CUDA 运行包分片、组件清单和 SHA256、第三方许可，以及 Qt/PySide/libsndfile/soxr/frozendict 对应源码。许可证和源码清单见 `packaging/source-manifest.json`。没有代码签名证书时发行包保持未签名，发布说明应如实注明。
