# Changelog

所有重要版本变更记录。格式基于 [Keep a Changelog](https://keepachangelog.com/)。

回退到任意版本: `git checkout v0.x.0`（查看）或 `git reset --hard v0.x.0`（强制回退）。

---

## [v0.8.0] — 2026-07-08 单线程聊天（移除 session/history UI）

### 变更
- **移除 session 弹窗**: 头部下拉的窗口切换器（实际从未被触发过，死代码）
- **移除 history 面板**: 全屏历史浏览覆盖层及其 CSS
- **移除 "新对话" 按钮**: 头部 + 按钮不再需要
- **菜单精简**: 去掉 History 入口，保留 Chat / Memory Bank / Settings

### 简化
- `loadSessions()` 自动选择活跃 session 或创建新的，无需用户交互
- 上滑加载旧消息（无限滚动）已有完整实现，无需额外操作

### 清理
- 删除约 200 行 JS 死代码: renderSessionList, setSessionPopover, createNewSession, renameSession, activateSession, openHistoryPanel, closeHistoryPanel, renderSessionHistory 及相关事件监听
- 后端 session 基础设施保留（消息标记用）

---

## [v0.7.0] — 2026-07-07 跨设备同步 + dev/prod 开发流程

### 新增
- **跨设备消息同步**: AI 回复现在携带 `api_session`，手机和电脑看到一致的对话记录
- **Session 删除接口**: `DELETE /app/sessions/{id}` 端点，可清理多余会话
- **模型状态心跳**: Bridge 每 5 分钟重新上报模型可用性，relay 重启后不会一直显示 unavailable
- **Bridge `.env.local` 支持**: 本地开发用 `.env.local` 覆盖 `.env`（连本地 relay 不影响生产配置）
- **日志级别控制**: Bridge `LOG_LEVEL`（debug/info/warn/error）、Relay `RELAY_LOG_LEVEL` 环境变量
- **dev_server.py 环境变量化**: `RELAY_ORIGIN`、`DEV_PORT` 替代硬编码值
- **`.env.local.example` 模板**: 本地开发配置参考文件

### 修复
- 代理端口冲突: `.env` 里的代理强制覆盖系统代理（解决 OpenRouter 区域封锁问题）
- `plugin_payload` 不再丢弃 `api_session` 字段
- Bridge unreachable 时模型状态显示灰色 "—" 而非红色 "Unavailable"

### 维护
- sw.js AI_NAME 防漂移注释（两个文件互相提醒同步）
- `.gitignore` 新增 `.env.local`、`HANDOFF.md` 排除

---

## [v0.6.0] — 2026-07-06 代码清理 + UI 打磨

### 新增
- 项目审计报告: 代码质量分析和清理计划
- `restart_bridge.ps1`: 带代理环境变量的 Bridge 重启脚本

### 改进
- **Phase 1**: 移除 `start_relay.bat`，加固 `.gitignore`
- **Phase 2**: 前后端死代码清理
- **Phase 3**: 模型状态统一 — bridge 作为唯一权威来源
- **Phase 4**: 启动脚本整合，归档 `api_loop.py`
- **V2 皮肤**: CSS 变量合并到 `:root`，移除重复的暗色定义
- **模型选择器打磨**: 滚动条隐藏、浅米色卡片（#FDFCF9）、关闭按钮移至左上角

---

## [v0.5.0] — 2026-07-05 模型管理

### 新增
- **Bridge 模型探测**: 启动时测试所有可用模型，上报真实可用状态
- **模型状态显示**: 前端展示 Available / Unavailable / Checking 状态标签
- Bridge 探测失败自动重试，等 relay 就绪后再上报

### 改进
- Peerpill 名称头部、通话按钮、AI 重命名为「沈洛」
- 模型选择器重设计: 白色卡片 + 米色背景
- 模型检查支持 `BRIDGE_OPENROUTER_KEY` 环境变量
- Session 过滤: AI 回复无 `api_session` 时也能正确显示

### 修复
- 模型选择器 CSS 在不同视口宽度下样式统一
- 后端模型检查读取 bridge API key 作为兜底

---

## [v0.4.0] — 2026-07-03 稳定性修复

### 修复
- 移除导致 JS SyntaxError 的残留代码（修复所有按钮失效）
- 隐藏消息时间戳
- Service Worker 每次加载强制更新，防止旧缓存
- `index.html` / `sw.js` 添加 no-cache 响应头
- SW 子资源 fetch 添加 catch() 兜底，防止 unhandled rejection

---

## [v0.3.0] — 2026-07-02 前端功能

### 新增
- **暗色主题 (Midnight)**: 外观切换开关 + 历史面板
- **原生会话管理**: 不依赖 loop 服务的 session 系统
- 新对话按钮、斜体渲染修复
- Bridge 从 relay 读取模型配置

### 修复
- 中文乱码修复、模型选择器升级

---

## [v0.2.0] — 2026-07-01 多模型 Bridge + 部署

### 新增
- **`bridge_any_llm.py`**: 接任意 OpenAI 兼容 LLM（GPT/DeepSeek/Gemini/GLM/Kimi/通义/本地 vLLM）
- **AGENTS.md**: AI 部署 SOP + 决策树 + 避雷点清单
- **DevChannel 自动确认**: Windows 下 CC 启动后自动按回车跳过确认框
- Zeabur 部署支持
- API Loop 开放部署路径

### 修复
- Bridge 首次运行不回放历史消息
- 近期上下文窗口、Python 3.7+ 兼容

---

## [v0.1.0] — 2026-06-30 初始版本

### 首次发布
- Relay 后端 (FastAPI + SQLite): 消息落库、SSE 实时推送、Bearer 鉴权
- PWA 前端: 手机端聊天界面，可安装到主屏
- Claude Code Channel 插件: CC 作为 AI 大脑直连
- Web Push 推送通知
- 附件上传（图片）

---

[v0.8.0]: https://github.com/Fez614/Tidal_Echo/compare/v0.7.0...v0.8.0
[v0.7.0]: https://github.com/Fez614/Tidal_Echo/compare/v0.6.0...v0.7.0
[v0.6.0]: https://github.com/Fez614/Tidal_Echo/compare/v0.5.0...v0.6.0
[v0.5.0]: https://github.com/Fez614/Tidal_Echo/compare/v0.4.0...v0.5.0
[v0.4.0]: https://github.com/Fez614/Tidal_Echo/compare/v0.3.0...v0.4.0
[v0.3.0]: https://github.com/Fez614/Tidal_Echo/compare/v0.2.0...v0.3.0
[v0.2.0]: https://github.com/Fez614/Tidal_Echo/compare/v0.1.0...v0.2.0
[v0.1.0]: https://github.com/Fez614/Tidal_Echo/releases/tag/v0.1.0
