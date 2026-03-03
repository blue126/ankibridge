---
title: 'iPhone-Anki 查词同步服务'
slug: 'iphone-anki-sync-api'
created: '2026-02-28'
updated: '2026-03-02'
status: 'completed'
stepsCompleted: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18]
tech_stack: ['Python 3.10+', 'FastAPI', 'uvicorn', 'httpx', 'mdict-query', 'pydantic']
files_to_modify: ['main.py (modify)', '.env.example (modify)', 'tests/test_main.py (modify)']
code_patterns: ['Clean Slate - new project, no existing patterns']
test_patterns: ['pytest + httpx AsyncClient for API tests']
---

# Tech-Spec: iPhone-Anki 查词同步服务

**Created:** 2026-02-28

## Overview

### Problem Statement

在 iPhone 上阅读时查词后，需要手动打开 Anki 桌面端创建卡片，流程繁琐且打断阅读体验。需要一个自动化方案，实现选词后一键创建 Anki 卡片，且使用高质量的 LDOCE5（朗文当代高级英语词典第五版）英英释义。

### Solution

在 PVE 虚拟机上部署 FastAPI 中间 API 服务（与已有的无头 Anki client 在同一台 VM 上）。iPhone 快捷指令发送单词到该 API，API 从本地 LDOCE5 MDX 词典文件查询英英释义，然后通过 AnkiConnect API 自动创建 ODH 卡片（多字段笔记类型）。通过 Tailscale 实现外网访问。

### Scope

**In Scope:**
- FastAPI 服务（`/add-word` 和 `/health` 端点）
- 本地 LDOCE5 MDX 文件解析与查词（通过 mdict-query）
- AnkiConnect 集成（创建 ODH 卡片到 ODH 牌组）
- 三层错误处理（词典/连接/业务）
- Tailscale 外网访问配置说明
- iPhone 快捷指令配置说明

**Out of Scope:**
- n8n 工作流方案
- 中文翻译功能
- Web UI / 管理界面
- Anki 桌面端安装与 AnkiConnect 插件配置（已有）
- MDX HTML 输出清理（先测试原始效果再决定）

## Context for Development

### Codebase Patterns

- **Confirmed Clean Slate** — 全新项目，无现有代码，无遗留约束
- 运行环境：PVE 虚拟机，已运行无头 Anki client（通过 Xvfb）
- 网络：通过 Tailscale 实现 iPhone 到 VM 的连接
- AnkiConnect 已在 VM 上的 Anki client 中配置并运行于 `localhost:8765`
- 项目结构采用简单的单文件 FastAPI 应用（代码量小，不需要复杂的模块化）
- **[Party Mode 共识]** MDX IndexBuilder 在应用启动时通过 FastAPI lifespan 加载（避免首次请求延迟）
- **[Party Mode 共识]** 加 `GET /health` 健康检查端点（检测 MDX 索引状态 + AnkiConnect 连通性）
- **[Party Mode 共识]** 错误处理分三层：词典查询失败(404) / AnkiConnect 不可达(502) / AnkiConnect 业务错误(422)
- **[Party Mode 共识]** LDOCE5 MDX 的 HTML 输出可能包含 CSS class 引用和资源链接，需确认渲染效果；Anki 原生支持 HTML，先不做清理，实际测试后再决定

### Files to Reference

| File | Purpose |
| ---- | ------- |
| `anki-iphone-guide.md` | 原始实现指南，包含架构概览和 FastAPI 代码示例 |

### Technical Decisions

- **词典库**：使用 `mdict-query` (IndexBuilder) 解析 LDOCE5 MDX 文件
  - 从 GitHub 安装：`pip install git+https://github.com/mmjang/mdict-query.git`
  - 首次加载会自动构建 SQLite 索引（`.mdx.db`），后续查询很快
  - 返回 **list** 类型的 HTML 释义（可能含多个结果）
  - 结果处理逻辑：过滤空字符串 → 检测 `@@@LINK=` 重定向条目并递归查询目标词 → 取第一个有效 HTML 结果 → 多个结果用 `<hr>` 拼接
  - 需要对用户输入做基本清理（strip + lowercase），不做过度严格的字符过滤（见 F4）
