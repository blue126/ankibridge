# Sprint Change Proposal / Sprint 变更提案

**Date / 日期:** 2026-03-03
**Project / 项目:** iphone-anki-sync
**Prepared by / 准备人:** Will (via Correct Course Workflow)
**Change Scope / 变更范围:** Moderate

---

## Section 1: Issue Summary / 问题摘要

### Problem Statement / 问题陈述

The project goal is to migrate from the current architecture (FastAPI monolith → AnkiConnect → Anki Desktop Docker → syncserver) to a simpler two-service architecture (ldoce5-api + anki-writer → anki Python package → syncserver).

**项目目标**是从当前架构（FastAPI 单体 → AnkiConnect → Anki Desktop Docker → syncserver）迁移到更简洁的双服务架构（ldoce5-api + anki-writer → anki Python 包 → syncserver）。

### Discovery Context / 发现背景

A thorough review of the existing migration document (`docs/migration-ankiconnect-to-anki-package.md`) against the actual codebase (`main.py`, `api-design.md`) revealed **four significant gaps** that, if left unaddressed, would cause implementation delays and rework.

对现有迁移文档与实际代码库的对比审查发现了 **4 个重要遗漏**，若不纠正将导致实施过程中的延误和返工。

### Triggering Evidence / 触发证据

- Existing `main.py` is a **monolithic** service combining dictionary lookup, AI disambiguation, audio handling, and AnkiConnect integration — not easily separated with "small changes"
- Migration doc states `ldoce5-api` changes are "很小" (small), but code review shows two entirely new endpoints and significant structural refactoring are required
- Step numbering gap (Step 6 → Step 8) indicates missing planned work
- Audio extraction logic is UK-only; API design spec requires both UK and US

---

## Section 2: Impact Analysis / 影响分析

### Epic Impact / Epic 影响

The project consists of a single epic: the migration itself. The overall epic remains valid and executable; only the **effort estimation and step completeness** require correction.

项目只有一个 epic（迁移计划本身）。Epic 整体仍然有效，只需修正**工作量估算和步骤完整性**。

| Epic 状态 | 结论 |
|-----------|------|
| 仍可按目标完成？ | ✓ 是 |
| 需要新增/移除 epic？ | 否 |
| 优先级/顺序变化？ | 否 |

### Artifact Conflicts / Artifact 冲突

| Artifact | 冲突类型 | 严重程度 |
|----------|---------|---------|
| `docs/migration-ankiconnect-to-anki-package.md` | ldoce5-api 改动范围描述不准确 | 高 |
| `docs/migration-ankiconnect-to-anki-package.md` | Step 5 未拆分（代码编写工作量低估） | 高 |
| `docs/migration-ankiconnect-to-anki-package.md` | 部署步骤遗漏 main.py + .env 清理 | 中 |
| `docs/migration-ankiconnect-to-anki-package.md` | 文件结构未定义 | 中 |
| `docs/migration-ankiconnect-to-anki-package.md` | 音频提取逻辑扩展未提及 | 中 |
| `requirements.txt` | 缺少 `anki==25.9.2` | 中 |

### Technical Impact / 技术影响

- **`main.py` (ldoce5-api):** Requires moderate refactoring, not minimal changes. Two new endpoints (`GET /lookup`, `GET /audio/{filename}`), removal of AnkiConnect integration, health endpoint update, and audio extraction logic enhancement.
- **`anki_writer.py`:** New file from scratch — implementation guide in migration doc is accurate and complete.
- **`requirements.txt`:** Needs one new dependency.
- **No infrastructure changes** beyond what's already documented (systemd, docker-compose).

---

## Section 3: Recommended Approach / 推荐方案

**Selected Path: Direct Adjustment (Option 1)**

路径 B（直接实施完整迁移）已确认，通过直接调整迁移计划来解决发现的遗漏。

### Rationale / 理由

| 评估维度 | 评估结果 |
|---------|---------|
| 技术可行性 | ✓ 高（anki 包已验证无 Qt 依赖，架构方向清晰） |
| 实施风险 | 低（并行运行策略，零停机切换） |
| 时间线影响 | 轻微延长（ldoce5-api 工作量约为文档预估的 2-3 倍） |
| 团队影响 | 无（个人项目） |

### Effort Estimate Correction / 工作量估算修正

| 工作项 | 原文档估计 | 修正后估计 |
|--------|-----------|-----------|
| `ldoce5-api` 重构 (`main.py`) | 小 | **中**（新增 2 个端点，重构现有逻辑，音频提取扩展） |
| `anki_writer.py` 新建 | 中 | 中（文档实现参考准确） |
| `requirements.txt` 更新 | 未提及 | 小 |
| 部署 / systemd | 小 | 小 |
| 验证与切换 | 小 | 小 |

---

## Section 4: Detailed Change Proposals / 详细变更提案

