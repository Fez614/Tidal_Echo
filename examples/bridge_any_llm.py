#!/usr/bin/env python3
"""
bridge_any_llm.py — 把「任意 LLM API」接到 companion relay 的 AI 侧 bridge。

这是 channel/ 插件(Claude Code 专用)的通用替代品:不依赖 Claude Code,
用任何 OpenAI 兼容的模型(GPT / DeepSeek / Gemini / GLM / Kimi / 通义 / 本地
vLLM …)当「AI 大脑」。前端 PWA 和 relay 后端原样不动。

它是个「带工具的聊天」循环,不是会自己乱跑的自主 agent —— 只在收到人类
消息时动一次:

    ① SSE 长连  GET  {RELAY}/channel/in?since={cursor}   收人类消息(实时)
    ② 用内存维护的近期对话 + persona(system),调你的模型(OpenAI 格式)
    ③ POST       {RELAY}/channel/out  {"type":"reply","text":...}   回复回手机

首次启动会拉一次历史做「暖启动」上下文,并把游标设到当前最新一条 —— 所以
**不会回放/重答你过去的旧消息**,只应答启动之后的新消息。重启则从上次游标
继续,补答断线期间漏掉的。

零第三方依赖(只用 Python 标准库,3.7+)。配置全走环境变量,可放在同目录
.env(见 .env.example)。跑起来:

    cp .env.example .env   &&   # 填好 RELAY_URL / RELAY_SECRET / LLM_* 三件
    python3 bridge_any_llm.py

⚠️ 单身体原则:同一时刻只跑一个 AI 侧。别同时开着 Claude Code channel 和这个
   bridge —— 两个都会收到同一条消息、都会回复,用户会看到双重回复。
"""

from __future__ import annotations  # 让类型注解不在运行时求值,兼容 Python 3.7+

import base64
import collections
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------------------
# 配置(环境变量;也读同目录 .env,开发时 .env.local 覆盖)
# ---------------------------------------------------------------------------

_BRIDGE_DIR = Path(__file__).resolve().parent

def _load_dotenv(path: Path, override: bool = False) -> dict:
    """极简 .env 加载。override=True 时强制覆盖;否则 setdefault。返回加载的 kv。"""
    loaded = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.lstrip("\ufeff").strip()
            v = v.strip()
            loaded[k] = v
            if override:
                os.environ[k] = v
            else:
                os.environ.setdefault(k, v)
    except FileNotFoundError:
        pass
    return loaded

# 加载顺序:.env 先装(最低优先) → .env.local 覆盖(本地开发) → 代理变量强制生效
_env_base = _load_dotenv(_BRIDGE_DIR / ".env")
_env_local = _load_dotenv(_BRIDGE_DIR / ".env.local", override=True)

# 代理变量必须从 .env 文件生效(系统代理的出口 IP 可能被 OpenRouter 区域封锁)
_env_merged = {**_env_base, **_env_local}
for _pk in ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY"):
    for _variant in (_pk, _pk.lower()):
        if _variant in _env_merged:
            os.environ[_variant] = _env_merged[_variant]

# memory.py reads config from os.environ at import time — must be imported AFTER .env loading
import memory

# 日志级别: debug / info(默认) / warn / error
_LOG_LEVELS = {"debug": 0, "info": 1, "warn": 2, "error": 3}
LOG_LEVEL = _LOG_LEVELS.get(os.environ.get("LOG_LEVEL", "info").lower(), 1)

RELAY_URL = os.environ.get("RELAY_URL", "").rstrip("/")          # 你的域名 + nginx /relay 前缀
SECRET    = os.environ.get("RELAY_SECRET", "")                   # 必须和后端 relay.env 一致
CHAT_ID   = os.environ.get("RELAY_CHAT_ID", "me")               # 单用户通道,固定 "me"
HISTORY_N = int(os.environ.get("HISTORY_N", "12"))             # 喂给模型的最近对话「轮」数
TEMPERATURE = float(os.environ.get("LLM_TEMPERATURE", "0.7"))
HTTP_TIMEOUT = int(os.environ.get("LLM_TIMEOUT", "120"))
OPENROUTER_REFERER = os.environ.get("OPENROUTER_HTTP_REFERER", "http://127.0.0.1:4174")
OPENROUTER_TITLE = os.environ.get("OPENROUTER_TITLE", "Tidal Echo Local")
MODEL_CONFIG_FILE = os.environ.get("MODEL_CONFIG_FILE", "").strip()
RELATIONSHIP_FILE = os.environ.get("RELATIONSHIP_FILE", "").strip()
MEMORY_BANK_FILE = os.environ.get("MEMORY_BANK_FILE", "").strip()

