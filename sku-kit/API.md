# SKU Kit 0.5 接口

## HTTP

运行 `python3 server.py --no-open --port 18766`，仅监听本机。
POST `/api/check`、`/api/pack` 或 `/api/checklist`，Content-Type 为 application/json：

```json
{
  "table": {"name": "products.csv", "content": "BASE64"},
  "images": [{"name": "images/photo.png", "content": "BASE64"}],
  "options": {
    "sku_column": "SKU",
    "match_mode": "exact",
    "case_sensitive": true,
    "min_width": 0,
    "min_height": 0,
    "duplicate_policy": "error",
    "assignments": {}
  },
  "allow_warnings": false
}
```

options.sheet：多工作表必填。match_mode 为 exact / suffix；duplicate_policy 为 error / repeat_rows。
图片路径为相对路径；禁止重复路径、父目录跳转、反斜杠或绝对路径。网页原始资料上限 50 MB。

- check：200 JSON。status=ok / warning / error，200 只表示检查完成。
- pack：200 ZIP，或 422 JSON（阻止交付）。每次重新检查，错误始终阻断，提醒需 allow_warnings=true。
- checklist：200 CSV，允许在有阻断错误时导出问题；输入无法解析仍返回 400。
- 输入/运行失败：400 JSON，`status=invalid_input` 及 error。

### 表格预读与服务状态

GET `/api/health` 返回 `status`、`version`、`storage=memory` 和 `model_api_required=false`，不包含业务文件。

POST `/api/inspect`，Content-Type 为 application/json，只传原表和可选工作表：

```json
{"table":{"name":"products.xlsx","content":"BASE64"},"sheet":"商品资料"}
```

返回 `sheets`、`sheet`、`requires_sheet`、`headers`、`sku_candidates`、`row_count`、最多 3 行 `preview` 和对应 `row_numbers`。多工作表且未选择时只返回工作表列表，`requires_sheet=true`，不猜测其中一张。编号候选不止一列时由调用者明确选择。

### 已有交付包核验

POST `/api/verify`，Content-Type 为 application/zip，Body 直接为 ZIP 原始字节，HTTP 上限 70 MB。返回 JSON：`kind=package_verification`、`status=ok / error / invalid_input`、`summary`、`issues`、`limits` 和 `notice`。HTTP 200 表示核验完成，必须再检查 JSON 的 status；请求格式错误仍返回 400。

核验不写入或解压到磁盘。检查文件集合、CRC、原表与原图 SHA-256、清单和报告的一致性。清单没有数字签名，不能证明发送者身份或图片中的商品是否正确。支持 schema 1.1 的 0.3/0.4 交付包。

## 标准结果

schema_version=1.1，engine_version=0.5.0。包与应用版本统一由 `_version.py` 定义。
包含 summary（products、images、matched_images、errors、warnings）、rules、table_sha256、context_sha256、products、images、issues、assignments、limits。
products 每项包括 row（原表行号）、sku、name。
images 每项包括 source、sha256、size、sku、candidates、valid、width、height、assignment_source（filename / manual）。
issues 每项为 code、severity、subject、message，可选 row。valid 只表示文件能验证解码，不代表图片商品身份正确。
CLI/MCP 导出另含 export.written；成功带 path、bytes；阻止带 reason。

## 人工确认

先 check，再根据用户明确选择，把结果中的哈希复制到 assignments：

```json
{
  "images/photo.png": {
    "sku": "001",
    "sha256": "IMAGE_SHA256_FROM_CHECK",
    "table_sha256": "TABLE_SHA256_FROM_CHECK",
    "context_sha256": "CONTEXT_SHA256_FROM_CHECK"
  }
}
```

sku=null 表示明确暂不归类，保留未匹配提醒；删除该项则恢复按文件名匹配。
不存在的文件/编号、哈希或编号列/工作表上下文不一致会拒绝；人工确认不绕过坏图、缺图、重复冲突等错误。
报告和交付包清单保留人工确认。不要让模型猜测归属或虚构哈希。

## CLI / MCP

在 Python 3.11+ 虚拟环境从仓库根目录执行 `python -m pip install .` 后，有三个入口：`sku-kit` 启动本机网页；`sku-kit-cli` 运行 JSON 命令；`sku-kit-mcp --root /data` 运行 stdio MCP。`python -m sku_kit` 也可启动网页。项目尚未发布到 PyPI。源码目录里的 `python3 cli.py ...`、`python3 server.py` 和 `python3 mcp_server.py ...` 保留兼容。

CLI 的 --assignments-file 指向上述 JSON，--duplicate-policy 控制重复记录。checklist 子命令可导出 CSV。
`python3 cli.py verify --package /data/delivery.zip` 可独立核验已有交付包，不需要原始文件；CLI 压缩及展开大小上限各 320 MB、最多 5,006 个文件。退出码 0=核验通过，2=核验未通过，1=输入/读取失败。stdout 使用 UTF-8 JSON。
MCP stdio：先 initialize，再 notifications/initialized，然后 tools/list / tools/call。
工具 sku_check、sku_pack、sku_checklist；table、images 及 output / assignments_file 路径限于配置 root。
也可直接传 assignments 对象。assignments 与 assignments_file 不能同时指定。
人工记录文件最多 2 MB；输出不覆盖，不能写入图片源目录。
协议参考：https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2025-11-25/basic/lifecycle.mdx

## 进度文件

前端保存格式为：

```json
{
  "format": "sku-kit-project",
  "version": 1,
  "saved_at": "ISO_DATE",
  "payload": {"table": {}, "images": [], "options": {}, "allow_warnings": false}
}
```

payload 与 HTTP 输入结构相同，包含 Base64 原件。导入先检查格式/路径/容量，再通过同一引擎重检；成功才替换当前批次。恢复时提醒确认状态重置。
此文件包含业务资料，并非只保存偏好；应用不会静默写入浏览器持久存储。