- **AnkiConnect API v6**：
  - `addNote` action 创建卡片，内置重复检查（`allowDuplicate: false`）
  - `version` action 用于健康检查
  - 响应格式固定：`{"result": ..., "error": ...}`
- **卡片格式**：ODH 笔记类型（多字段：word, pronunciation, definition, sentence, audio 等）
- **牌组名称**：`ODH`
- **标签**：`iphone-capture`
- **配置管理**：通过环境变量配置 MDX 文件路径和 AnkiConnect URL
- **部署方式**：直接运行在 PVE VM 上，与无头 Anki client 同机
- **外网访问**：Tailscale VPN 隧道（iPhone 和 VM 都安装 Tailscale）

## Implementation Plan

### Tasks

- [x] Task 1: 创建项目依赖文件
  - File: `requirements.txt` (create)
  - Action: 列出所有 Python 依赖及版本约束
  - Notes: 包含 fastapi, uvicorn, httpx, pydantic, python-dotenv, mdict-query (GitHub URL, pin 到具体 commit), python-lzo

- [x] Task 2: 创建环境变量配置模板
  - File: `.env.example` (create)
  - Action: 定义所有配置项及默认值/说明
  - Notes: 包含 `LDOCE5_MDX_PATH`（MDX 文件路径，必填）、`ANKI_CONNECT_URL`（默认 `http://localhost:8765`）、`DECK_NAME`（默认 `ODH`）、`NOTE_TYPE_NAME`（默认 `ODH`）、`API_HOST`（默认 `0.0.0.0`）、`API_PORT`（默认 `5050`）

- [x] Task 3: 实现 FastAPI 应用主文件 — 配置与模型定义
  - File: `main.py` (create)
  - Action: 定义以下内容：
    - 在模块顶部调用 `from dotenv import load_dotenv; load_dotenv()` 加载 `.env` 文件
    - 环境变量读取（使用 `os.environ.get()` + 默认值）
    - Pydantic 请求模型 `WordRequest(word: str)`
    - Pydantic 响应模型 `AddWordResponse(word: str, definition: str, anki_response: dict)`
    - 错误响应模型 `ErrorResponse(detail: str)`
  - Notes: 使用 `python-dotenv` 加载 `.env` 文件，不引入 pydantic-settings

- [x] Task 4: 实现 FastAPI 应用主文件 — Lifespan（MDX + HTTP Client）
  - File: `main.py` (continue)
  - Action: 实现以下内容：
    - FastAPI `lifespan` async context manager
    - **启动时**：
      - 验证 MDX 文件路径存在
      - 创建 `mdict_query.IndexBuilder` 实例，存入 `app.state.mdx_builder`
      - 创建 `httpx.AsyncClient(timeout=10.0)` 实例，存入 `app.state.http_client`
    - **关闭时**：
      - 调用 `await app.state.http_client.aclose()` 关闭 HTTP 连接池
    - 如果 MDX 文件不存在或加载失败，记录错误日志并 raise RuntimeError 终止启动
  - Notes: `IndexBuilder` 首次加载会构建 SQLite 索引，可能需要几十秒；`http_client` 全局复用，避免每请求创建连接池

- [x] Task 5: 实现 FastAPI 应用主文件 — `/health` 健康检查端点
  - File: `main.py` (continue)
  - Action: 实现 `GET /health` 端点：
    - 检查 `app.state.mdx_builder` 是否已加载
    - 使用 `app.state.http_client` 发送 `{"action": "version", "version": 6}` 到 AnkiConnect 验证连通性（已内置 10s timeout）
    - 返回 `{"status": "ok", "mdx_loaded": true, "anki_connected": true}` 或相应错误状态
  - Notes: 用于监控和 iPhone 快捷指令的前置检查