### Change Proposal 1 — ldoce5-api 改动范围修正

**Artifact:** `docs/migration-ankiconnect-to-anki-package.md`
**Section:** "ldoce5-api 的 main.py 改动"

**OLD:**
```
ldoce5-api 的 main.py 改动很小——只需移除 AnkiConnect 相关代码，
/add-word 端点整体迁移到 anki_writer.py
```

**NEW:**
```
ldoce5-api 的 main.py 需要中等程度重构：
(a) 新增 GET /lookup 端点（暴露 senses 列表 + audio URLs）
(b) 新增 GET /audio/{filename} 端点（返回 mp3 流）
(c) 删除 POST /add-word 端点（迁移到 anki_writer.py）
(d) lifespan 中删除 AnkiConnect http_client（retries=1）
(e) /health 响应从 anki_connected 改为 llm_enabled
(f) _extract_senses() 中 html 字段重命名为 definition_html
```

**Rationale:** Code review of existing `main.py` (684 lines) confirms it is a full monolith. The `/lookup` and `/audio/{filename}` endpoints do not currently exist and must be built from scratch.

---

### Change Proposal 2 — requirements.txt 添加 anki 依赖

**Artifact:** `requirements.txt` + `docs/migration-ankiconnect-to-anki-package.md`

**OLD (`requirements.txt`):** No `anki` package listed
**NEW (`requirements.txt`):** Add `anki==25.9.2`

**OLD (migration doc Step 1):** Implies installing into venv separately, no mention of requirements.txt

**NEW (migration doc):**
```
Step 1: 安装 anki 包并更新 requirements.txt
  pip install anki==25.9.2
  # 同时将 anki==25.9.2 添加到 requirements.txt
  # 注：anki 包仅由 anki-writer 使用，ldoce5-api 不 import 它
```

**Rationale:** Keeping dependencies in `requirements.txt` ensures reproducibility. The two services share one venv (acceptable for a personal tool with no dependency conflicts).

---

### Change Proposal 3 — 迁移步骤编号修正 + 文件结构定义

**Artifact:** `docs/migration-ankiconnect-to-anki-package.md`

**核心说明：** 原 Step 5（"编写代码"）将 anki_writer.py 和 ldoce5-api 的重构合并为一步，严重低估了工作量。将其**拆分为两个独立步骤（Step 5 + Step 6）**，原 Step 6 及后续步骤全部顺延 +1。

**OLD (Step 5–8):**
```
Step 5: 编写代码
  - 编写 anki_writer.py
  - ldoce5-api 的 main.py 只需删除 AnkiConnect 相关代码

Step 6: 部署代码并配置 systemd service

（Step 7 缺失）

Step 8: 验证新服务（并行运行期间）
```

**NEW (Step 5–9):**
```
Step 5: 编写 anki_writer.py（新文件，port 5051）
  - 按迁移文档实现要点实现 /add-word 和 /health 端点

Step 6: 重构 ldoce5-api（main.py）
  ├── 新增 GET /lookup 端点
  ├── 新增 GET /audio/{filename} 端点
  ├── 删除 POST /add-word 端点
  ├── lifespan 删除 AnkiConnect http_client
  ├── /health 响应更新（anki_connected → llm_enabled）
  └── _extract_senses() html 字段重命名为 definition_html

Step 7: 部署代码并配置 systemd service（原 Step 6）

Step 8: 验证新服务（并行运行期间）（原 Step 8，编号不变）

Step 9: 下线 Anki Desktop（原 Step 9）
Step 10: 更新 iPhone Shortcuts（原 Step 10）
Step 11: 最终验证（原 Step 11）
```

**NEW (file structure section to add):**
```
服务文件结构（宿主机 /opt/iphone-anki-sync/）：
  main.py          ← ldoce5-api（重构后，port 5050）
  anki_writer.py   ← anki-writer（新建，port 5051）
  requirements.txt ← 两个服务共用（含 anki==25.9.2）
  .env             ← 含新增变量（见 Change Proposal 5）
```

**Rationale:** Splitting Step 5 into two steps makes both work items visible and independently verifiable. File structure clarity reduces deployment ambiguity.

---

### Change Proposal 4 — 音频提取逻辑扩展

**Artifact:** `docs/migration-ankiconnect-to-anki-package.md`
**Section:** "Step 7：重构 ldoce5-api 实现要点"（新增小节）

**Problem:** Current `_extract_audio_filename()` only extracts UK (GB_ prefix) audio. The `GET /lookup` endpoint per `api-design.md` must return both `audio.uk` and `audio.us`.

