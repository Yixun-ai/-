# 参与贡献

欢迎提交复现步骤、文档改进和代码。商品资料检查的结果应当可解释、可复现；对无法确认的归属应交给用户，不能靠猜测消除错误。交流请遵守[行为准则](CODE_OF_CONDUCT.md)。

## 先选对入口

- 安装、使用问题：先看[两分钟上手](docs/quickstart.md)和[常见问题](docs/troubleshooting.md)，再按[支持说明](SUPPORT.md)反馈。
- 普通缺陷或功能建议：使用 [Issue 表单](https://github.com/Yixun-ai/-/issues/new/choose)，先搜索是否已有讨论。
- 安全漏洞：按 [SECURITY.md](SECURITY.md)报告，不要公开利用步骤或客户资料。
- 较大的流程或格式改动：先开 Issue，描述用户正在完成什么任务，再实施。修正错字和小范围文档问题可直接提交 PR。

## 本地开发

需要 Python 3.11+。Python 运行依赖是 Pillow 13；运行前端测试另需支持 `node --test` 的 Node.js。以下在仓库根目录执行：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/sku-kit
```

Windows 使用 `py -3 -m venv .venv`，随后把 `.venv/bin/python` 换成 `.venv\Scripts\python.exe`，启动命令换成 `.venv\Scripts\sku-kit.exe`。创建环境的 Python 必须为 3.11 或更新版本。

源码在 `sku-kit/`；网页资源在 `sku-kit/static/`。桌面构建说明见 [docs/desktop.md](docs/desktop.md)。不要把本地业务目录加入仓库，也不要把真实客户资料加入测试。

## 验证改动

先从仓库根目录进入 `sku-kit/`，再执行 Python 测试：

```sh
cd sku-kit
../.venv/bin/python -m unittest discover -s tests -p 'test_*.py'
```

前端数据逻辑测试：

```sh
node --test tests/project.test.mjs
```

Windows 的测试 Python 路径为 `..\.venv\Scripts\python.exe`。根据改动补充验证：

- 修改匹配、打包或核验规则时，使用临时合成文件复现原问题，并增加能够区分正确与错误行为的回归测试。
- 修改网页流程时，在浏览器中走一遍相关操作；确认原件不变、错误无法绕过、失败不会替换当前批次。
- 修改安装或桌面构建时，检查实际安装后的入口或最终 ZIP，而不只是在源码目录运行。
- 仅改文档时，检查命令与相对链接是否正确，不必为文字改动编写程序测试。

测试通过的数量随版本变化。历史[验证记录](sku-kit/验证记录.md)描述当时的环境与范围，不代表本次提交已经通过，也不代表所有平台都已真人实测。

## 提交 PR

让一次 PR 解决一个清楚的问题，说明触发条件、修改后的行为和已做验证。截图或附件只使用合成、脱敏内容。未运行的系统或测试请直接注明，不要把计划写成已通过结果。

兼容性、数据格式或依赖要求变化时同步更新使用说明和 [CHANGELOG.md](CHANGELOG.md)。项目采用 [MIT](LICENSE)；请确认自己有权贡献代码及素材，不要提交来源不明的代码、图片或客户数据。

维护者按实际时间审阅，不承诺固定响应时间。重复、偏离项目用途或无法安全复现的请求可能先被要求补充信息或关闭。