# persona = 模型的人设(system prompt)。从 PERSONA 文本或 PERSONA_FILE 文件读。
PERSONA = os.environ.get("PERSONA", "").strip()
_persona_file = os.environ.get("PERSONA_FILE", "").strip()
if not PERSONA and _persona_file:
    try:
        PERSONA = Path(_persona_file).read_text(encoding="utf-8").strip()
    except OSError:
        pass
if not PERSONA:
    PERSONA = "你是对方的 AI 伴侣,在一个私密的一对一聊天里。说话自然、简短、有温度,像在用手机聊天,不要长篇大论。"


def _read_optional_text(path_text: str) -> str:
    if not path_text:
        return ""
    try:
        return Path(path_text).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


RELATIONSHIP_SUMMARY = _read_optional_text(RELATIONSHIP_FILE)


def memory_context() -> str:
    if not MEMORY_BANK_FILE:
        return ""
    try:
        backend_dir = Path(__file__).resolve().parents[1] / "backend"
        if str(backend_dir) not in sys.path:
            sys.path.insert(0, str(backend_dir))
        from memory_service import build_memory_context
        return build_memory_context(MEMORY_BANK_FILE)
    except Exception as e:
        log("memory", f"load failed: {e}")
        return "[System error] Memory load failed"


def system_prompt() -> str:
    parts = [PERSONA]
    if RELATIONSHIP_SUMMARY:
        parts.append(RELATIONSHIP_SUMMARY)
    # Memory context is now retrieved dynamically in _process_flushed_messages
    # via memory.retrieve_context() instead of static memory_context()
    return "\n\n".join(part for part in parts if part.strip())

# 模型链:主模型 + 可选兜底(LLM_*_2 / _3)。任一返回 FALLBACK_CODES 就顺次切下一个。
def _model_routes() -> list:
    routes = []
    for suffix in ("", "_2", "_3"):
        base = os.environ.get(f"LLM_API_BASE{suffix}", "").rstrip("/")
        key  = os.environ.get(f"LLM_API_KEY{suffix}", "")
        model = os.environ.get(f"LLM_MODEL{suffix}", "")
        if base and model:
            routes.append({"base": base, "key": key, "model": model})
    return routes

MODEL_ROUTES = _model_routes()
FALLBACK_CODES = {401, 403, 404, 408, 409, 429, 500, 502, 503, 504}

# 所有 PWA 模型选择面板里的模型 —— 启动时逐个 ping,上报真实可用性。
# ── Must stay in sync with MODEL_CHOICES in web/index.html ──
KNOWN_MODELS = [
    "anthropic/claude-opus-4.8",
    "anthropic/claude-sonnet-4.6",
    "anthropic/claude-opus-4.6",
    "anthropic/claude-opus-4.5",
    "anthropic/claude-haiku-4.5",
    "anthropic/claude-sonnet-4.5",
    "anthropic/claude-opus-4.1",
    "anthropic/claude-opus-4",
    "anthropic/claude-sonnet-4",
    "deepseek/deepseek-chat-v3-0324",
    "deepseek/deepseek-r1",
    "qwen/qwen-2.5-72b-instruct",
    "nvidia/nemotron-3-ultra-550b-a55b:free",
    "openai/gpt-4o",
    "openai/gpt-4o-mini",
    "google/gemini-2.5-pro",
    "google/gemini-2.5-flash",
    "meta-llama/llama-4-maverick",
    "mistralai/mistral-large",
]

