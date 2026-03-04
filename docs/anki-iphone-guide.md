# iPhone 查词自动生成 Anki 卡片 — 完整实现指南

## 架构概览

```
iPhone 阅读中选词
    → 快捷指令（可附带原文语境句）
    → POST /add-word  →  ldoce-api（Debian VM）
        ├── 查询 LDOCE5 MDX 字典
        ├── 提取音频（LDOCE5 MDD）
        └── 调用 AnkiConnect → 创建 ODH 卡片
```

**ldoce-api** 是本项目的核心服务，部署在与 Anki Desktop, Anki-Sync-Server 同一台 PVE Debian VM 上，对外暴露两个接口：

| 接口 | 说明 |
|------|------|
| `GET /health` | 检查 MDX/MDD 加载状态和 AnkiConnect 连通性 |
| `POST /add-word` | 查词并创建 Anki 卡片 |

---

## 第一步：Anki + AnkiConnect 配置

### 1. 安装 AnkiConnect 插件

打开 Anki → 工具 → 插件 → 获取插件 → 输入代码 `2055492159`，重启 Anki。

### 2. 配置 AnkiConnect 允许局域网访问

工具 → 插件 → 选中 AnkiConnect → 配置：

```json
{
  "apiKey": null,
  "apiLogPath": null,
  "ignoreOriginList": [],
  "webBindAddress": "0.0.0.0",
  "webBindPort": 8765,
  "webCorsOriginList": ["*"]
}
```

> `webBindAddress` 必须改为 `0.0.0.0`，否则只监听 localhost。

### 3. 防火墙放行 8765 端口（Linux）

```bash
sudo ufw allow 8765/tcp
```

### 4. 验证 AnkiConnect

```bash
curl -s http://localhost:8765 -X POST -d '{"action":"version","version":6}'
# 期望：{"result":6,"error":null}
```

---

## 第二步：部署 ldoce-api 服务

### 1. 准备字典文件

将以下四个文件上传到 VM（例如 `/root/anki/dict/`）：

| 文件 | 说明 |
|------|------|
| `Longman Dictionary Of Contemporary English 5th.mdx` | 字典主体（必须） |
| `Longman Dictionary Of Contemporary English 5th.mdx.db` | MDX 索引（由 GoldenDict 生成，必须） |
| `Longman Dictionary Of Contemporary English 5th.mdd` | 音频资源（可选） |
| `Longman Dictionary Of Contemporary English 5th.mdd.db` | MDD 索引（有 mdd 时必须） |

> `.mdx.db` 和 `.mdd.db` 由 GoldenDict 生成，服务直接复用，无需重建。

### 2. 安装项目及依赖

```bash
mkdir -p /opt/iphone-anki-sync
scp main.py requirements.txt root@<vm-ip>:/opt/iphone-anki-sync/

cd /opt/iphone-anki-sync
python3 -m venv .venv
sudo apt install build-essential liblzo2-dev   # python-lzo 编译依赖
.venv/bin/pip install -r requirements.txt
```

### 3. 配置环境变量

```bash
cat > /opt/iphone-anki-sync/.env << 'EOF'
LDOCE5_MDX_PATH=/root/anki/dict/Longman Dictionary Of Contemporary English 5th.mdx
LDOCE5_MDD_PATH=/root/anki/dict/Longman Dictionary Of Contemporary English 5th.mdd
ANKI_CONNECT_URL=http://localhost:8765
DECK_NAME=ODH
NOTE_TYPE_NAME=ODH
API_HOST=0.0.0.0
API_PORT=5050
EOF
```

### 4. 配置 systemd 开机自启

```bash
cat > /etc/systemd/system/ldoce-api.service << 'EOF'
[Unit]
Description=LDOCE5 Dictionary API
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/iphone-anki-sync
ExecStart=/opt/iphone-anki-sync/.venv/bin/python main.py
EnvironmentFile=/opt/iphone-anki-sync/.env
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable ldoce-api
systemctl start ldoce-api
```

### 5. 验证服务

```bash
curl http://localhost:5050/health
# 期望：{"status":"ok","mdx_loaded":true,"mdd_loaded":true,"anki_connected":true}

curl -s -X POST http://localhost:5050/add-word \
  -H "Content-Type: application/json" \
  -d '{"word": "ephemeral"}'
```

启动日志（含预热）：