- [x] Task 6: 实现 FastAPI 应用主文件 — `/add-word` 核心端点
  - File: `main.py` (continue)
  - Action: 实现 `POST /add-word` 端点：
    1. 接收 `WordRequest`，清理输入：`word.strip().lower()`，拒绝空字符串和超长输入（>100 字符）
    2. 使用 `app.state.mdx_builder.mdx_lookup(word)` 查询 LDOCE5
    3. **处理 list 返回值**：
       a. 过滤空字符串和纯空白条目
       b. 检测 `@@@LINK=` 重定向：提取目标词，用 `mdx_lookup(target)` 递归查询（最多 3 层防循环）
       c. 如果所有结果都为空/重定向死循环，返回 404
       d. 多个有效结果用 `<hr>` 拼接
    4. 构造 AnkiConnect `addNote` 请求 payload（ODH 多字段格式：`word`、`pronunciation`、`definition`、`sentence`、`audio`、`extrainfo`、`url`，详见 Task 15 字段映射表）
    5. 使用 `app.state.http_client` POST 到 AnkiConnect URL（timeout=10s）
    6. 如果 AnkiConnect 不可达（ConnectionError/TimeoutException），返回 502 `{"detail": "AnkiConnect is not reachable"}`
    7. 如果 AnkiConnect 返回 error（用 `"duplicate" in error.lower()` 判断重复），返回 422 `{"detail": "{anki_error}"}`
    8. 成功时返回 200 `AddWordResponse`
  - Notes: 不使用严格字符过滤 regex，仅做长度和空值校验（允许 apostrophe、accent 等合法字符）

- [x] Task 7: 实现 FastAPI 应用主文件 — uvicorn 入口
  - File: `main.py` (continue)
  - Action: 添加 `if __name__ == "__main__"` 块，使用 `uvicorn.run()` 启动服务
  - Notes: host 和 port 从环境变量读取

- [x] Task 8: 创建测试文件
  - File: `tests/test_main.py` (create)
  - Action: 编写以下测试用例（mock MDX 和 AnkiConnect）：
    - `test_add_word_success` — 正常查词创卡
    - `test_add_word_not_found` — 单词不在词典中
    - `test_add_word_anki_unreachable` — AnkiConnect 不可达
    - `test_add_word_duplicate` — 重复卡片
    - `test_add_word_invalid_input` — 无效输入（特殊字符）
    - `test_health_all_ok` — 健康检查正常
    - `test_health_anki_down` — AnkiConnect 不可达时的健康检查
  - Notes: 使用 `pytest`, `httpx.AsyncClient`, `unittest.mock.patch` mock 外部依赖

- [x] Task 9: 编写部署与使用说明
  - File: `anki-iphone-guide.md` (update — 在末尾追加部署章节)
  - Action: 追加以下内容：
    - PVE VM 上的部署步骤（clone, pip install, 配置 .env, 启动服务）
    - Tailscale 配置要点
    - iPhone 快捷指令配置步骤（简化版，只需发一个 POST 到 `/add-word`）
    - systemd service 文件示例（用于开机自启）

### Acceptance Criteria

- [x] AC 1: Given 服务已启动且 MDX 已加载, when POST `/add-word` 请求体为 `{"word": "ephemeral"}`, then 返回 200 且响应包含 `word`, `definition`(非空 HTML), `anki_response`(含 result 字段)
- [x] AC2: Given 服务已启动, when POST `/add-word` 请求体为 `{"word": "xyznonexistent"}` (词典中不存在的词), then 返回 404 且 detail 包含 "not found"
- [x] AC3: Given 服务已启动但 AnkiConnect 未运行, when POST `/add-word` 请求体为 `{"word": "test"}`, then 返回 502 且 detail 包含 "AnkiConnect"
- [x] AC4: Given 同一单词已添加过, when POST `/add-word` 请求体为该重复单词, then 返回 422 且 detail 包含 "duplicate"
- [x] AC5: Given POST `/add-word` 请求体为 `{"word": ""}` 或 `{"word": "超过100字符的字符串"}` (实际超过 100 字符), then 返回 422 且 detail 包含 "invalid"
- [x] AC6: Given 服务已启动且所有依赖正常, when GET `/health`, then 返回 200 且包含 `{"status": "ok", "mdx_loaded": true, "anki_connected": true}`
- [x] AC7: Given 服务已启动但 AnkiConnect 未运行, when GET `/health`, then 返回 200 且 `anki_connected` 为 false
- [x] AC8: Given 服务部署在 PVE VM 上, when iPhone 通过 Tailscale IP POST `/add-word`, then 卡片成功出现在 Anki 的 ODH 牌组中