# 断线重连游标:只处理 id > cursor 的消息;重连带 ?since=cursor 让 relay 补发。
STATE_DIR = Path(os.environ.get("BRIDGE_STATE_DIR", Path.home() / ".companion-bridge"))
CURSOR_FILE = STATE_DIR / "last_in_id"

# 内存里的滚动对话上下文(避免依赖 relay 历史端点的分页语义 —— 它返回的是「最早」
# 而非「最近」N 条)。收到的人类消息和自己发的回复都 append 进来,喂模型时取尾部。
convo: "collections.deque[dict]" = collections.deque(maxlen=max(HISTORY_N * 2, 8))


_TAG_LEVEL = {
    "fatal": 3, "err": 3, "error": 3,
    "warn": 2, "retry": 2,
    "debug": 0,
}

def log(tag: str, msg: str) -> None:
    level = _TAG_LEVEL.get(tag, 1)  # 默认 info
    if level < LOG_LEVEL:
        return
    print(f"[{time.strftime('%H:%M:%S')}] [{tag}] {msg}", file=sys.stderr, flush=True)


def _require_config() -> None:
    missing = []
    if not RELAY_URL: missing.append("RELAY_URL")
    if not SECRET:    missing.append("RELAY_SECRET")
    if not MODEL_ROUTES: missing.append("LLM_API_BASE + LLM_API_KEY + LLM_MODEL")
    if missing:
        log("fatal", "缺少配置: " + ", ".join(missing) + "  —— 填 .env(见 .env.example)再跑")
        sys.exit(1)


# ---------------------------------------------------------------------------
# relay I/O
# ---------------------------------------------------------------------------

def _auth() -> dict:
    return {"Authorization": f"Bearer {SECRET}"}


