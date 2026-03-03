# 迁移方案：完全去除 Anki Desktop，直接使用 `anki` Python 包

## 目标

**彻底移除 Anki Desktop Docker 容器**，让 FastAPI 服务用 `anki` Python 包
自己管理本地 collection，并直接通过 anki-syncserver 的同步协议推送到 iOS。

---

## 现有架构

```
iPhone Shortcuts
      │ POST /add-word
      ▼
FastAPI (port 5050)
      │ HTTP (AnkiConnect)
      ▼
Anki Desktop Docker (port 8765)   ← AnkiConnect add-on
      │ collection I/O
      ├──▶ /root/anki/anki_data/.../collection.anki2
      │
      │ sync（Anki Desktop 定时发起）
      ▼
anki-syncserver (port 27701)
      │ /root/.syncserver/anki/collection.anki2
      │ /root/.syncserver/anki/media/  (15,182 个文件)
      ▼
AnkiMobile (iOS)
```

---

## 目标架构

```
iPhone Shortcuts
      │ POST /add-word (port 5051)
      ▼
anki-writer (port 5051)          ← 新服务，专职卡片写入 + sync
      │ 内部调用 localhost:5050
      ▼
ldoce5-api (port 5050)           ← 不变，词典查询 + AI 选义
      │
anki-writer 同时：
      │ anki Python 包
      ├── 写入 /opt/iphone-anki-sync/collection.anki2（服务独占）
      ├── 写入 collection.media/（音频文件）
      │
      │ col.sync_collection(auth, sync_media=True)
      ▼
anki-syncserver (port 27701)     ← 保留，不动
      │
      ▼
AnkiMobile (iOS)
```

**移除：** Anki Desktop Docker 容器、AnkiConnect HTTP 调用、docker-compose.yml 的 anki service
**保留：** anki-syncserver（完全不需要改动）、所有现有卡片数据
**新增：** anki-writer 独立服务（port 5051），ldoce5-api 保持 port 5050 不变

---

## 技术基础（已验证）

### anki 包的 sync API（版本 25.9.2）

```python
from anki.collection import Collection

col = Collection(path)

# 1. 登录获取 session
auth = col.sync_login(
    username="anki",        # SYNC_USER1 的用户名
    password="anki",        # SYNC_USER1 的密码
    endpoint="http://localhost:27701/"
)
# auth.hkey = session key（字符串）

# 2. 同步 collection（双向合并）+ media
output = col.sync_collection(auth, sync_media=True)
# output 是 SyncOutput protobuf，包含同步结果

# 3. 或者单独同步 media
col.sync_media(auth)

col.close()
```

这和 Anki Desktop 内部用的是完全相同的 Rust 实现（通过 Python bindings 暴露）。

---

## 实现方案

### 核心思路

FastAPI 服务维护**自己专属**的一个 `collection.anki2`，每次 `/add-word` 请求：

1. 打开 collection
2. 写入新卡片 + 音频文件
3. 调用 `sync_collection` 推送到 anki-syncserver
4. 关闭 collection
5. AnkiMobile 下次 sync 时自动拿到新卡片

### collection 文件位置

```
/opt/iphone-anki-sync/collection.anki2      ← FastAPI 服务独占
/opt/iphone-anki-sync/collection.media/     ← 音频文件目录（anki 包自动管理）
```

### 初始化（一次性）

在 Anki Desktop 仍运行期间，先在旧链路执行一次最终 sync，再拉取全量数据到本地 collection（见迁移步骤 Step 3-4）：

```python
from anki.collection import Collection

col = Collection("/opt/iphone-anki-sync/collection.anki2")
auth = col.sync_login("anki", "anki", "http://localhost:27701/")
# 第一次 sync：把 syncserver 上的全量数据拉下来
output = col.sync_collection(auth, sync_media=True)
# sync_media=True 会下载全部 15,182 个媒体文件（只需一次）
col.close()
```

初始化完成后，后续 sync 只传输增量。

---

## anki_writer.py 实现要点

### 环境变量（新增到 .env）

```ini
# 替代 ANKI_CONNECT_URL
COLLECTION_PATH=/opt/iphone-anki-sync/collection.anki2
ANKI_SYNC_URL=http://localhost:27701/
ANKI_SYNC_USER=anki
ANKI_SYNC_PASSWORD=anki
LDOCE5_API_URL=http://localhost:5050
API_PORT=5051
```

### POST /add-word 内部流程

按 `api-design.md` 规定，`anki-writer` 自身不加载词典，所有词典操作通过调用 `ldoce5-api` 完成：

