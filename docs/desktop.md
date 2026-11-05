# 桌面下载版

桌面入口运行原有本机服务和检查引擎。下载用户不需要安装 Python；表格、图片依然在本机内存中处理。界面通过默认浏览器打开，终端窗口保留启动地址和错误信息。

## 下载与启动

优先打开仓库的 [Releases 下载页](https://github.com/Yixun-ai/-/releases)，选择最新的 `SKU Kit 桌面预览版`。预览版不是正式稳定版，不会被设为 GitHub 的正式 `Latest` 版本。

- Windows 64 位 Intel/AMD：下载 `SKU-Kit-windows-amd64.zip`。
- macOS Apple Silicon（M 系列）：下载 `SKU-Kit-darwin-arm64.zip`。此包不支持 Intel Mac。
- 同时提供各 ZIP 的 `.sha256` 校验和。

完整解压 ZIP 后再启动。必须保留 `SKU-Kit` 文件夹中的全部文件，尤其是 `_internal`。

若 Releases 暂无下载包，可在 GitHub Actions 的 `Desktop build` 成功运行记录中下载对应平台的 Artifact；这通常需要登录 GitHub。先解压外层 Artifact，再完整解压里面的程序 ZIP。只有两个平台都完成构建及最终 ZIP 冒烟测试后，主分支推送对应的预览版才会自动发布到 Releases。

- Windows x64：双击 `Start SKU Kit.bat`。
- macOS Apple Silicon：双击 `Start SKU Kit.command`。
- 浏览器未自动打开时，复制终端显示的 `http://127.0.0.1:端口` 地址。
- 使用期间保留终端；按 `Ctrl+C` 停止。关闭网页本身不会停止服务。

这些构建目前没有商业代码签名或 macOS 公证。系统可能拦截首次启动；只在确认下载来源和 SHA-256 校验和后，按照系统自身提供的打开方式处理。不要关闭整机安全设置。macOS 的普通构建只支持相应架构，不是通用二进制。

## 自己构建

构建工具与桌面运行环境分开。下面在仓库根目录执行；Windows 将 `.venv-build/bin/python` 换为 `.venv-build\Scripts\python.exe`：

```sh
python3 -m venv .venv-build
.venv-build/bin/python -m pip install "Pillow==11.3.0" "pyinstaller==6.22.3" "pyinstaller-hooks-contrib==2026.9"
.venv-build/bin/python sku-kit/build_desktop.py
.venv-build/bin/python scripts/smoke_desktop.py --archive build-desktop/SKU-Kit-darwin-arm64.zip
```

Windows 最后一条命令的 ZIP 文件名为 `build-desktop/SKU-Kit-windows-amd64.zip`；Intel Mac 为 `SKU-Kit-darwin-x86_64.zip`。构建脚本接受 `--output 路径`，内部使用绝对资源路径；从其他工作目录调用也可构建。

打包模式为 `onedir`，保留终端和依赖目录，便于排查问题。生成目录为 `build-desktop/dist/SKU-Kit/`，可分发 ZIP 和 `.sha256` 文件位于 `build-desktop/`。构建记录 `build-info.json` 保存 Python、Pillow、PyInstaller 与平台版本。

Windows 软件必须在 Windows 上打包，macOS 软件必须在 macOS 上打包。[PyInstaller 官方说明](https://pypi.org/project/pyinstaller/6.22.3/)解释了这一限制。当前版本固定 PyInstaller 6.22.3，工作流中的 GitHub Actions 固定完整提交 SHA。

## 自动验证与发布边界

工作流在 Windows 2022 x64、macOS 15 ARM64 原生 runner 上分别构建。主分支的相关源码推送、标签推送、相关拉取请求或手动触发会运行。构建任务只有仓库读取权限；独立发布任务仅在 `main` 推送且两个平台的构建、测试和最终 ZIP 冒烟检查全部成功后运行，发布任务具有写入 Release 所需的权限。

发布任务只下载本次运行的两个平台产物，重新检查 ZIP 的 SHA-256，然后创建 `preview-提交短哈希` 预览版。下载包上传完成前保持草稿状态；不会覆盖旧版本，也不设为正式 Latest。重复运行同一提交若预览版已经存在，创建步骤会失败并保留原版本；手动构建、标签构建和拉取请求都不会自动发布。

工作流不包含付款、模型 API 或签名服务。公共仓库的标准 GitHub runner 可免费使用；私有仓库使用者应先检查自己的 Actions 免费额度，再启用自动构建。[GitHub 官方 runner 说明](https://docs.github.com/en/actions/how-tos/write-workflows/choose-where-workflows-run/choose-the-runner-for-a-job)列出了免费公共 runner 与私有仓库额度的区别。

打包后冒烟检查先把分发 ZIP 解压到临时目录，再从另一个独立工作目录启动真正的可执行文件，使用自动空闲端口，并验证：

1. 页面、样式和 JS 资源可访问。
2. 两个真实 PNG 与 CSV 可以检查和匹配。
3. 导出的 ZIP 可完整读取，原始图片和表格字节保持一致。
4. 缺图会阻止导出；允许警告也不能绕过错误。
5. 外部网页来源请求会被拒绝。

自动构建与上述验证通过，说明该构建在对应 runner 上完成了基本流程；不能据此声称已通过真人 Windows 或所有系统兼容测试。首次正式发布仍应在目标用户的真实机器上检查解压、双击、浏览器打开与退出。