## Additional Context

### Dependencies

| Package | Version | Purpose |
| ------- | ------- | ------- |
| `fastapi` | >=0.100 | Web 框架 |
| `uvicorn[standard]` | >=0.20 | ASGI 服务器 |
| `httpx` | >=0.24 | 异步 HTTP 客户端（调用 AnkiConnect） |
| `pydantic` | v2 (FastAPI 内置) | 请求/响应数据验证 |
| `python-dotenv` | >=1.0 | 加载 `.env` 文件到环境变量 |
| `mdict-query` | GitHub (pin commit) | MDX 词典文件查询 |
| `python-lzo` | >=1.14 | MDX LZO 压缩解码（如果 MDX 使用 LZO 压缩） |
| `pytest` | >=7.0 (dev) | 测试框架 |
| `pytest-asyncio` | >=0.21 (dev) | 异步测试支持 |

**外部服务依赖：**
- AnkiConnect 插件运行在同一台 VM 的 `localhost:8765`（已有）
- Tailscale VPN 网络（iPhone 和 VM 都已安装）
- LDOCE5 MDX/MDD 文件在 VM 本地文件系统上

### Testing Strategy

**单元/集成测试（自动化）：**
- 使用 `pytest` + `httpx.AsyncClient` 对 FastAPI 端点做集成测试
- 使用 `unittest.mock.patch` mock `IndexBuilder.mdx_lookup()` 和 `httpx.AsyncClient.post()`
- 覆盖所有 7 个 AC 中可自动化的场景（AC 1-7）

**手动测试：**
- AC 8：在 iPhone 上通过 Tailscale 实际发送请求，验证端到端流程
- 检查 Anki 中创建的卡片，确认 LDOCE5 HTML 释义的渲染效果
- 如果 HTML 渲染效果不佳，记录为后续优化项

### Notes

- **高风险项**：LDOCE5 MDX 的 HTML 输出格式未知，可能需要后续 HTML 清理工作。先上线原始 HTML，根据实际渲染效果决定是否需要处理。
- AnkiConnect 默认监听 `localhost:8765`，由于 API 服务与 Anki 在同一台 VM 上，无需修改 `webBindAddress`
- LDOCE5 MDX 文件路径通过环境变量 `LDOCE5_MDX_PATH` 配置
- `mdict-query` 首次加载 MDX 时会在同目录下生成 `.mdx.db` 索引文件，需要对 MDX 文件所在目录有写权限
- 如果 MDX 文件使用 LZO 压缩（MDict v1.x），需要安装 `python-lzo`；v2.0+ 使用 zlib（Python 内置）
- **[备注-F2]** `mdict-query` 在 `requirements.txt` 中应 pin 到具体 commit hash，避免上游变动导致构建失败
- **[备注-F6]** AnkiConnect 重复卡片的错误消息用 `"duplicate" in error.lower()` 模糊匹配，而非硬编码完整字符串
- **未来扩展**（out of scope but noted）：中文释义支持、自定义笔记类型、批量添加、Web UI 管理

---

## Feature: AI 辅助词义消歧（AI-Assisted Sense Disambiguation）

**新增日期：** 2026-03-02

### Problem Statement

`sentence` 字段当前存放 LDOCE5 第一义项的第一例句，但用户在原文中遇到的单词很可能对应的是第二、第三义项。这导致 Anki 卡片记录的释义与用户实际阅读语境不符，违背了制卡的初衷。

### Solution

**iOS 端**采用方案 A：用户选中整个句子（而非单个单词）→ 共享 → 快捷指令弹窗"Which word?" → 用户输入目标单词 → 同时发送 `word` 和 `sentence`（原文语境句）到 API。