```python
# 1. 查词（获取所有义项 + AI 建议义项）
# 无 sentence 时不传该参数，避免空字符串被服务端误判为"有 sentence"
params = {"word": word}
if sentence:
    params["sentence"] = sentence
resp = await ldoce5_client.get(f"{LDOCE5_API_URL}/lookup", params=params)
lookup = resp.json()   # 含 senses、selected_sense_index、audio、warning

# 2. 义项选择
sense_idx = sense_index if sense_index is not None else lookup["selected_sense_index"]
sense = lookup["senses"][sense_idx]

# 3. 下载音频（UK 优先）
# audio.*.url 是相对路径（如 /audio/GB_kind_n0205.mp3），需拼接 LDOCE5_API_URL
audio_rel = (lookup["audio"].get("uk") or lookup["audio"].get("us") or {}).get("url")
audio_url = urljoin(LDOCE5_API_URL, audio_rel) if audio_rel else None
# ldoce5-api 的 /audio 端点返回已转换的 mp3（MDD 内 .spx 由 ldoce5-api 在响应前转换）
audio_filename, audio_data = await _fetch_audio(audio_url) if audio_url else (None, None)

# 4. 写 collection + sync（在 thread executor 中）
note_id = await loop.run_in_executor(
    None, _add_note_and_sync,
    COLLECTION_PATH, ANKI_SYNC_URL, ANKI_SYNC_USER, ANKI_SYNC_PASSWORD,
    word, lookup["pronunciation"], sense["definition_html"],
    sentence or sense["example"], audio_filename, audio_data,
    DECK_NAME, NOTE_TYPE_NAME,
)

# 5. 返回（符合 api-design.md 响应格式）
return {
    "word": word,
    "sense_used": sense_idx,
    "definition": sense["definition_html"],
    "note_id": note_id,
    "warning": lookup.get("warning"),
}
```

### collection 写入 wrapper

```python
import os
import threading
from anki.collection import Collection

_col_lock = threading.Lock()   # 进程内串行化（anki 包不是线程安全的）

def _add_note_and_sync(
    collection_path: str,
    sync_url: str, sync_user: str, sync_password: str,
    word: str, pronunciation: str, definition: str,
    sentence: str, audio_filename: str, audio_data: bytes,
    deck_name: str, note_type_name: str,
) -> int:
    """运行在 thread executor 中。返回新 note ID，失败时抛异常。"""
    with _col_lock:
        col = Collection(collection_path)
        try:
            if audio_filename and audio_data:
                media_path = os.path.join(col.media.dir(), audio_filename)
                with open(media_path, "wb") as f:
                    f.write(audio_data)

            deck_id = col.decks.id(deck_name)
            notetype = col.models.by_name(note_type_name)
            note = col.new_note(notetype)
            note["word"] = word
            note["pronunciation"] = pronunciation
            note["definition"] = definition
            note["sentence"] = sentence
            note["audio"] = f"[sound:{audio_filename}]" if audio_filename else ""
            note["extrainfo"] = ""
            note["url"] = ""
            col.add_note(note, deck_id)
            col.save()

            auth = col.sync_login(sync_user, sync_password, sync_url)
            col.sync_collection(auth, sync_media=True)

            return note.id
        finally:
            col.close()
```

### ldoce5-api 的 main.py 改动

`ldoce5-api` 的 `main.py` 需要中等程度重构（详见 Step 6），包括：

- 新增 `GET /lookup` 端点（暴露 senses 列表 + audio URLs）
- 新增 `GET /audio/{filename}` 端点（返回 mp3 流）
- 删除 `POST /add-word` 端点（迁移到 `anki_writer.py`）
- lifespan 删除 AnkiConnect `http_client`
- `/health` 响应更新（`anki_connected` → `llm_enabled`）
- `_extract_senses()` 中 `html` 字段重命名为 `definition_html`

lifespan 中需要删除的代码：

```python
# lifespan 删除：
#   app.state.http_client (AnkiConnect)
#   httpx.AsyncHTTPTransport(retries=1)

# 只保留：
app.state.llm_client = httpx.AsyncClient(timeout=15.0)
```

> **音频处理：** MDD 内存储的是 `.spx` 格式，`ldoce5-api` 的 `/audio/{filename}` 端点负责
> 读取 MDD 并通过 ffmpeg 转换为 mp3 后返回（`audio/mpeg`）。`anki-writer` 只需 GET 该端点
> 拿到 mp3 字节，写入 `col.media.dir()`，无需自行转换。注意使用 `urljoin` 拼接完整 URL。

