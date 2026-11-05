# SKU Kit · 商品资料整理

本机整理产品表和图片：检查问题、看图确认归属、补选图片、下载补件清单，最后打包交付。

## 启动

Python 3.9+，免费依赖 Pillow。从仓库根目录先运行 `cd sku-kit`，确认当前目录包含 `server.py`，再在虚拟环境安装：

```sh
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
python3 server.py
```

Windows 激活命令为 `.venv\Scripts\activate`。浏览器自动打开，关闭终端或 Ctrl+C 停止。
需要固定地址时：`python3 server.py --port 18766`；默认自动选择空闲端口。
macOS / Windows 启动脚本也可使用，需先安装好 Python 和依赖。macOS 终端可运行 `zsh 启动.command`，无需依赖下载后的可执行权限；Windows 可双击 `启动.bat`。

## 日常操作

1. 选择产品表和图片文件夹。CSV / XLSX 首行是表头；页面先读取工作表和列名，多张工作表需要直接选择。唯一的 SKU、货号、商品编号或产品编号列会被选中，有多个候选时需手动选择；可展开查看前 3 条记录。
2. 确认文件名规则。默认要求文件名完整等于编号；`SKU_主图` 类命名要明确选后缀模式。多编号命中不猜测。
3. 查看“问题与提醒”；“看图核对”显示缩略图、文件、产品编号和名称，可搜索、筛选、分页。
4. 点击图片或“调整归属”，选择表中已有产品。可暂不归类，或恢复按文件名匹配。
5. 缺图可以补选；损坏图片可补选同名文件替换。同名文件唯一时只替换本批次中的副本，原文件不修改；多个目录存在同名文件时只追加，避免错替换。
6. 有错误先修正；仅剩提醒时，查看并明确确认后可打包。无法读取的图片不能靠改归属绕过。
7. 可随时在有检查结果时下载补件清单；即使交付被阻止，也可把问题发给供应商。
8. 开始下一批时，点击“新的一批”并确认；当前选择和确认记录清空，原件不变。未保存时离开页面会触发浏览器提示。

## 核验收到的资料包

展开页面下方“核验已有的交付包”，选择 SKU Kit 导出的 ZIP。网页支持最多 70 MB；更大包可运行：

```sh
python3 cli.py verify --package /data/delivery.zip
```

核验原表、原图的 SHA-256 以及文件清单、CRC、人工确认记录的一致性，不需要重新提供原件，也不解压到磁盘。包内记录未签名，此功能不证明发送者身份或商品内容正确。CLI 包大小及展开总量上限各为 320 MB。

普通用户可使用不需要安装 Python 的[桌面构建版](../docs/desktop.md)。构建和测试状态请以对应 GitHub Actions 运行结果为准。

## 保存和继续

“保存进度”下载 `.sku-kit.json` 文件，包含原表、原图、规则和人工归属。该文件可能含业务资料，请妥善保存。
打开进度会重新检查所有资料和人工确认，不需要重新选择原始文件；恢复失败不替换当前批次。提醒确认状态不沿用。

人工确认绑定原图、原表以及工作表/编号列的哈希；相关内容变化后必须重新确认。替换图片会清除该图片旧归属。

重复编号默认视为错误。在高级选项里可明确选择“同一商品的多条记录”，此时保留原表行并共用图片文件夹，仍会提醒；名称冲突的重复编号始终阻止交付。

## AI 调用

页面、CLI、HTTP、MCP 共用 `engine.py`。不调用模型 API，也不上传云端。
纯聊天模型无法直接访问本机；可执行命令、调用本机 HTTP 或连接本地 MCP 的宿主可接入。没有逐家客户端实测或全协议兼容承诺。

```sh
python3 cli.py check --table /data/products.csv --images /data/images --sku-column SKU
python3 cli.py checklist --table /data/products.csv --images /data/images --output /data/补件.csv
python3 cli.py pack --table /data/products.csv --images /data/images --output /data/delivery.zip
```

stdout 为 JSON；退出码 0 表示检查通过或文件成功写出，2 表示有问题/打包阻止，1 为输入或运行失败。
`--assignments-file /data/assignments.json` 可传人工确认，格式见 [API.md](API.md)。
`--duplicate-policy repeat_rows` 明确允许同商品多行；`--allow-warnings` 仅适用于打包的提醒，不绕过错误。
输出必须位于图片输入目录外，已有文件不覆盖。

### MCP

按宿主方式配置，路径换成自己的绝对路径：

```json
{
  "mcpServers": {
    "sku-kit": {
      "command": "/path/to/.venv/bin/python",
      "args": ["/path/to/sku-kit/mcp_server.py", "--root", "/path/to/product-data"]
    }
  }
}
```

工具：`sku_check`、`sku_pack`、`sku_checklist`。只暴露最小的商品资料目录，不要把整个主目录当 root。
适配 2024-11-05、2025-03-26、2025-06-18、2025-11-25 握手版本；未做完整协议认证。
[Skill 入口](skills/sku-kit/SKILL.md) 可供支持该格式的客户端阅读或按其规则安装。

## 输出与边界

- `products/<SKU>/`：分好的原始图片。
- `unmatched/`：经明确允许提醒后单独存放的未匹配图片。
- `source/`：原始产品表，保留原始字节。
- `reports/report.json`、`manifest.json`：规则、路径、SHA-256、人工归属记录。
- `reports/补件清单.csv` 和 `issues.csv`：问题记录；潜在公式字段已转义。

XLSX 编号必须为文本；不恢复 Excel 已丢失的前导零/数字精度，不执行公式。多工作表需明确选择。
支持 CSV（UTF-8 / GB18030）、标准 XLSX；不支持 XLS、加密表、嵌入图片或外链下载。
CLI 最多 20,000 条记录、256 列、5,000 个文件，总图片 240 MB；单文件 30 MB、单图 2500 万像素，动画最多完整检查 100 帧。网页及进度文件中的原始资料总量最多 50 MB。
图片解码依赖 Pillow 构建，缩略图依赖浏览器；不能预览不等于文件损坏。工具不判断图片拍的是否是对应商品，也不代表电商平台审核通过。
这是本机工具，没有收费、账户、云存储或自动更新。当前发布包不包含示例业务资料或测试输出。源码采用 [MIT 许可证](../LICENSE)，依赖软件遵循各自许可证。

## 验证

```sh
python3 -m unittest discover -s tests -p 'test_*.py'
node --test tests/project.test.mjs
```

测试范围与浏览器实际流程见 [验证记录.md](验证记录.md)。