**API 端**引入 LLM 判断：当请求中包含 `sentence` 时，调用 LLM API（OpenAI-compatible，当前配置为 NVIDIA NIM Llama 3.1 Nemotron 70B，可替换）进行词义消歧，将匹配义项的 HTML + 该义项例句 + 用户原句一并存入 Anki。

无 `sentence` 字段时，行为与原有逻辑完全一致（向后兼容）。

### iOS Shortcut 改动（Approach A）

```
动作 1：接收共享输入（文本）
        ← 用户选中整句话

动作 2：获取网络详细信息（Wi-Fi 网络）→ currentSSID

动作 3：判断 SSID → 设置 apiURL（内网 or Tailscale，同现有逻辑）

动作 4：弹出文本输入框
        标题："Which word?"
        → 存储到变量: targetWord

动作 5：获取 URL 的内容
        URL: apiURL
        方法: POST
        请求头: Content-Type: application/json
        请求体: {"word": "[targetWord]", "sentence": "[快捷指令输入]"}

动作 6：从词典值获取值 → 键路径: word → addedWord

动作 7：显示通知
        标题: Anki ✅
        内容: 已添加「addedWord」
```

### API 改动

#### 1. 新增环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `LLM_API_KEY` | `""` | LLM 服务 API Key（空时跳过 AI，退化为旧逻辑） |
| `LLM_BASE_URL` | `https://integrate.api.nvidia.com/v1` | LLM 服务端点（OpenAI-compatible） |
| `LLM_MODEL` | `nvidia/llama-3.1-nemotron-70b-instruct` | 使用的模型 ID |

#### 2. `WordRequest` 模型变更

```python
class WordRequest(BaseModel):
    word: str
    sentence: Optional[str] = None   # 新增：用户原文语境句
```

#### 3. 新增函数：`_extract_senses(html: str) -> list[dict]`

从 LDOCE5 拼接 HTML 中提取各义项信息，供 LLM 做消歧判断。

**输入：** `_lookup_word` 返回的拼接 HTML（多词性条目以 `<hr>` 分隔）。

**逻辑：**
1. 以 `<hr>` 分割得到各词性块（block）
2. 每个 block 中提取 POS：`class="POS"` span 文本
3. 在 block 中找出所有 `<span class="Sense"` 的起始位置
4. 相邻 Sense 起始位置之间的 HTML 切片即为一个义项的 HTML
5. 每个义项提取：
   - `definition`：`class="DEF"` span 内纯文本（若 DEF span 缺失，则为 `""`）
   - `example`：第一个 `class="EXAMPLE"` 下 `class="BASE"` 的纯文本（若 EXAMPLE/BASE span 均缺失，则为 `""`）
   - `html`：原始 HTML 切片（用于写入 Anki `definition` 字段）

**返回：**

```python
[
  {
    "pos": "verb",          # 词性（空字符串若未找到）
    "sense_num": 1,         # 在当前词性内的义项序号（1-based）
    "definition": "...",    # 纯文本释义
    "example": "...",       # 第一例句纯文本（可能为空）
    "html": "<span ...",    # 该义项原始 HTML（用于写入 Anki `definition` 字段）
  },
  ...
]
```

若某 block 内无 `class="Sense"` span，则整个 block 作为一条义项处理。

#### 4. 新增函数：`_ai_pick_sense(word, context, senses, http_client) -> int`

调用 LLM API 判断哪个义项与语境匹配。

**Prompt 格式：**

```
You are a dictionary sense disambiguation assistant.

Word: "{word}"
Context sentence: "{context}"

Available senses:
1. [noun] the job you do regularly to earn money — e.g. "She works at a bank."
2. [noun] tasks that need to be done — e.g. "I have a lot of work to do."
3. [verb] to do a job that you are paid for — e.g. "He works for a tech company."
4. [verb] to operate or function correctly — e.g. "The machine isn't working."

Which sense number (1-4) best matches the word's meaning in the context sentence?
Reply with ONLY the number.
```

**调用参数：** `max_tokens=10, temperature=0`（确定性输出，仅需一个数字）

**返回：** 0-based 索引，通过 `max(0, min(int(response) - 1, len(senses) - 1))` 计算（自动截断越界值），解析失败时返回 `0`（降级到第一义项，不抛异常）。

