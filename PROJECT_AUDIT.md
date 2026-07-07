# Tidal Echo 项目体检报告

> 日期：2026-07-07  
> 范围：全仓库（backend/ web/ examples/ channel/ 根目录脚本）  
> 目的：识别重复实现、废弃代码、多源状态、历史遗留逻辑，给出安全的清理和重构建议

---

## 一、项目规模概览

| 模块 | 核心文件 | 行数 | 职责 |
|---|---|---|---|
| Relay 后端 | `backend/app.py` | ~1278 | FastAPI 消息中继、SSE 扇出、鉴权、模型管理、内存库 |
| 前端 PWA | `web/index.html` | ~7327 | 单文件 SPA，聊天 UI + 模型选择 + 主题 + 会话管理 |
| Service Worker | `web/sw.js` | ~95 | 离线缓存 + Web Push |
| LLM Bridge | `examples/bridge_any_llm.py` | ~530 | 标准库实现的 AI 侧，接任意 OpenRouter 模型 |
| API Loop | `examples/api_loop.py` | ~490 | FastAPI 版 AI 侧（第三方依赖，当前未使用） |
| Channel 插件 | `channel/server.ts` | ~454 | Claude Code 专用 MCP channel（当前未使用） |

辅助文件：`memory_service.py`（222 行）、`memory_provider_local.py`（30 行）、`persona.md`、`relationship_summary.md`、`memory_policy.md`、`confirm_dev_channel_win.py` 等。

启动脚本 8 个：`start-local-backend.cmd/ps1`、`start-local-web.cmd/ps1`、`start-local-web-python.cmd`、`start-local-ai.ps1`、`start_relay.bat`、`restart_bridge.ps1`。

开发服务器 2 个：`dev-server.mjs`（Node.js）、`dev_server.py`（Python），功能完全相同。

---

## 二、重复实现与多源状态

### 2.1 模型列表：4 份独立维护的清单

| 列表 | 位置 | 数量 | 状态 |
|---|---|---|---|
| `MODEL_CHOICES` | `web/index.html` L4425 | 13 个 | **当前使用**（PWA 模型面板） |
| `KNOWN_MODELS` | `examples/bridge_any_llm.py` L135 | 13 个 | **当前使用**（启动探测） |
| `MODEL_OPTIONS` | `backend/app.py` L98 | 5 个 | **过时**（含 cohere、claude-sonnet-5、claude-fable） |
| `modelQuickSelect` HTML | `web/index.html` L3116 | 5 个 | **死代码**（CSS 隐藏，已被 MODEL_CHOICES 替代） |

**问题**：后端 `GET /app/model` 仍然返回过时的 5 模型列表，前端虽然不依赖它（前端有自己的 MODEL_CHOICES），但这个端点的响应数据是误导性的。`KNOWN_MODELS` 和 `MODEL_CHOICES` 目前内容一致但各自独立维护，加模型时需要同步改两处。

**建议**：
- 删除 `modelQuickSelect` 的 HTML 和相关 JS 引用
- 后端 `MODEL_OPTIONS` 要么与前端同步为 13 个，要么删除（前端不依赖它）
- 考虑将模型列表统一到一处（如 JSON 配置文件），三端共享

### 2.2 模型可用性检查：3 套机制并存

| 机制 | 位置 | 数据来源 | TTL |
|---|---|---|---|
| Relay 主动探测 | `backend/app.py` `/app/model/check` | 从 Zeabur 服务器 ping OpenRouter | 60 秒内存缓存 |
| Bridge 上报 | `backend/app.py` `/app/model/report` + `/app/model/bridge-status` | Bridge 实测（区域准确） | 内存，重启丢失 |
| 前端双层缓存 | `web/index.html` `_fetchBridgeStatus()` + `checkModelStatus()` | 先读 bridge 状态，再 fallback 到 relay 检查 | 120 秒 / 60 秒 |

**问题**：Relay 主动探测（从 Zeabur 海外服务器）和 Bridge 实测（从国内网络）结果经常不一致——这正是之前 Claude 显示 available 但实际 403 的根因。现在 bridge 上报机制已经工作，relay 主动探测变成了死代码路径。

**建议**：
- 保留 bridge 上报作为唯一权威来源
- 前端 `checkModelStatus` 中 `/app/model/check` 的 fallback 可以保留作为兜底，但应降低优先级或标记为 "relay-side, may be inaccurate"
- Relay 的 `/app/model/check` 端点可以考虑废弃

### 2.3 Fallback 错误码：2 处相同定义

`FALLBACK_CODES = {401, 403, 404, 408, 409, 429, 500, 502, 503, 504}` 在 `bridge_any_llm.py`（L132）和 `api_loop.py`（L62）各定义一次，内容完全一致。

### 2.4 .env 加载器：2 个不同实现

`_load_dotenv()`（bridge，L46）处理 BOM 字符；`load_dotenv()`（api_loop，L36）处理引号。同一个工具函数两种实现。

### 2.5 模型选择 UI：4 套并存