---

## 服务文件结构

迁移完成后，宿主机 `/opt/iphone-anki-sync/` 的关键文件：

```
/opt/iphone-anki-sync/
  main.py          ← ldoce5-api（重构后，port 5050）
  anki_writer.py   ← anki-writer（新建，port 5051）
  requirements.txt ← 两个服务共用（含 anki==25.9.2）
  .env             ← 含新增变量，已移除废弃的 ANKI_CONNECT_URL
  collection.anki2 ← anki-writer 专属 collection（Step 4 初始化）
  collection.media/ ← 音频文件目录（anki 包自动管理）
```

---

## 迁移步骤

> **策略：** AnkiConnect 运行在 8765，anki-writer 运行在 5051，端口不冲突。
> 因此可以先部署并验证新服务，确认无误后再下线 Anki Desktop，实现零停机切换。

### Step 1：安装 anki 包并更新 requirements.txt

```bash
# 版本必须与 anki-syncserver 一致
/opt/iphone-anki-sync/.venv/bin/pip install anki==25.9.2
```

在本地 `requirements.txt` 中追加（仅 `anki-writer` 使用，`ldoce5-api` 不 import 此包）：

```
anki==25.9.2
```

### Step 2：验证无 Qt 可用

```bash
/opt/iphone-anki-sync/.venv/bin/python -c "
from anki.collection import Collection
import tempfile, os
path = tempfile.mktemp(suffix='.anki2')
col = Collection(path)
print('OK, tables:', col.db.scalar('select count() from sqlite_master'))
col.close()
os.unlink(path)
"
```

### Step 3：在旧链路执行最后一次 sync（关键）

在 Anki Desktop 中手动触发一次完整 sync（Tools → Sync），确认无 pending 改动。
这确保 syncserver 上的数据是最新的，下一步初始化才完整。

### Step 4：初始化本地 collection（拉取全量数据）

```bash
/opt/iphone-anki-sync/.venv/bin/python -c "
from anki.collection import Collection
col = Collection('/opt/iphone-anki-sync/collection.anki2')
auth = col.sync_login('anki', 'anki', 'http://localhost:27701/')
col.sync_collection(auth, sync_media=True)
print('Synced. Notes:', col.db.scalar('select count() from notes'))
col.close()
"
```

> 首次运行会下载全部媒体文件（15,000+），可能需要数分钟。

### Step 5：编写 anki_writer.py

编写 `anki_writer.py`（新文件，port 5051），按上节实现要点实现 `/add-word` 和 `/health` 端点。

### Step 6：重构 ldoce5-api（main.py）

`ldoce5-api` 的 `main.py` 需要中等程度重构，具体改动清单：

- **(a)** 新增 `GET /lookup` 端点，返回 senses 列表、UK/US audio URLs、selected_sense_index
- **(b)** 新增 `GET /audio/{filename}` 端点，读取 MDD 并经 ffmpeg 转换后返回 mp3 流
- **(c)** 删除 `POST /add-word` 端点（已迁移到 `anki_writer.py`）
- **(d)** lifespan 删除 AnkiConnect `http_client`（`AsyncHTTPTransport(retries=1)`）
- **(e)** `/health` 响应更新：移除 `anki_connected`，改为 `llm_enabled`
- **(f)** `_extract_senses()` 中 `html` 字段重命名为 `definition_html`

#### 音频提取逻辑扩展

`_extract_audio_filename()` 需拆分为 `_extract_audio_filenames(html)`，同时提取 UK 和 US 音频：

```python
def _extract_audio_filenames(html: str) -> dict:
    """Return {"uk": "GB_xxx.spx", "us": "US_xxx.spx"}, values may be None."""
    uk_match = re.search(r'href="sound://(GB_[^"]+\.spx)"', html)
    us_match = re.search(r'href="sound://(US_[^"]+\.spx)"', html)
    return {
        "uk": uk_match.group(1) if uk_match else None,
        "us": us_match.group(1) if us_match else None,
    }
```

`GET /lookup` 响应中的 `audio` 字段格式（与 `api-design.md` 对齐）：

```json
{
  "uk": {"filename": "GB_xxx.spx", "url": "/audio/GB_xxx.mp3"},
  "us": {"filename": "US_xxx.spx", "url": "/audio/US_xxx.mp3"}
}
```

> 注：`filename` 字段保留原始 `.spx` 名，`url` 字段使用转换后的 `.mp3` 名（相对路径）。

### Step 7：部署代码并配置 systemd service