**错误处理：** 任何异常（网络、解析）记录 warning 日志，返回 `0`，不影响正常制卡流程。

#### 5. `add_word` 端点逻辑分支

```
http_client = app.state.http_client  # 由 lifespan 初始化的复用连接池
context = req.sentence.strip() if req.sentence else None

if context and LLM_API_KEY:
    # AI 模式
    senses = _extract_senses(ldoce_html)
    if len(senses) > 1:
        sense_idx = await _ai_pick_sense(word, context, senses, http_client)
    else:
        sense_idx = 0
    matched = senses[sense_idx] if senses else None

    definition_field = matched["html"] if matched else ldoce_html
    ldoce_ex         = matched["example"] if matched else ""
    sentence_field   = context + ("\n\n— LDOCE: " + ldoce_ex if ldoce_ex else "")
else:
    # 原有逻辑（向后兼容）
    definition_field = ldoce_html
    sentence_field   = _extract_sentence(ldoce_html)
```

#### 6. Anki 字段映射（重命名后，LDOCE5 对齐）

| Anki 字段名 | AI 模式（有 sentence） | 原有模式（无 sentence） |
|------------|------------------------|------------------------|
| `word` | 单词原形 | 单词原形 |
| `pronunciation` | LDOCE5 IPA | LDOCE5 IPA |
| `definition` | 匹配义项 HTML（单条） | 全部义项 HTML |
| `sentence` | 用户原句 + `— LDOCE: 匹配义项例句` | 第一义项第一例句 |
| `audio` | LDOCE5 英式发音 | LDOCE5 英式发音 |
| `extrainfo` | `""` | `""` |
| `url` | `""` | `""` |

**`sentence` 字段格式示例（AI 模式）：**

```
The ephemeral beauty of cherry blossoms reminded her to cherish the present.

— LDOCE: Fame is ephemeral, but art endures.
```

### Implementation Plan（新增任务）

- [x] Task 10: 更新 `.env.example` — 追加 LLM 配置项
  - File: `.env.example` (modify)
  - Action: 追加 `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL` 三个变量及说明注释
  - Notes: `LLM_API_KEY` 留空默认值，注释说明为空时跳过 AI 逻辑；`LLM_BASE_URL` 默认填 NVIDIA NIM 端点，未来可换其他 OpenAI-compatible 提供商

- [x] Task 11: 更新 `main.py` — 读取 LLM 环境变量 + 重命名 NOTE_TYPE_NAME
  - File: `main.py` (modify)
  - Action: 在配置区（`os.environ.get` 块）追加 LLM 变量，并将 `MODEL_NAME` 改为 `NOTE_TYPE_NAME`：
    ```python
    NOTE_TYPE_NAME = os.environ.get("NOTE_TYPE_NAME", "ODH")   # 原 MODEL_NAME
    LLM_API_KEY  = os.environ.get("LLM_API_KEY", "")
    LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://integrate.api.nvidia.com/v1")
    LLM_MODEL    = os.environ.get("LLM_MODEL", "nvidia/llama-3.1-nemotron-70b-instruct")
    ```
  - Notes: 全局将变量 `MODEL_NAME` 替换为 `NOTE_TYPE_NAME`（包括 `anki_payload` 里以 `NOTE_TYPE_NAME` 为值的地方；注意 `"modelName"` 是 AnkiConnect API 的固定字段名，不变）

- [x] Task 12: 更新 `main.py` — `WordRequest` 加 `sentence` 字段
  - File: `main.py` (modify)
  - Action: `WordRequest` 添加 `sentence: Optional[str] = None`
  - Notes: `Optional` 已在原文件 import，无需新增 import

- [x] Task 13: 更新 `main.py` — 实现 `_extract_senses()`
  - File: `main.py` (modify)
  - Action: 在 `_extract_sentence()` 函数之后插入 `_extract_senses(html)` 函数（见上方逻辑说明）
  - Notes: 使用 `re.split(r'<hr\s*/?>', html)` 分割 POS 块；使用 `[m.start() for m in re.finditer(r'<span[^>]+class="Sense"', block)]` 定位义项边界