**NEW (add to migration doc):**
```
音频提取逻辑扩展：
_extract_audio_filename() 需拆分为 _extract_audio_filenames(html)，
返回 {"uk": "GB_xxx.spx", "us": "US_xxx.spx"}（各自可为 None）。

LDOCE5 HTML 中 UK/US 音频的区分方式：
  UK: href="sound://GB_xxx.spx"
  US: href="sound://US_xxx.spx"

/lookup 响应中 audio.*.url 格式：
  "/audio/GB_xxx.mp3"（相对路径，由 ldoce5-api 的 /audio 端点提供）
  注：filename 字段保留原始 .spx 名，url 字段使用转换后的 .mp3 名
```

**Rationale:** Without this fix, `GET /lookup` cannot satisfy the `api-design.md` spec, and `anki-writer`'s UK-preferred audio fallback logic will fail silently when UK audio is unavailable.

---

### Change Proposal 5 — 部署步骤补全：main.py scp + .env 清理

**Artifact:** `docs/migration-ankiconnect-to-anki-package.md`
**Section:** Step 7（重构后的部署步骤，原 Step 6）

**问题 A：部署只提到 anki_writer.py，遗漏 main.py**

**OLD:**
```bash
scp anki_writer.py root@192.168.1.100:/opt/iphone-anki-sync/
```

**NEW:**
```bash
# 同时部署两个文件：新服务 + 重构后的 ldoce5-api
scp anki_writer.py main.py root@192.168.1.100:/opt/iphone-anki-sync/
```

**问题 B：.env 更新只追加，未移除已废弃变量**

**OLD (migration doc):**
```bash
cat >> /opt/iphone-anki-sync/.env << 'EOF'
COLLECTION_PATH=...
ANKI_SYNC_URL=...
...
EOF
# （ANKI_CONNECT_URL 仍残留在 .env 中）
```

**NEW:**
```bash
# 追加新变量
cat >> /opt/iphone-anki-sync/.env << 'EOF'
COLLECTION_PATH=/opt/iphone-anki-sync/collection.anki2
ANKI_SYNC_URL=http://localhost:27701/
ANKI_SYNC_USER=anki
ANKI_SYNC_PASSWORD=anki
LDOCE5_API_URL=http://localhost:5050
API_PORT=5051
EOF

# 移除已废弃的 ANKI_CONNECT_URL（ldoce5-api 重构后不再使用）
sed -i '/^ANKI_CONNECT_URL=/d' /opt/iphone-anki-sync/.env
```

**Rationale:** Deploying only `anki_writer.py` while leaving the old `main.py` would break `/lookup` and `/audio` endpoints. Leaving `ANKI_CONNECT_URL` in `.env` creates confusion about whether AnkiConnect is still required.

---

## Section 5: Implementation Handoff / 实施交接

### Change Scope Classification / 变更范围分类

**Moderate** — Requires updating planning documents before implementation begins. Core architecture direction unchanged; work scope clarified.

### Handoff Plan / 交接计划

| 角色 | 责任 |
|------|------|
| **开发者 (Will)** | 按修正后的迁移步骤实施所有代码变更 |
| **无需其他干系人** | 个人项目，单人决策 |

### Implementation Order / 实施顺序

1. **立即执行（Pre-coding）：** 按本提案更新 `docs/migration-ankiconnect-to-anki-package.md`
2. **Step 1-4：** 环境准备（安装 anki 包 + 更新 requirements.txt、验证、初始化 collection）
3. **Step 5：** 编写 `anki_writer.py`（新文件）
4. **Step 6：** 重构 `main.py` → `ldoce5-api`（新增 /lookup、/audio 端点等）
5. **Step 7：** 部署（scp `anki_writer.py` + `main.py`，更新 `.env`，配置 systemd）
6. **Step 8-11：** 验证、下线 Anki Desktop、更新 Shortcuts、最终验证

### Success Criteria / 成功标准

- [ ] `GET /lookup` 返回正确的 senses 列表、UK/US audio URLs、selected_sense_index
- [ ] `GET /audio/{filename}` 返回 mp3 流
- [ ] `POST /add-word` (anki-writer) 成功写入 collection 并触发 sync
- [ ] AnkiMobile 能收到新卡片
- [ ] Anki Desktop Docker 容器成功下线
- [ ] iPhone Shortcuts 端口切换至 5051 后端到端验证通过

---

## Checklist Summary / 检查清单完成状态

| 章节 | 状态 |
|------|------|
| 1. 理解触发背景 | ✅ Done |
| 2. Epic 影响评估 | ✅ Done |
| 3. Artifact 冲突分析 | ✅ Done |
| 4. 前进路径评估 | ✅ Done |
| 5. Sprint Change Proposal 组件 | ✅ Done |
| 6. 最终审阅与交接 | ✅ Done — Approved 2026-03-03 |

---

*Self-review completed 2026-03-03. Corrected: step numbering inconsistency, added Change Proposal 5 (deployment gaps), updated Artifact Conflicts table and Implementation Order.*