此时两套链路并行：旧链路（AnkiConnect 8765）仍可用，新链路（anki-writer 5051）即将上线。

```bash
# 先传代码和更新 .env，再启动服务
scp anki_writer.py main.py root@192.168.1.100:/opt/iphone-anki-sync/

# 在目标机上更新 .env（追加新增变量，移除废弃的 ANKI_CONNECT_URL）
ssh root@192.168.1.100 "cat >> /opt/iphone-anki-sync/.env << 'EOF'
COLLECTION_PATH=/opt/iphone-anki-sync/collection.anki2
ANKI_SYNC_URL=http://localhost:27701/
ANKI_SYNC_USER=anki
ANKI_SYNC_PASSWORD=anki
LDOCE5_API_URL=http://localhost:5050
API_PORT=5051
EOF"

# 移除已废弃的 ANKI_CONNECT_URL（ldoce5-api 重构后不再使用）
ssh root@192.168.1.100 "sed -i '/^ANKI_CONNECT_URL=/d' /opt/iphone-anki-sync/.env"

# 配置并启动 systemd service
ssh root@192.168.1.100 "cat > /etc/systemd/system/anki-writer.service << 'EOF'
[Unit]
Description=Anki Writer Service
After=network.target ldoce5-api.service

[Service]
Type=simple
User=root
WorkingDirectory=/opt/iphone-anki-sync
ExecStart=/opt/iphone-anki-sync/.venv/bin/python anki_writer.py
EnvironmentFile=/opt/iphone-anki-sync/.env
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload && systemctl enable anki-writer && systemctl start anki-writer"
```

### Step 8：验证新服务（并行运行期间）

```bash
# 健康检查
curl http://192.168.1.100:5051/health
# 预期：{"status":"ok","collection_accessible":true,"ldoce5_api_reachable":true,...}

# 测试已存在的词
curl -X POST http://192.168.1.100:5051/add-word \
  -H "Content-Type: application/json" \
  -d '{"word":"ephemeral","sentence":"..."}'
# 预期：422 duplicate（已存在）

# 新词（验证写入 + sync 全链路）
curl -X POST http://192.168.1.100:5051/add-word \
  -H "Content-Type: application/json" \
  -d '{"word":"obstinate","sentence":"..."}'
# 预期：200 OK
```

在 AnkiMobile 手动触发一次 sync，确认新卡片出现。**验证通过后再执行下一步。**

### Step 9：下线 Anki Desktop

```bash
cd /root/anki
docker compose stop anki   # 或 docker compose down
# 确认 port 8765 已释放
```

### Step 10：更新 iPhone Shortcuts

Shortcuts 里有两处 URL 需要从 **port 5050 改为 5051**（`/add-word` 已迁移到 anki-writer）：

- **Wi-Fi 分支**：`http://192.168.x.x:5050/add-word` → `http://192.168.x.x:5051/add-word`
- **Tailscale 分支**：`http://<tailscale-ip>:5050/add-word` → `http://<tailscale-ip>:5051/add-word`

> 注意：iOS Shortcuts 的 URL 不能通过变量传递（会变成 Rich text 导致报错），两个分支必须各自硬编码修改。

### Step 11：最终验证

用 iPhone Shortcuts 添加一个新词，确认流程端到端正常。

---

## 风险评估

| 风险 | 分析 |
|------|------|
| `anki` 包需要 Qt | 经验证：`Collection` 用 Rust 后端，不需要 Qt |
| anki 包版本与 syncserver 不一致 | **必须锁定** `anki==25.9.2`，否则 protobuf schema 可能不兼容 |
| sync 期间 syncserver 也在处理 AnkiMobile sync | 罕见（用户主动触发），SQLite WAL 处理并发，最差情况是我们的写入稍有延迟 |
| 初始化时媒体文件下载超时 | 首次 sync 可能需要 5–10 分钟，之后全部增量 |
| `_col_lock` 导致并发请求串行 | 可接受：这是个人工具，并发量为 0–1 |
| 现有 .spx → .mp3 转换逻辑 | 保留，只是写文件的位置从 AnkiConnect 改为 `col.media.dir()` |

---

## 对比简单修复方案

如果暂时不想做架构迁移，一行代码可以修复当前的 502 问题：

```python
# 给所有 AnkiConnect 请求加 Connection: close，禁用 keep-alive
resp = await http_client.post(
    ANKI_CONNECT_URL, json=payload,
    headers={"Connection": "close"},
)
```

代价仅是每次多一次 TCP 握手（约 1ms）。两个方案不互斥，可以先打这个补丁，
再在合适的时候做完整迁移。
