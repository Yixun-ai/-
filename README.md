# SKU Kit · 商品资料整理

把产品表和图片对上，找出缺漏，再打包交付。

适合整理供应商交来的商品资料。选择 CSV / XLSX 和图片文件夹，检查编号、缺图、重复和损坏文件；有歧义时看图确认归属，最后导出按产品编号分好的原图、原表和检查记录。

所有处理在本机完成，不需要模型 API Key，也不依赖付费 AI 服务。

## 能做什么

- 按文件名匹配产品编号，支持明确开启主图、细节图后缀规则。
- 检查空编号、重复编号、缺图、损坏图片和多义匹配。
- 看缩略图调整图片归属，补选或替换图片。
- 下载补件清单，保存进度后继续处理。
- 将原始资料和检查记录打包；有阻断错误时不会生成交付包。
- 通过网页、CLI、HTTP 或本地 MCP 调用同一个检查引擎。

## 快速启动

需要 Python 3.9 或更新版本。程序源码位于仓库的 `sku-kit/` 子目录。

### macOS / Linux

```sh
git clone https://github.com/Yixun-ai/-.git sku-kit-project
cd sku-kit-project/sku-kit
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
python3 server.py
```

### Windows（命令提示符 CMD）

```bat
git clone https://github.com/Yixun-ai/-.git sku-kit-project
cd sku-kit-project\sku-kit
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe server.py
```

没有 Git 也可在仓库页面选择 **Code → Download ZIP**，解压后在包含 `server.py` 的 `sku-kit` 文件夹打开终端，从创建虚拟环境那一步开始。

启动后自动打开浏览器；如果没有打开，访问终端显示的本机地址。按 Ctrl+C 停止。默认自动选择空闲端口；需要固定地址时加 `--port 18766`。

首次装好依赖后，可在 macOS 终端运行 `zsh 启动.command`，Windows 可双击 `启动.bat`。macOS 从网页下载的脚本可能没有执行权限，终端启动命令不依赖该权限。

## 第一次使用

1. 选择产品表和图片文件夹，表格首行应为列名，产品编号建议保存为文本。
2. 确认编号列和文件名规则。例如产品 `001` 对应 `001.png`。
3. 检查问题，看图确认有歧义的归属，补齐缺图。
4. 修正错误；仅有提醒时，核对并确认后下载资料包。

原始文件不会被修改。保存的进度文件包含原表和原图，请保存在自己的业务资料目录；仓库忽略规则不能代替上传前的人工核对。

## 使用边界

网页一次最多处理 50 MB 原始资料；CLI 的图片总量上限为 240 MB。支持 CSV、标准 XLSX；不支持 XLS、加密表或 Excel 内嵌图片提取。

工具检查文件与编号的对应关系，不判断图片拍的是否是正确商品，也不替代电商平台审核。可执行命令、访问本机 HTTP 或连接 MCP 的 AI 宿主可接入；普通聊天页面无法直接读取本机文件，尚未逐一实测所有客户端。

## 文档与验证

- [完整使用说明](sku-kit/README.md)
- [CLI / HTTP / MCP 接口](sku-kit/API.md)
- [Skill 入口](sku-kit/skills/sku-kit/SKILL.md)
- [已完成的验证与范围](sku-kit/验证记录.md)
- [反馈问题](https://github.com/Yixun-ai/-/issues)

在 `sku-kit/` 目录运行 Python 测试；JavaScript 测试另需 Node.js：

```sh
python3 -m unittest discover -s tests -p 'test_*.py'
node --test tests/project.test.mjs
```

测试会生成临时资料，不需要上传真实商品数据。请在反馈中提供脱敏后的文件结构、报错和复现步骤，不要提交客户原图、业务表或密钥。

## 许可

[MIT License](LICENSE)，Copyright (c) 2026 Yixun-ai。允许使用、修改、分发和商用，分发时需保留版权及许可声明。依赖软件遵循其各自许可证。
