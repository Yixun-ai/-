# SKU Kit 0.3 接口

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

## 标准结果

schema_version=1.1，engine_version=0.3.0。
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

CLI 的 --assignments-file 指向上述 JSON，--duplicate-policy 控制重复记录。checklist 子命令可导出 CSV。
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