- [x] Task 14: 更新 `main.py` — 实现 `_ai_pick_sense()`
  - File: `main.py` (modify)
  - Action: 在 `_extract_senses()` 之后插入 `_ai_pick_sense(word, context, senses, http_client)` async 函数
  - Notes:
    - POST 到 `LLM_BASE_URL + "/chat/completions"`
    - Header: `{"Authorization": f"Bearer {LLM_API_KEY}"}`
    - `max_tokens=10, temperature=0`
    - 解析响应：`int(re.search(r'\d+', content).group()) - 1`
    - 任何异常 log warning，return 0

- [x] Task 15: 更新 `main.py` + Anki — 字段重命名为 LDOCE5 对齐命名
  - Files: `main.py` (modify)、Anki（手动：工具 → 管理笔记类型 → ODH → 字段）
  - Action: 当前字段名继承自 ODH（原为日语词典设计），需同步改为 LDOCE5 自身术语。**API 代码和 Anki 笔记类型字段必须同步修改**，两者保持一致。

    | 当前字段名（ODH 继承） | 目标字段名（LDOCE5 对齐） | LDOCE5 来源 | 说明 |
    |-----------------------|--------------------------|------------|------|
    | `expression` | `word` | 词条标题（headword） | 单词原形 |
    | `reading` | `pronunciation` | `<span class="PRON">` | IPA 音标，如 `/ɪˈfemərəl/` |
    | `glossary` | `definition` | `<span class="DEF">` | 义项 HTML |
    | `sentence` | `sentence` | `<span class="EXAMPLE">` | 用户原句 + LDOCE 例句（保留，因含用户数据） |
    | `audio` | `audio` | `href="sound://GB_*.spx"` | 发音文件，格式不变 |
    | `extrainfo` | `extrainfo` | — | 预留，当前为空 |
    | `url` | `url` | — | 预留，当前为空 |

  - **API 改动（`main.py`）**：
    1. 将 `add_word` 端点中 `anki_payload` 的 `fields` 字典键名从旧名改为新名
    2. 将局部变量 `definition`（`_lookup_word` 的返回值，原始 LDOCE HTML）重命名为 `ldoce_html`，避免与 Anki 字段名 `definition` 产生歧义：
    ```python
    ldoce_html = await loop.run_in_executor(...)  # 原 definition = ...
    ...
    "fields": {
        "word": word,                  # 原 expression
        "pronunciation": reading,       # 原 reading
        "definition": definition_field, # 原 glossary（局部变量也应重命名）
        "sentence": sentence_field,
        "audio": audio_field,
        "extrainfo": "",
        "url": "",
    }
    ```
  - **Anki 改动（手动）**：工具 → 管理笔记类型 → ODH → 字段 → 逐一重命名；然后在卡片模板（正面/背面）里将 `{{expression}}` → `{{word}}`、`{{reading}}` → `{{pronunciation}}`、`{{glossary}}` → `{{definition}}`
  - Notes:
    - **此 Task 必须在 Task 16 之前完成**，因为 Task 16 的 AI 分支代码使用新字段名
    - 将 `add_word` 端点中的局部变量 `reading`（即 `reading = _extract_reading(ldoce_html)`）一并重命名为 `pronunciation`，与 `anki_payload` 字段名保持一致
    - 重命名 Anki 字段后，模板里的 `{{旧字段名}}` 变量会失效，必须同步更新模板
    - 已有卡片的数据不会丢失（Anki 重命名字段时数据随字段一起迁移）
    - `AddWordResponse.definition` 字段（Python 响应模型）仅为 API 响应，与 Anki 字段名无关，可保留

- [x] Task 16: 更新 `main.py` — `add_word` 端点加 AI 分支
  - File: `main.py` (modify)
  - Action: 在提取 `pronunciation`（原 `reading`）之后、构建 `anki_payload` 之前，插入 AI 分支逻辑（见上方伪代码）
  - Notes:
    - 移除现有 `sentence = _extract_sentence(ldoce_html)` 单行，替换为完整分支
    - 使用 Task 15 引入的新局部变量名 `ldoce_html` 和 `definition_field`