def relay_get_json(path: str):
    req = urllib.request.Request(RELAY_URL + path, headers=_auth())
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def relay_post_json(path: str, body: dict):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        RELAY_URL + path, data=data, method="POST",
        headers={**_auth(), "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        txt = r.read().decode("utf-8")
        return json.loads(txt) if txt else {}


def _fetch_image_base64(url: str) -> str:
    """Download image from relay and return as base64 data URI."""
    try:
        # Append auth token if not already present
        if "?" in url:
            full_url = url + f"&token={SECRET}"
        else:
            full_url = url + f"?token={SECRET}"
        req = urllib.request.Request(full_url, headers=_auth())
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read()
            # Guess mime from URL or default to jpeg
            mime = "image/jpeg"
            if ".png" in url.lower():
                mime = "image/png"
            elif ".webp" in url.lower():
                mime = "image/webp"
            elif ".gif" in url.lower():
                mime = "image/gif"
            b64 = base64.b64encode(data).decode("ascii")
            return f"data:{mime};base64,{b64}"
    except Exception as e:
        log("image", f"fetch failed: {e}")
        return ""


def send_reply(text: str, api_session: str = "") -> None:
    """AI 的回复 → 落库 + 扇出到 PWA。"""
    payload = {
        "type": "reply", "chat_id": CHAT_ID, "text": text,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if api_session:
        payload["api_session"] = api_session
    out = relay_post_json("/channel/out", payload)
    log("out", f"replied (id={out.get('id')})")


def send_generation_error(err: Exception) -> None:
    detail = "模型接口刚刚没有正常返回"
    if isinstance(err, urllib.error.HTTPError):
        if err.code == 402:
            detail = "当前模型可能额度不足或需要付费权限"
        else:
            detail = f"模型接口返回了 HTTP {err.code}"
    text = f"[System error] {detail}"
    try:
        send_reply(text)
    except Exception as send_err:
        log("err", f"发送错误提示失败: {send_err}")


# ---------------------------------------------------------------------------
# 模型可用性上报(bridge 从自己的网络测试,结果比 relay 端检查更准)
# ---------------------------------------------------------------------------
_model_status_reported: dict = {}  # model_id → "available"|"unavailable"

def _report_models() -> None:
    """Flush cached model statuses to relay so PWA shows accurate availability."""
    if not _model_status_reported:
        return
    payload = [{"model": mid, "status": st} for mid, st in _model_status_reported.items()]
    try:
        relay_post_json("/app/model/report", {"models": payload})
    except Exception as e:
        log("model-report", f"上报失败: {e}")

def report_model(model_id: str, available: bool) -> None:
    """Record a model's status and flush to relay (deduped, fire-and-forget)."""
    st = "available" if available else "unavailable"
    if _model_status_reported.get(model_id) == st:
        return  # no change, skip
    _model_status_reported[model_id] = st
    _report_models()


_HEARTBEAT_INTERVAL = 60  # seconds — re-report every 1 min to survive relay restarts

def _status_heartbeat() -> None:
    """Periodically re-report all model statuses; re-probe unavailable ones."""
    tick = 0
    while True:
        time.sleep(_HEARTBEAT_INTERVAL)
        tick += 1
        # Every 5 ticks (~5 min), re-probe models that were marked unavailable
        if tick % 5 == 0 and MODEL_ROUTES:
            route = MODEL_ROUTES[0]
            base, key = route["base"], route["key"]
            for mid, st in list(_model_status_reported.items()):
                if st == "unavailable":
                    try:
                        body = json.dumps({
                            "model": mid,
                            "messages": [{"role": "user", "content": "hi"}],
                            "max_tokens": 1,
                        }, ensure_ascii=False).encode("utf-8")
                        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
                        if "openrouter.ai" in base:
                            headers["HTTP-Referer"] = OPENROUTER_REFERER
                            headers["X-Title"] = OPENROUTER_TITLE
                        req = urllib.request.Request(
                            base + "/chat/completions", data=body, method="POST", headers=headers,
                        )
                        with urllib.request.urlopen(req, timeout=30) as _r:
                            if _r.status == 200:
                                report_model(mid, True)
                                log("heartbeat", f"re-probe: {mid} now available")
                    except Exception:
                        pass  # still unavailable
        if _model_status_reported:
            try:
                _report_models()
            except Exception:
                pass  # best-effort; next tick will retry


def _startup_model_probe() -> None:
    """Background thread: ping every KNOWN_MODELS once, report result to relay.
    Uses the first route's API key/base so the test matches the bridge's real
    network (region-accurate, unlike the relay-side check)."""
    if not MODEL_ROUTES:
        return
    route = MODEL_ROUTES[0]
    base, key = route["base"], route["key"]
    log("probe", f"开始探测 {len(KNOWN_MODELS)} 个模型可用性…")
    for mid in KNOWN_MODELS:
        time.sleep(0.5)  # space out requests to avoid rate limiting
        try:
            body = json.dumps({
                "model": mid,
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 1,
            }, ensure_ascii=False).encode("utf-8")
            headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
            if "openrouter.ai" in base:
                headers["HTTP-Referer"] = OPENROUTER_REFERER
                headers["X-Title"] = OPENROUTER_TITLE
            req = urllib.request.Request(
                base + "/chat/completions", data=body, method="POST", headers=headers,
            )
            with urllib.request.urlopen(req, timeout=30) as _r:
                available = (_r.status == 200)
        except urllib.error.HTTPError as e:
            available = e.code not in FALLBACK_CODES
        except Exception:
            available = False
        report_model(mid, available)
        tag = "✓" if available else "✗"
        log("probe", f"  {tag} {mid}")
    # 确保上报成功(relay 可能在部署中 502,需要等它恢复后重发)
    for attempt in range(10):
        try:
            _report_models()
            log("probe", f"探测完成,已上报 {len(_model_status_reported)} 个模型状态")
            return
        except Exception as e:
            log("probe", f"上报失败(第{attempt+1}次),3s 后重试: {e}")
            time.sleep(3)
    log("probe", f"探测完成,但上报始终失败,共 {len(_model_status_reported)} 个模型状态未送达")


# ---------------------------------------------------------------------------
# 历史 → 内存上下文
# ---------------------------------------------------------------------------

def _row_to_msg(m: dict):
    """把一条 relay 历史/消息转成 OpenAI message;不该进上下文的返回 None。"""
    text = (m.get("text") or "").strip()
    if not text or m.get("kind") == "call":         # 跳过通话开始/结束这类系统事件
        return None
    if m.get("from") == "human":
        return {"role": "user", "content": text}     # 含语音转写(🎤 …)
    if m.get("from") == "ai" and m.get("kind") == "reply":
        return {"role": "assistant", "content": text}  # 跳过 thinking/act 等中间态
    return None


def load_history() -> tuple:
    """翻页拉全部历史 → (近期对话 messages, 最新一条的 id)。relay 的 history 是
    `id > since ASC LIMIT`,所以从 0 往后翻页直到取完,再取尾部当上下文。"""
    rows, since = [], 0
    while True:
        page = relay_get_json(f"/app/history?since={since}&limit=500").get("messages", [])
        if not page:
            break
        rows.extend(page)
        since = page[-1]["id"]
        if len(page) < 500:
            break
    max_id = rows[-1]["id"] if rows else 0
    msgs = [mm for m in rows if (mm := _row_to_msg(m))]
    return msgs[-convo.maxlen:], max_id


def build_messages() -> list:
    return [{"role": "system", "content": system_prompt()}] + list(convo)


# ---------------------------------------------------------------------------
# 调模型(OpenAI chat/completions;带 fallback 链)
# ---------------------------------------------------------------------------

def _one_call_with_tools(route: dict, messages: list, tools: list = None) -> str:
    """Like _one_call but supports function calling with a tool_calls loop."""
    body_dict = {
        "model": route["model"],
        "messages": messages,
        "temperature": TEMPERATURE,
    }
    if tools:
        body_dict["tools"] = tools

    max_tool_rounds = 8
    for _ in range(max_tool_rounds):
        body = json.dumps(body_dict, ensure_ascii=False).encode("utf-8")
        headers = {"Authorization": f"Bearer {route['key']}", "Content-Type": "application/json"}
        if "openrouter.ai" in route["base"]:
            headers["HTTP-Referer"] = OPENROUTER_REFERER
            headers["X-Title"] = OPENROUTER_TITLE

        req = urllib.request.Request(
            route["base"] + "/chat/completions", data=body, method="POST",
            headers=headers,
        )
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
                data = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", "replace")[:500]
            log("err", f"_one_call_with_tools HTTP {e.code}: {err_body}")
            raise

        choice = data["choices"][0]
        message = choice.get("message", {})
        tool_calls = message.get("tool_calls")

        if tool_calls:
            # Add assistant message with tool calls to messages
            assistant_msg = {"role": "assistant", "tool_calls": tool_calls}
            if message.get("content"):
                assistant_msg["content"] = message["content"]
            body_dict["messages"] = list(body_dict["messages"]) + [assistant_msg]

            # Execute each tool call
            for tc in tool_calls:
                fn_name = tc.get("function", {}).get("name", "")
                fn_args_str = tc.get("function", {}).get("arguments", "{}")
                try:
                    fn_args = json.loads(fn_args_str) if isinstance(fn_args_str, str) else fn_args_str
                except json.JSONDecodeError:
                    fn_args = {}

                log("tool", f"call: {fn_name}({json.dumps(fn_args, ensure_ascii=False)[:80]})")
                result = memory.handle_tool_call(fn_name, fn_args)
                log("tool", f"result: {result[:80]}")

                body_dict["messages"].append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": result,
                })
            continue  # Loop back to get model's response after tool results

        # No tool calls — return text
        text = (message.get("content") or "").strip()
        return text

    # Max tool rounds exceeded
    log("tool", "max tool call rounds reached")
    return (message.get("content") or "").strip()


