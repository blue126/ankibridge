# LDOCE5 API 设计规范

## 服务拆分

| 服务 | 职责 | 端口 |
|------|------|------|
| `ldoce5-api` | 词典查询、AI 选义 | 5050 |
| `anki-writer` | Anki 卡片写入 + sync | 5051 |

---

## ldoce5-api

### `GET /lookup`

**用途：** 查词。返回所有义项、AI 建议选义、发音、音频。

**查询参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `word` | string | 是 | 要查的单词 |
| `sentence` | string | 否 | 上下文例句，有则触发 AI 选义（max 2000 字符） |

**响应 200：**

```json
{
  "word": "kind",
  "pronunciation": "/kaɪnd/",
  "audio": {
    "uk": {
      "filename": "GB_kind_n0205.mp3",
      "url": "/audio/GB_kind_n0205.mp3"
    },
    "us": {
      "filename": "US_kind1.mp3",
      "url": "/audio/US_kind1.mp3"
    }
  },
  "senses": [
    {
      "index": 0,
      "pos": "adjective",
      "sense_num": 1,
      "definition": "saying or doing things that show that you care about other people",
      "definition_html": "<span class=\"Sense\">...</span>",
      "example": "They've been very kind to me.",
      "ai_selected": true
    },
    {
      "index": 1,
      "pos": "noun",
      "sense_num": 1,
      "definition": "a type or sort of something",
      "definition_html": "<span class=\"Sense\">...</span>",
      "example": "What kind of music do you like?",
      "ai_selected": false
    }
  ],
  "selected_sense_index": 0,
  "warning": null
}
```

**字段说明：**

| 字段 | 说明 |
|------|------|
| `pronunciation` | IPA，格式 `/ɪˈfemərəl/` |
| `audio.uk` / `audio.us` | 可能为 null（词典无音频） |
| `audio.*.url` | 相对路径，指向同服务的 `/audio/{filename}` |
| `senses[].definition` | 纯文本（Alfred / 无障碍） |
| `senses[].definition_html` | LDOCE5 原始 HTML 片段（浏览器插件渲染） |
| `senses[].ai_selected` | true 表示 AI 认为这是最匹配上下文的义项 |
| `selected_sense_index` | `senses[].ai_selected=true` 对应的 index；无 sentence 时为 0 |
| `warning` | AI 不可用时的提示字符串，否则为 null |

**错误响应：**

| 状态码 | detail 示例 |
|--------|------------|
| 404 | `"not found: 'foobar' is not in the dictionary"` |
| 422 | `"invalid: word exceeds maximum length of 100 characters"` |

---

### `GET /audio/{filename}`

**用途：** 返回音频文件（mp3）。供浏览器 `<audio>` 标签直接引用。

**路径参数：** `filename` 为 `/lookup` 响应中 `audio.*.filename` 的值。

**响应：** `200 audio/mpeg`（二进制流）

**错误：** `404` 若文件不在 MDD 中。

---

### `GET /health`

```json
{
  "status": "ok",
  "mdx_loaded": true,
  "mdd_loaded": true,
  "llm_enabled": true
}
```

---

## anki-writer

### `POST /add-word`

**用途：** 根据词典数据创建 Anki 卡片并同步。

**请求体：**

```json
{
  "word": "kind",
  "sentence": "She is very kind to everyone.",
  "sense_index": 0
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `word` | string | 是 | 单词（max 100 字符） |
| `sentence` | string | 否 | 用户上下文例句（max 2000 字符） |
| `sense_index` | integer | 否 | 客户端指定义项（覆盖 AI 选义）；省略则由 AI 自动选择 |

**`sense_index` 的使用场景：**
- iPhone Shortcuts：省略（AI 自动选）
- 浏览器插件：用户从 `/lookup` 返回的义项列表中手动确认后传入
- Alfred：省略（AI 自动选）

**内部流程：**

```
1. 调用 ldoce5-api /lookup?word=...&sentence=...
2. 若请求体含 sense_index → 忽略 AI 选义，用指定的 sense
   若无 sense_index → 使用 /lookup 返回的 selected_sense_index