- [x] Task 17: 更新 iOS 快捷指令说明
  - File: `anki-iphone-guide.md` (modify)
  - Action: 将快捷指令动作流程替换为方案 A（选句 + 弹窗输入单词），更新注意事项

- [x] Task 18: 更新测试文件
  - File: `tests/test_main.py` (modify)
  - Action: 新增以下测试，并将已有测试中的旧字段名（`expression`、`reading`、`glossary`）更新为新字段名（`word`、`pronunciation`、`definition`）：
    - `test_add_word_with_sentence_ai_mode` — 有 sentence 且 AI 返回正确 sense_idx
    - `test_add_word_ai_fallback_on_error` — AI 调用失败时退化为 sense 0，不 502，并断言产生 warning 日志（AC10）
    - `test_add_word_no_sentence_unchanged` — 无 sentence 时行为与原有一致（AC12）
    - `test_add_word_no_llm_key` — `LLM_API_KEY=""` 时带 `sentence` 仍走旧逻辑，`definition` 为全量 HTML（AC11）

### Acceptance Criteria（新增）

- [x] AC9: Given `LLM_API_KEY` 已配置，when POST `/add-word` body 为 `{"word": "work", "sentence": "She put a lot of work into the project."}`, then:
  - `definition` 仅包含一条义项的 HTML（非全部义项拼接）
  - `sentence` 字段格式为 `{用户原句}\n\n— LDOCE: {匹配义项例句}`（若该义项有例句）
  - HTTP 200

- [x] AC10: Given AI 服务不可达或返回非数字响应，when POST `/add-word` 带 `sentence`，then:
  - 退化使用第一义项（sense_idx=0）
  - 不返回 502，正常完成制卡
  - 日志中有 warning 记录

- [x] AC11: Given `LLM_API_KEY` 为空（未配置），when POST `/add-word` 带 `sentence`，then:
  - 行为与不带 `sentence` 完全一致（跳过 AI 逻辑）
  - HTTP 200

- [x] AC12: Given POST `/add-word` 不带 `sentence`，then:
  - `definition` = 全部义项 HTML（原有行为）
  - `sentence` = 第一义项第一例句（原有行为）
  - HTTP 200

- [x] AC13: Given 目标单词在 LDOCE5 中仅有一个义项（`len(senses) == 1`），when POST `/add-word` 带 `sentence` 且 `LLM_API_KEY` 已配置，then 跳过 LLM 调用，直接使用该唯一义项，HTTP 200

- [x] AC14: Given POST `/add-word` 带 `sentence`（或不带）但单词在 LDOCE5 中不存在，then 返回 404（`detail` 含 "not found"），AI 逻辑不触发

---

## Review Notes

- Adversarial review completed: 12 findings
- Resolution: auto-fix applied to all 12
- Findings fixed:
  - F1: 拆分独立 LLM client（`app.state.llm_client`），与 AnkiConnect client 解耦
  - F2: `_ai_pick_sense` 加 `resp.raise_for_status()`，4xx/5xx 可区分
  - F3: `WordRequest.sentence` 加 `max_length=2000` 限制
  - F4: 添加 prompt injection 风险注释
  - F5: 添加设计意图注释（client 注入 vs 全局配置）
  - F6: `_extract_senses` 在每个 sense HTML 前拼接 POS block header，保留词性上下文
  - F7: `test_add_word_with_sentence_ai_mode` 增加 sentence_field 内容断言
  - F8: `test_add_word_ai_fallback_on_error` 增加 fallback definition 内容断言
  - F9: 新增 `_extract_span_text()` 深度计数器，正确处理 DEF 内嵌套 span
  - F10: 启动日志显示 AI 开关状态；异常日志只记录类型名，不记录完整 exc（防止 key 泄露）
  - F11: 新增 `test_extract_senses_multi_pos` 单元测试覆盖多词性 HTML
  - F12: 新增 `test_add_word_ai_multi_pos_html` 集成测试覆盖 AI 分支在多词性 HTML 上的完整流程