```
INFO: Opening MDX dictionary: ...mdx
INFO: MDX dictionary ready.
INFO: Pre-warming MDX file cache...
INFO: MDX cache warm-up complete.
INFO: MDD audio file ready: ...mdd
INFO: Pre-warming MDD file cache (may take a few seconds on cold start)...
INFO: MDD cache warm-up complete.
INFO: HTTP client created.
INFO: Application startup complete.
INFO: Uvicorn running on http://0.0.0.0:5050
```

> **预热说明**：服务启动时对 MDX（80 MB）和 MDD（1005 MB）各做一次真实查词，将文件头索引块写入 OS 页面缓存。预热期间服务不接受请求，完成后冷启动问题消失。

---

## API 设计

### POST /add-word

**请求体**

```json
{
  "word": "ephemeral",
  "sentence": "The ephemeral nature of social media fame."
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `word` | string（必须） | 要查询的单词，不区分大小写，自动转小写 |
| `sentence` | string（可选） | 用户阅读时的原文语境句，留空则只用 LDOCE5 例句 |

**ODH 卡片字段映射**

| Anki 字段 | AI 模式（有 sentence） | 原有模式（无 sentence） |
|-----------|------------------------|------------------------|
| `word` | 单词原形（小写） | 单词原形（小写） |
| `pronunciation` | LDOCE5 IPA，如 `/ɪˈfemərəl/` | LDOCE5 IPA |
| `definition` | 匹配义项 HTML（单条） | 全部义项 HTML（多词性用 `<hr>` 拼接） |
| `sentence` | 用户原句 + `— LDOCE: 匹配义项例句` | LDOCE5 第一义项第一例句 |
| `audio` | LDOCE5 英式发音 `[sound:GB_xxx.spx]` | LDOCE5 英式发音 |
| `extrainfo` | 空 | 空 |
| `url` | 空 | 空 |

**`sentence` 字段格式（AI 模式）**

```
The ephemeral beauty of cherry blossoms reminded her to cherish the present.

— LDOCE: Fame is ephemeral, but art endures.
```

原有模式（无 sentence）：只存 LDOCE5 第一义项第一例句；若该词无例句（如 hallucinate），字段为空。

**错误响应**

| HTTP 状态 | detail 格式 | 说明 |
|-----------|-------------|------|
| 404 | `not found: 'xyz' is not in the dictionary` | LDOCE5 中没有该词 |
| 422 | `duplicate: cannot create note...` | Anki 中已存在该卡片 |
| 422 | `invalid: word cannot be empty` | 空词 |
| 502 | `AnkiConnect is not reachable` | Anki 未运行或 AnkiConnect 故障 |

---

## 第三步：iPhone 快捷指令

### 功能目标

- 在任意 App 中选中整句话 → Share → 触发快捷指令 → 弹窗输入目标单词
- 自动检测 Wi-Fi，在家走内网，外出走 Tailscale
- 发送单词 + 语境句到 ldoce-api（AI 消歧模式）
- 成功后弹出通知；LLM 不可用时通知显示 ⚠️ 警告

### 启用 Share Sheet 触发（iOS 18）

新建快捷指令后，点右上角 **ⓘ** 按钮 → **Details** → 开启 **Show in Share Sheet** → 勾选 **Text** 作为输入类型。

设置完成后，选中文本 → Share → 在分享面板里选择该快捷指令即可触发。共享进来的文本在快捷指令内称为 **Shortcut Input**。

### 动作流程

使用方式：在阅读 App 中选中**整句语境**（含目标单词）→ Share → 触发快捷指令 → 弹窗中输入目标单词。

```
Action 1: Get Network Details
          Type: Network Name

Action 2: Ask for Text
          Prompt: "Which word?"
          → magic variable: Provided Input

Action 3: If  Network Details  is  <your home Wi-Fi SSID>
    Get contents of  http://192.168.x.x:5050/add-word
        Method:   POST
        Headers:  Content-Type: application/json
        Body:     JSON
                    word     = Provided Input   ← Ask for Text 的结果
                    sentence = Shortcut Input   ← Share Sheet 传入的语境句
  Otherwise:
    Get contents of  http://<tailscale-ip>:5050/add-word
        （POST / Headers / Body 配置与上方完全相同）
End If

Action 4: Get Dictionary Value
          Key:  word
          From: Contents of URL
          → Set Variable: addedWord

Action 5: Get Dictionary Value
          Key:  warning
          From: Contents of URL

Action 6: If  Dictionary Value  has any value   ← warning 非空 = LLM 失败
    Show Notification
        Title:  Anki ⚠️
        Body:   AI disabled — [addedWord] added
  Otherwise:
    Show Notification
        Title:  Anki ✅
        Body:   [addedWord] added