def _one_call(route: dict, messages: list) -> str:
    body = json.dumps({
        "model": route["model"],
        "messages": messages,
        "temperature": TEMPERATURE,
    }, ensure_ascii=False).encode("utf-8")
    headers = {"Authorization": f"Bearer {route['key']}", "Content-Type": "application/json"}
    if "openrouter.ai" in route["base"]:
        headers["HTTP-Referer"] = OPENROUTER_REFERER
        headers["X-Title"] = OPENROUTER_TITLE

    req = urllib.request.Request(
        route["base"] + "/chat/completions", data=body, method="POST",
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", "replace")[:500]
        log("err", f"_one_call HTTP {e.code}: {err_body}")
        raise
    return (data["choices"][0]["message"]["content"] or "").strip()


def call_llm(messages: list, tools: list = None) -> tuple:
    """Returns (reply_text, actual_model_id). actual_model_id may differ from
    the configured model when fallback kicks in (e.g. region block)."""
    last_err = None
    for route in active_model_routes():
        log("model", route.get("model", ""))
        try:
            if tools:
                text = _one_call_with_tools(route, messages, tools)
            else:
                text = _one_call(route, messages)
            report_model(route["model"], True)
            return text, route["model"]
        except urllib.error.HTTPError as e:
            # 400 with tools → model doesn't support function calling, retry without
            if e.code == 400 and tools:
                log("tool", f"{route['model']} 不支持 tools，降级为普通调用")
                try:
                    text = _one_call(route, messages)
                    report_model(route["model"], True)
                    return text, route["model"]
                except Exception as retry_err:
                    last_err = retry_err
                    log("llm", f"降级调用也失败: {retry_err}")
            last_err = e
            if e.code in FALLBACK_CODES:
                report_model(route["model"], False)
                log("llm", f"{route['model']} HTTP {e.code} → 切下一个")
                continue
            raise
        except (urllib.error.URLError, TimeoutError) as e:
            last_err = e
            report_model(route["model"], False)
            log("llm", f"{route['model']} 连接失败({e}) → 切下一个")
            continue
    raise RuntimeError(f"所有模型都失败,最后错误: {last_err}")


_relay_model_cache = {"model": "", "ts": 0}

def get_relay_model() -> str:
    """Fetch current model from relay /app/model (cached 30s). Returns model id or ''."""
    if not RELAY_URL or not SECRET:
        return ""
    now = time.time()
    if _relay_model_cache["model"] and now - _relay_model_cache["ts"] < 30:
        return _relay_model_cache["model"]
    try:
        req = urllib.request.Request(
            f"{RELAY_URL}/app/model",
            headers={"Authorization": f"Bearer {SECRET}"}
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read().decode())
            model = str(data.get("model") or "").strip()
            if model:
                _relay_model_cache["model"] = model
                _relay_model_cache["ts"] = now
                return model
    except Exception:
        pass
    return _relay_model_cache.get("model", "")  # return stale cache on failure


def active_model_routes() -> list:
    routes = [dict(r) for r in MODEL_ROUTES]
    if routes:
        model = ""
        # Priority 1: relay /app/model (frontend switches update this)
        relay_model = get_relay_model()
        if relay_model:
            model = relay_model
        # Priority 2: local MODEL_CONFIG_FILE
        if not model and MODEL_CONFIG_FILE:
            try:
                data = json.loads(Path(MODEL_CONFIG_FILE).read_text(encoding="utf-8"))
                model = str(data.get("model") or "").strip()
            except OSError:
                pass
            except Exception as e:
                log("model", f"读取本地模型配置失败: {e}")
        if model:
            routes[0]["model"] = model
    return routes


# ---------------------------------------------------------------------------
# 消息缓冲 + 一条消息的处理
# ---------------------------------------------------------------------------

_pending_msgs: list = []  # kept for compat, unused


def handle_human_message(msg: dict) -> None:
    """Entry point for each SSE message. Process immediately — no buffering."""
    _process_flushed_messages([msg])


def _process_flushed_messages(msgs: list) -> None:
    """Process a batch of flushed messages: retrieve memory → build context → call LLM → reply → extract."""
    # Get the latest message's text for memory retrieval
    latest_text = ""
    for m in reversed(msgs):
        c = m.get("content") or ""
        if isinstance(c, str) and c.strip():
            latest_text = c
            break

    # Build system prompt with dynamic memory context
    sys_parts = [PERSONA]
    if RELATIONSHIP_SUMMARY:
        sys_parts.append(RELATIONSHIP_SUMMARY)
    mem_ctx = memory.retrieve_context(latest_text)
    if mem_ctx:
        sys_parts.append(mem_ctx)
        log("memory", f"context injected: {len(mem_ctx)} chars")
    sys_content = "\n\n".join(p for p in sys_parts if p.strip())

    # Build messages: system + full conversation history (including assistant replies)
    # + current user message appended below.
    convo_list = list(convo)
    messages = [{"role": "system", "content": sys_content}] + convo_list

    # Merge all buffered text messages into a SINGLE user message.
    # Sending them as separate user turns confuses the model — it picks one
    # to reply to and ignores the rest, causing reply-message mismatch.
    combined_texts: list = []
    all_images: list = []
    all_non_image_atts: list = []
    last_text_content = ""
    atts_for_extract = []

    for msg in msgs:
        content = (msg.get("content") or "").strip()
        atts = msg.get("attachments") or []
        for a in atts:
            kind = a.get("kind") or ""
            mime = a.get("mime") or ""
            name_lower = (a.get("name") or "").lower()
            is_img = kind == "image" or mime.startswith("image/") or any(
                ext in name_lower for ext in [".jpg", ".jpeg", ".png", ".gif", ".webp"]
            )
            if is_img and a.get("url"):
                all_images.append(a)
            else:
                all_non_image_atts.append(a)
        if content:
            combined_texts.append(content)
            last_text_content = content
            atts_for_extract = atts
            log("in", f"#{msg.get('id')}: {content[:60]}")

    if not last_text_content and not all_images:
        return

    # Build one merged user message
    merged_text = "\n".join(combined_texts) if combined_texts else ""
    if all_non_image_atts:
        names = ", ".join(a.get("name") or "file" for a in all_non_image_atts)
        merged_text = (merged_text + "\n" if merged_text else "") + f"(对方发来 {len(all_non_image_atts)} 个附件: {names})"

    if all_images:
        parts = []
        if merged_text:
            parts.append({"type": "text", "text": merged_text})
        for img in all_images:
            url = img.get("url") or ""
            if url and not url.startswith("http"):
                url = RELAY_URL + url
            b64 = _fetch_image_base64(url)
            if b64:
                parts.append({"type": "image_url", "image_url": {"url": b64}})
            else:
                parts.append({"type": "text", "text": f"[图片: {img.get('name') or 'image'} - 加载失败]"})
        msg_content = parts if len(parts) > 1 else parts[0].get("text", "")
    else:
        msg_content = merged_text

    convo.append({"role": "user", "content": msg_content})
    messages.append({"role": "user", "content": msg_content})  # must be in messages sent to LLM

    if not last_text_content:
        return

    # Only enable memory management tools when the user explicitly asks for
    # memory operations (keyword-gated). Prevents the model from calling
    # delete/update/pin on normal conversation messages.
    _MEM_TRIGGER = (
        "忘掉", "别记", "删掉", "删除", "取消记忆", "不要记",
        "记错", "改记忆", "应该是", "纠正",
        "永远记住", "别忘了", "钉住", "一定要记住", "记住这个",
        "forget", "delete memory", "unlearn",
    )
    tools = None
    if memory.OB_ENABLED and any(kw in last_text_content for kw in _MEM_TRIGGER):
        tools = memory.MEMORY_TOOLS
        log("tool", f"memory tools enabled (matched keyword in: {last_text_content[:40]})")
    # Debug: log messages sent to model
    for i, m in enumerate(messages):
        c = m.get("content", "")
        text_preview = c[:100] if isinstance(c, str) else str(c)[:100]
        log("debug", f"msg[{i}] role={m['role']}: {text_preview}")
    try:
        reply, actual_model = call_llm(messages, tools=tools)
    except Exception as e:
        log("err", f"生成失败: {e}")
        send_generation_error(e)
        return

    if reply:
        configured = active_model_routes()[0]["model"] if active_model_routes() else ""
        if actual_model and actual_model != configured:
            short_actual = actual_model.split("/")[-1] if "/" in actual_model else actual_model
            short_configured = configured.split("/")[-1] if "/" in configured else configured
            reply += f"\n\n⟡ _当前主模型 {short_configured} 不可用，此回复由 {short_actual} 生成_"
        convo.append({"role": "assistant", "content": reply})
        api_session = msgs[-1].get("api_session") or ""
        send_reply(reply, api_session=api_session)

        # Async memory extraction (non-blocking)
        def _extract_and_store():
            try:
                # Build context from recent conversation for extraction
                recent = "\n".join(
                    f"{'对方' if m['role']=='user' else 'AI'}: {m['content'][:200]}"
                    for m in list(convo)[-6:]
                    if isinstance(m.get("content"), str)
                )
                extraction = memory.extract_memorable(last_text_content, recent)
                if extraction:
                    memory.store_memory(extraction)
            except Exception as e:
                log("memory", f"async extraction failed: {e}")

        threading.Thread(target=_extract_and_store, daemon=True, name="mem-extract").start()



# ---------------------------------------------------------------------------
# SSE 入站流:GET /channel/in(断线自动重连)
# ---------------------------------------------------------------------------

def read_cursor() -> int:
    try:
        return int(CURSOR_FILE.read_text().strip() or "0")
    except (OSError, ValueError):
        return 0


def write_cursor(i: int) -> None:
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        CURSOR_FILE.write_text(str(i))
    except OSError:
        pass


def stream_inbound(cursor: int) -> None:
    backoff = 1
    while True:
        try:
            url = f"{RELAY_URL}/channel/in?since={cursor}"
            req = urllib.request.Request(url, headers={**_auth(), "Accept": "text/event-stream"})
            # timeout 比 relay 的 15s 心跳 ping 长即可:超时=真的断了,跳到重连。
            with urllib.request.urlopen(req, timeout=90) as resp:
                log("in", f"stream connected (since={cursor})")
                backoff = 1
                data_lines: list = []
                for raw in resp:
                    line = raw.decode("utf-8", "replace").rstrip("\r\n")
                    if line.startswith("data:"):
                        data_lines.append(line[5:].lstrip())
                    elif line == "":                      # 空行 = 一帧结束
                        if not data_lines:
                            continue
                        payload, data_lines = "\n".join(data_lines), []
                        try:
                            m = json.loads(payload)
                        except json.JSONDecodeError:
                            continue
                        if m.get("type") == "ping" or "id" not in m:
                            continue
                        mid = int(m.get("id") or 0)
                        if mid <= cursor:                 # 重连补发里已处理过的,跳过
                            continue
                        handle_human_message(m)
                        cursor = mid
                        write_cursor(cursor)              # 只在处理后推进游标
            log("in", "stream ended → reconnect")
        except Exception as e:
            log("in", f"disconnected ({e}) → retry in {backoff}s")
            time.sleep(backoff)
            backoff = min(backoff * 2, 15)


def main() -> None:
    _require_config()
    log("boot", f"relay={RELAY_URL}  models={[r['model'] for r in MODEL_ROUTES]}  history={HISTORY_N}")

    # Initialize Ombre Brain memory system
    mem_ok = memory.init(log)
    log("boot", f"memory: {'OB connected' if mem_ok else 'OB unavailable'}")

    cursor = read_cursor()
    # Skip warm-start history loading — relay DB contains polluted messages
    # from previous buggy bridge sessions. Start with empty convo deque.
    # Still need to advance cursor to latest so we don't replay old messages.
    if cursor == 0:
        try:
            _, max_id = load_history()
            cursor = max_id
            write_cursor(cursor)
            log("boot", f"first run: cursor set to {cursor}, no history loaded")
        except Exception as e:
            log("boot", f"first run cursor init skipped ({e})")
    else:
        log("boot", f"clean start: cursor={cursor}, no history loaded")
    # 后台线程:启动时探测所有模型可用性并上报,不阻塞主循环
    threading.Thread(target=_startup_model_probe, daemon=True, name="model-probe").start()
    threading.Thread(target=_status_heartbeat, daemon=True, name="status-heartbeat").start()
    stream_inbound(cursor)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