| UI 机制 | 位置 | 状态 |
|---|---|---|
| `<select id="modelQuickSelect">` | HTML L3116 | **死代码**（CSS 隐藏） |
| `.model-pill` 按钮 | JS L2260 | **当前使用**（打开模型面板） |
| `#modelSheet` 底部面板 | HTML L3120 | **当前使用**（13 模型列表） |
| `#modelSeg` 分段控件 | JS L4621 | **死代码**（无对应 HTML 元素） |

`setModelUi()` 函数（L4568）试图同步所有 4 套机制，其中 2 套操作的是 null 元素。

---

## 三、废弃代码与死代码

### 3.1 前端：约 20 个 DOM 引用指向 null

以下 JS 变量查找了不存在的 HTML 元素，所有相关代码都是死代码：

| 变量 | 用途 | 涉及行数 |
|---|---|---|
| `modelSeg` | 模型分段控件 | ~10 行 |
| `modelQuickInput` | 模型文本输入 | ~15 行 |
| `modelQuickSave` | 模型保存按钮 | ~5 行 |
| `modelHint` | 模型提示文本 | ~5 行 |
| `effortSeg` | 推理力度控件 | ~5 行 |
| `contextSlider` | 上下文滑块 | ~10 行 |
| `brainSeg`, `brainHint` | AI 大脑切换 | ~40 行 |
| `resetRow`, `swapRow`, `resumeRow` | 上下文管理按钮 | ~25 行 |
| `statusCopy`, `statusSid`, `statusUsed` | 状态显示 | ~30 行 |
| `sessionBtn` | 会话按钮 | ~5 行 |
| `pushSeg`, `pushHint` | 推送通知 UI | ~100 行 |

这些元素来自一个早期的"设置/档案页"设计，后来被 V2 Hub 设计替代，但旧代码从未清理。

### 3.2 前端：V2 皮肤覆盖导致约 1500 行 CSS 失效

`web/index.html` 的 "UI V2 companion skin"（L1992-L2413）用 `:root` 变量覆盖了原始主题（L68-L1991）的几乎所有视觉属性。结果：

- 原始 Pearl Tide 主题（L68-L127）→ **全部被覆盖，死 CSS**
- Lamplight 主题（L129-L159）→ **大部分被覆盖**
- 只有 Midnight 主题通过 JS 动态注入能部分生效

### 3.3 前端：Midnight 主题定义了 3 次

1. `<head>` 里的 JS 字符串（L29）—— 防闪烁注入
2. `:root[data-theme="midnight"]` CSS 规则（L160-L173）—— 正常 CSS
3. `setTheme()` 函数里的 JS 字符串（L3645）—— 与 #1 重复

三处必须手动同步。

### 3.4 前端：占位函数

- `apiContextStatus()`（L4751）：返回硬编码假数据 `{ok:true, usage_tokens:96000, ...}`
- `apiContextAction()`（L4758）：返回 `{ok:true}`
- 整个上下文管理 UI 操作不存在的 DOM 和假 API

### 3.5 后端：未使用的依赖

`requirements.txt` 中 `httpx==0.28.1` 从未被 import。所有 HTTP 调用用的是标准库 `urllib.request`。

### 3.6 后端：跨模块文件读取

`app.py` 在模块加载时（L1061-L1069）读取 `examples/.env` 来提取 `LLM_API_KEY`。这是一个隐式的跨模块依赖，如果 `examples/` 目录不存在（如 Docker 构建不含它），会静默 fallback 到环境变量。

---

## 四、可安全删除的内容

以下是风险极低、可以直接删除的项目：

| # | 内容 | 位置 | 理由 |
|---|---|---|---|
| 1 | `modelQuickSelect` HTML 元素 | `web/index.html` ~L3116 | CSS 已隐藏，被 MODEL_CHOICES 面板完全替代 |
| 2 | `modelQuickSelect` 相关 JS 引用 | `web/index.html` L4580-4582, L4630-4632 | 操作 null 元素 |
| 3 | `modelSeg` 相关 JS | L4570-4575, L4621-4625 | 无对应 HTML |
| 4 | `modelQuickInput` / `modelQuickSave` / `modelHint` 相关 JS | L4579, L4601, L4627-4628, L4632-4645 | 无对应 HTML |
| 5 | `effortSeg` 相关 JS | L4423 | 无对应 HTML |
| 6 | `contextSlider` 相关 JS | L4672-4676 | 无对应 HTML |
| 7 | `brainSeg` / `brainHint` 相关 JS | L4678-4717 | 无对应 HTML |
| 8 | `resetRow` / `swapRow` / `resumeRow` 相关 JS | L4763-4788 | 无对应 HTML |
| 9 | `statusCopy` / `statusSid` / `statusUsed` 相关 JS | L4729-4803 | 无对应 HTML |
| 10 | `sessionBtn` 相关 JS | L5011-5015 | 无对应 HTML |
| 11 | `pushSeg` / `pushHint` 及推送通知 UI 代码 | L5246-5357 | 无对应 HTML（~100 行） |
| 12 | `apiContextStatus()` / `apiContextAction()` | L4751-4770 | 返回假数据的占位函数 |
| 13 | `applyContextStatus()` / `refreshContextStatus()` | 相关函数 | 操作不存在的 DOM |
| 14 | `start_relay.bat` | 根目录 | **含硬编码的生产密钥**，安全风险 |
| 15 | `bridge_err.log` / `bridge_out.log` | 根目录 | 运行时日志，不应提交 |
| 16 | `sse_test.txt` | 根目录 | 手动测试产物 |
| 17 | `httpx` from `requirements.txt` | `backend/requirements.txt` | 从未使用 |
| 18 | `examples/.bridge-state/` 空目录 | examples/ | bridge 实际用的是 `~/.companion-bridge` |

