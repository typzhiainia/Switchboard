# Switchboard — Windows 本地大模型 API 集中管理网关

> 根据[《生成式人工智能服务管理暂行办法》](http://www.cac.gov.cn/2023-07/13/c_1690898327029107.htm)的要求，请勿对中国地区公众提供一切未经备案的生成式人工智能服务。

面向 Windows 的本地网关软件：把多个上游大模型 API（OpenAI、DeepSeek、Moonshot、智谱、阿里云百炼等任意 OpenAI 兼容服务）统一到一个本机出口 `http://127.0.0.1:8688/v1`，提供图形化管理界面，集中管理 API 密钥、切换上游、监控请求状态。**所有数据保存在本机，默认仅监听 127.0.0.1。**

## 功能特性

- **统一出口**：完全兼容 OpenAI SDK（`chat/completions`、`completions`、`embeddings`、`images`、`responses`、`models`），客户端只需改 `base_url`。
- **多上游管理**：任意添加 OpenAI 兼容上游，配置模型列表、优先级、超时、自定义请求头；支持 `*` 通配模型。
- **智能路由与故障转移**：按模型路由到支持的上游，按优先级分组选择；失败自动切换到下一上游（可配置重试次数）。
- **加权负载均衡**：同优先级上游按权重平滑轮询（权重 2:1 时流量序列 A B A A B A），均匀分散请求。
- **熔断器**：上游连续失败 3 次自动熔断 60 秒，冷却期跳过该上游、半开自动恢复，支持手动重置，界面实时展示熔断状态。
- **API 密钥集中管理**：本地签发 `sk-` 密钥供客户端使用，支持停用、按分钟限流；上游真实密钥只保存在服务端，不出现在客户端。
- **流式输出透传**：SSE 流式响应原样透传，兼容流式调用与工具调用。
- **请求监控**：请求日志（状态码、延迟、Token 用量、错误信息）、24h 趋势图、上游/模型分布统计。
- **图形化界面**：启动自动打开浏览器进入控制台，无需命令行；控制台内置**模型测试**页，选择上游与模型即可对话测试（支持流式输出、Token/延迟统计），不消耗本地密钥配额。
- **健康检查**：一键检测上游连通性并自动拉取模型列表。
- **在线更新**：以 CNB Release 为更新源，用户在控制台点一下「检查更新 → 立即更新」即可自动下载、校验、替换文件并重启完成升级。
- **Windows 安装包**：Inno Setup 一键安装、桌面快捷方式、开机即用。

## 快速开始（源码运行）

要求：Windows 10/11，Python 3.10+

```bat
install.bat    :: 安装依赖（只需一次）
run.bat        :: 启动网关，自动打开 http://127.0.0.1:8688/
```

首次启动时控制台会打印 **管理令牌（Admin Token）**，在浏览器登录页输入即可进入控制台。

## 制作 Windows 安装包（免 Python 环境）

```bat
build.bat
```

生成 `dist\Switchboard\Switchboard.exe`（已内置 Python 运行时）。再用 [Inno Setup 6](https://jrsoftware.org/isdl.php) 打开 `installer.iss` 编译，得到 `installer-output\Switchboard-Setup-1.0.0.exe`，双击安装即用，无需安装 Python。

## 在线更新（CNB Release 为更新源）

本地网关以本仓库在 CNB 发布的 Release 为更新源。发布新版本后，老用户在控制台 **设置 → 软件更新** 点「检查更新」→「立即更新」，即可自动下载更新包、SHA256 校验、替换文件并重启服务完成升级，无需重装。

**用户侧（一次性配置）**：

1. 设置 → 软件更新，填写「更新仓库」（格式 `组织/仓库`，如 `my-org/switchboard`）；
2. 填写「访问令牌」：在 cnb.cool 个人设置中创建（Open API 调用必需），令牌只保存在本机数据库；
3. 点「检查更新」，有新版本时点「立即更新」，等待服务自动重启即可。

**发布侧（每次发版）**：

1. 修改 `app/__init__.py` 中的 `__version__`；
2. 运行 `build.bat`（或单独运行 `python make_update.py`），在 `dist\update\` 下生成：
   - `switchboard-<版本>-src.zip`（源码运行用户的更新包，含全部代码文件）
   - `switchboard-<版本>-win.zip`（Windows 打包版更新包，含 `dist\Switchboard` 全部内容）
   
   控制台会打印每个包的 SHA256；
3. 在 CNB 仓库创建 Release：tag 填 `v<版本>`（与 `__version__` 对应，如 `v1.1.0`），附件上传两个 zip（命名保持 `-src.zip` / `-win.zip` 后缀，程序按安装方式自动选择）。也可用 Open API 发布：

```bat
:: 创建 Release（TOKEN 为 cnb.cool 访问令牌）
curl -X POST -H "Authorization: Bearer %TOKEN%" -H "Content-Type: application/json" ^
  -d "{\"tag_name\":\"v1.1.0\",\"name\":\"1.1.0\",\"body\":\"更新说明\"}" ^
  "https://api.cnb.cool/my-org/switchboard/-/releases"
:: 附件上传：先申请上传地址，再 PUT 文件，最后确认（见 CNB OpenAPI 文档 /-/releases/{id}/asset-upload-url）
```

**注意事项**：

- 网关按自身安装方式选择附件：PyInstaller 打包运行选 `-win.zip`，源码运行选 `-src.zip`；版本号取 tag 去掉 `v` 后与本地 `__version__` 比较；
- 守护模式（`start-protected.bat`）会先于更新助手把服务拉起，导致文件被占用，更新前请先关闭守护窗口；
- 若安装在 `C:\Program Files` 等需要管理员权限的目录，自动替换会失败并提示，请右键「以管理员身份运行」Switchboard 后再更新，或改用安装包升级；
- 更新只替换程序文件，数据库（`%LOCALAPPDATA%\Switchboard\gateway.db`）与所有配置不受影响。

## 使用流程

1. **添加上游**：控制台 → 上游服务 → 添加上游，填入名称、Base URL、上游 API Key、支持模型（逗号分隔，`*` 表示全部）→ 保存并启用。
2. **健康检查**：点击上游「检测」，正常后显示延迟与状态。
3. **签发密钥**：控制台 → API 密钥 → 新建密钥，复制 `sk-...`。
4. **客户端接入**（以 Python OpenAI SDK 为例）：

```python
from openai import OpenAI
client = OpenAI(base_url="http://127.0.0.1:8688/v1", api_key="sk-你的本机密钥")
print(client.chat.completions.create(model="deepseek-chat",
      messages=[{"role": "user", "content": "你好"}]).choices[0].message.content)
```

5. **监控**：仪表盘查看成功率/延迟/Token 统计；请求日志可过滤、分页、清空。

## 路由与故障转移规则

- 请求体中的 `model` 决定路由：网关在所有「启用」的上游中查找 `models` 包含该模型（或 `*`）的上游。
- 上游按「优先级（小=优先）」分组，优先使用最小组；同组内按「权重」做平滑加权轮询。
- 熔断中的上游不参与选路；整组全部熔断时该组仍作为兜底（半开试探）。
- 单次请求失败（网络错误/5xx）时，自动按序尝试后续上游，尝试次数 = `故障转移次数 + 1`。

## 数据与安全

- 数据库：`%LOCALAPPDATA%\Switchboard\gateway.db`（上游密钥、本机密钥、日志、设置），可用环境变量 `SWITCHBOARD_HOME` 覆盖。
- 管理令牌保存在数据库 settings 中，仅用于登录控制台；客户端必须使用本机签发的 `sk-` 密钥。
- 默认只监听 `127.0.0.1`；如需局域网内其他设备访问，可在设置中改为 `0.0.0.0` 并自行配置防火墙。
- 日志默认保留 30 天，可自动清理，也可手动清空。

## 运行与停止

- **启动**：双击 `Switchboard.exe`（或桌面快捷方式），出现黑色控制台窗口并自动打开管理页面。
- **手动停止**（三种方式任选）：
  1. 控制台 → 设置 → 「停止网关服务」按钮（优雅停止，推荐）；
  2. 在黑色控制台窗口按 `Ctrl+C`；
  3. 直接关闭黑色控制台窗口。
- **防异常退出**：服务本身由 uvicorn 托管、请求级异常已兜底不会崩溃；如需更强保障，使用 `start-protected.bat`（守护模式：进程异常退出 3 秒后自动拉起；注意此模式下界面「停止服务」会被守护脚本重新拉起，需直接关闭窗口停止）。
- **开机自启**：安装时勾选「开机自动启动」，或手动把快捷方式放入 `Win+R` → `shell:startup` 打开的启动文件夹。

## 命令行参数

```bat
Switchboard.exe --host 127.0.0.1 --port 8688   :: 指定监听地址/端口
Switchboard.exe --no-browser                    :: 启动时不自动打开浏览器
```

## 项目结构

```
app/
  main.py      FastAPI 主程序：管理 API + /v1/* 代理出口 + 静态界面
  proxy.py     转发引擎：健康检查、非流式/流式(SSE)转发、usage 提取
  updater.py   在线更新：CNB Release 检查/下载/校验/应用并重启
  db.py        SQLite 数据层：providers / api_keys / logs / settings
  schemas.py   Pydantic 模型
  static/      管理控制台（原生 HTML/CSS/JS，无构建步骤）
launcher.py    PyInstaller 打包入口
gateway.spec   PyInstaller 配置
build.bat      打包脚本（含更新包生成）
make_update.py 生成在线更新 zip（-src.zip / -win.zip）及 SHA256
installer.iss  Inno Setup 安装包脚本
run.bat        开发/源码方式启动
install.bat    安装依赖
```