End If
```

> **⚠️ 关键：URL 不能用变量传递**
> iOS Shortcuts 变量不保留类型信息，存入变量的 URL 会变成 Rich text，导致 "Get Contents of URL" 报错。
> 解决方法：在 If / Otherwise 两个分支内各放一个 **Get Contents of URL**，URL 直接硬编码，不经过变量。

> **只查单词、不附语境句**：去掉 Ask for Text，将 `word` 改为 `Shortcut Input`（用户选中单词直接共享），同时删除 `sentence` 字段。此模式不调用 AI，`definition` 为全部义项 HTML，`sentence` 为 LDOCE5 第一例句。

### 注意事项

- **定位权限**：iOS 读取 Wi-Fi SSID 需要定位权限，首次运行时允许
- **蜂窝网络**：不连 Wi-Fi 时 Network Details 为空，自动走 Tailscale 分支
- **局域网 IP 固定**：在路由器按 VM 的 MAC 地址做 DHCP 静态绑定
- **Tailscale**：iPhone 和 VM 均安装 Tailscale，外出时确保 iPhone 端已连接
- **AI 功能开关**：服务端 `LLM_API_KEY` 为空时不调用 AI，降级为原有逻辑（即使请求中包含 `sentence`）
- **API warning 字段**：LLM 调用失败时响应包含 `"warning": "AI unavailable — used default sense"`，快捷指令据此显示 ⚠️ 通知

---

## 关键实现细节

### LDOCE5 多词性处理

LDOCE5 对高频词按词性分 key 存储：

```
work ,noun   →  名词条目 HTML
work ,verb   →  动词条目 HTML
```

`MdxWrapper.mdx_lookup` 用如下 SQL 同时查出所有词性：

```sql
SELECT key_text FROM MDX_INDEX
WHERE key_text = 'work' COLLATE NOCASE
   OR key_text LIKE 'work ,%' COLLATE NOCASE
```

所有词性 HTML 按词典原始顺序拼接，`definition` 字段包含全部词性（原有模式）；AI 模式下只写入匹配义项的 HTML 片段。

### 冷启动预热

`mdict-utils` 每次调用 `query()` 都需要解析 MDict 文件的 key block（约 5 MB），在 OS 页面缓存冷的情况下首次访问 1005 MB MDD 耗时约 10 秒，会阻塞 asyncio 事件循环并导致 AnkiConnect TCP 连接超时（502 错误）。

解决方案：

1. 服务启动时在线程池中对 MDX 和 MDD 各执行一次真实查词（warmup）
2. 所有字典 I/O（`_lookup_word`、`mdd_lookup`）均通过 `asyncio.run_in_executor` 运行，不阻塞事件循环
3. `httpx.AsyncHTTPTransport(retries=1)` 处理 AnkiConnect 在 storeMediaFile 后关闭连接的情况

### 音频提取流程

1. 从 `glossary` HTML 中提取 `href="sound://GB_xxx.spx"` 中的文件名
2. 在 MDD SQLite 索引中查找该文件名（key 带 `\` 前缀）
3. 用 `mdict-utils` 读取音频字节（OGG Speex 格式）
4. base64 编码后通过 AnkiConnect `storeMediaFile` 上传
5. `audio` 字段写入 `[sound:GB_xxx.spx]`

---

## 常见问题排查

| 问题 | 解决方法 |
|------|----------|
| 502 AnkiConnect is not reachable | 确认 Anki 正在运行；`systemctl status ldoce-api` 查看日志 |
| 404 not found | 单词不在 LDOCE5，确认拼写；LDOCE5 收录约 22 万词条 |
| 422 duplicate | 该词已在 ODH 牌组，正常拒绝 |
| 启动报 MDX index DB not found | 确认 `.mdx.db` 文件与 `.mdx` 文件在同一目录 |
| audio 字段为空 | 检查 `.env` 中 `LDOCE5_MDD_PATH` 是否设置且文件存在 |
| 快捷指令报连接失败 | 检查 VM IP、端口 5050 是否开放；外网检查 Tailscale 是否连接 |

---

## 参考

- [AnkiConnect 官方文档](https://foosoft.net/projects/anki-connect/)
- [mdict-utils](https://github.com/liuyug/mdict-utils)
- [Tailscale 官网](https://tailscale.com/)
- [Apple 快捷指令用户手册](https://support.apple.com/zh-cn/guide/shortcuts/welcome/ios)