3. 写入 anki collection（anki Python 包）
4. 存音频到 collection.media/
5. col.sync_collection(auth, sync_media=True)
```

**响应 200：**

```json
{
  "word": "kind",
  "sense_used": 0,
  "definition": "<span class=\"Sense\">...</span>",
  "note_id": 1772453695307,
  "warning": null
}
```

**错误响应：**

| 状态码 | detail 示例 |
|--------|------------|
| 404 | `"not found: 'foobar' is not in the dictionary"` |
| 422 | `"duplicate: cannot create note because it is a duplicate"` |
| 502 | `"ldoce5-api is not reachable"` |
| 502 | `"anki sync failed"` |

---

### `GET /health`

```json
{
  "status": "ok",
  "collection_accessible": true,
  "ldoce5_api_reachable": true,
  "last_sync": "2026-03-02T12:14:17Z"
}
```

---

## 各客户端调用流程

### iPhone Shortcuts（一步完成）

```
POST anki-writer /add-word
  { "word": "kind", "sentence": "She is kind to everyone." }
→ 200 OK（或 422 duplicate）
→ 通知用户结果
```

### 浏览器插件（两步，用户确认）

```
Step 1: GET ldoce5-api /lookup?word=kind&sentence=...
→ 展示义项列表，高亮 AI 建议的 sense
→ 用户可修改选择
→ 播放 /audio/GB_kind_n0205.mp3

Step 2（用户点击"存入 Anki"）:
POST anki-writer /add-word
  { "word": "kind", "sentence": "...", "sense_index": 0 }
→ 提示保存成功
```

### Alfred workflow（纯查词入口）

```
GET ldoce5-api /lookup?word=kind      ← 无 sentence，不触发 AI 选义

→ Alfred Large Type 展示义项列表（纯文本 senses[].definition）
→ 播放 UK/US 发音

（可选）用户按快捷键:
POST anki-writer /add-word
  { "word": "kind" }                  ← 无 sentence、无 sense_index，
                                         anki-writer 使用 selected_sense_index=0 的义项 HTML
→ Alfred 通知：「✓ kind 已加入 Anki」
```

Alfred 使用 `senses[].definition`（纯文本）展示义项，不渲染 HTML。

---

## 设计决策记录

| 决策 | 选择 | 理由 |
|------|------|------|
| 音频传输方式 | 独立 `/audio/{filename}` 端点 | 浏览器可直接用 URL 作 `<audio src>`，天然支持缓存 |
| 义项选择权 | 客户端可覆盖（`sense_index`） | 浏览器插件需要让用户确认；AI 不是最终裁判 |
| 纯文本 vs HTML | 两者都返回（`definition` 纯文本 + `definition_html`） | Alfred 用 `definition` 纯文本，浏览器插件用 `definition_html` 渲染 |
| 两服务通信 | anki-writer 内部调 ldoce5-api | 单一数据源，避免重复加载 MDX 文件 |
| `sentence` 字段 | 两个服务都接受 | `/lookup` 用于 AI 选义，`/add-word` 用于写入卡片例句字段 |
| 浏览器插件方案 | Fork ODH，替换 Anki 保存逻辑 | ODH 词典脚本机制灵活（`findTerm` 接口），但 Anki 保存只支持 AnkiConnect/AnkiWeb，必须 fork 才能改为调 anki-writer `/add-word`，同时彻底去除 Anki Desktop 依赖 |
| LDOCE5 CSS 加载 | ldoce5-api 提供 `GET /static/ldoceAZ.css`，返回 HTML 中将相对路径替换为绝对路径 | ODH 弹窗是沙盒 iframe，相对路径 CSS 引用会 404 |