**预计可删除行数**：前端约 300-400 行 JS + 1500 行死 CSS；后端约 5 行；根目录 2-3 个文件。

---

## 五、建议重构的模块

### 5.1 前端 CSS 去层（优先级：高）

**现状**：V2 皮肤覆盖了原始主题，导致大量死 CSS。Midnight 主题定义 3 次。  
**建议**：
- 将 V2 皮肤变量合并到 `:root`，删除被覆盖的原始主题代码
- Midnight 主题只保留一处（CSS 规则），JS 注入改为 toggle class
- 预期减少 ~1500 行 CSS

### 5.2 模型列表统一（优先级：高）

**现状**：4 份模型列表分散在 3 个模块。  
**建议**：
- 创建一个 `models.json` 配置文件，作为单一数据源
- 前端 `MODEL_CHOICES`、bridge `KNOWN_MODELS`、后端 `MODEL_OPTIONS` 都从它读取
- 或者至少在文档中明确指定一处为"主列表"，其他地方引用

### 5.3 模型状态检查统一（优先级：中）

**现状**：3 套机制（relay 探测、bridge 上报、前端双层缓存）。  
**建议**：
- Bridge 上报作为唯一权威来源
- 前端只读 bridge 状态，移除 `/app/model/check` fallback
- 后端 `/app/model/check` 端点标记为 deprecated 或删除

### 5.4 启动脚本整合（优先级：中）

**现状**：8 个启动脚本，3 种方式启动 web 开发服务器，`start_relay.bat` 含硬编码密钥。  
**建议**：
- 删除 `start_relay.bat`（安全风险）
- 保留 `restart_bridge.ps1`（唯一含代理注入的）
- 合并 web 开发服务器的 3 个脚本为 1 个（自动检测 Node/Python）
- 或保留 `start-local-backend.ps1` + `start-local-web.ps1` + `restart_bridge.ps1` 三个核心脚本

### 5.5 api_loop.py 决策（优先级：低）

**现状**：490 行的完整 FastAPI 替代 AI 侧，当前未使用。  
**建议**：
- 如果确定只用 bridge，移到 `examples/unused/` 或删除
- 如果保留作为"高级选项"，在 README 中明确标注为可选

### 5.6 前端模块化（优先级：低，工作量大）

**现状**：7300 行单文件，无构建步骤，无类型检查。  
**建议**：如果项目继续发展，考虑拆分为模块（可以用 ES modules + 简单构建）。但这是大工程，当前不急。

---

## 六、安全风险（需立即关注）

| 风险 | 位置 | 严重度 |
|---|---|---|
| `start_relay.bat` 含硬编码生产密钥（RELAY_SECRET + OpenRouter API Key） | 根目录，已提交 git | **高** |
| `examples/.env` 含真实 API 密钥，可能已被提交 | examples/ | **高** |
| SSE 端点接受 `?token=<SECRET>` 查询参数 | `backend/app.py` | **中**（已知权衡） |
| 无速率限制 | 所有端点 | **低**（单用户设计） |

**建议**：将 `start_relay.bat` 加入 `.gitignore` 并从 git 历史中移除。确认 `examples/.env` 是否在 git 跟踪中，如是则移除。

---

## 七、总结与执行优先级

### 第一阶段：安全清理（立即可做，无风险）
1. 删除 `start_relay.bat`（含硬编码密钥）
2. 将运行时日志加入 `.gitignore`
3. 确认并清理 git 中的敏感文件

### 第二阶段：死代码清除（低风险，预计减少 ~2000 行）
4. 删除前端 ~20 个 null DOM 引用及相关 JS
5. 删除 `modelQuickSelect` HTML 和 JS
6. 删除占位函数（apiContextStatus 等）
7. 清理 V2 皮肤覆盖的死 CSS
8. 移除未使用的 `httpx` 依赖

### 第三阶段：状态统一（中等风险，需要测试）
9. 统一模型列表为单一数据源
10. 统一模型状态检查为 bridge 上报优先
11. 整合启动脚本

### 第四阶段：架构优化（可选，长期）
12. Midnight 主题去重复
13. api_loop.py 决策
14. 前端模块化评估
