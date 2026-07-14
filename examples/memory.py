"""
memory.py — Ombre Brain integration for Liminal bridge.

Handles:
- MCP session management (init, notify, tool calls)
- Memory retrieval (breath-hook for surfacing, search API for queries)
- Memory extraction (cheap LLM to decide what to remember)
- Memory writing (hold/grow via MCP)
- Memory management (trace/delete via MCP for in-chat commands)
- Dream triggering (dream-hook for idle consolidation)
- 20-second message buffer (batch consecutive messages)
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

OB_BASE_URL = os.environ.get("OB_BASE_URL", "http://localhost:18001").rstrip("/")
OB_DASHBOARD_PASSWORD = os.environ.get("OB_DASHBOARD_PASSWORD", "")
OB_ENABLED = os.environ.get("OB_ENABLED", "true").lower() in ("true", "1", "yes")

MEMORY_EXTRACT_MODEL = os.environ.get("MEMORY_EXTRACT_MODEL", "gemini-2.0-flash")
MEMORY_EXTRACT_BASE_URL = os.environ.get(
    "MEMORY_EXTRACT_BASE_URL",
    "https://generativelanguage.googleapis.com/v1beta/openai/",
).rstrip("/")
MEMORY_EXTRACT_API_KEY = os.environ.get("MEMORY_EXTRACT_API_KEY", "")
MEMORY_EXTRACT_ENABLED = os.environ.get("MEMORY_EXTRACT_ENABLED", "true").lower() in (
    "true", "1", "yes"
)

MESSAGE_BUFFER_SECONDS = int(os.environ.get("MESSAGE_BUFFER_SECONDS", "20"))

# Hook token for breath-hook / dream-hook auth
OB_HOOK_TOKEN = os.environ.get("OB_HOOK_TOKEN", "liminal-bridge-token")

# Memory retrieval token budget
MEMORY_MAX_TOKENS = int(os.environ.get("MEMORY_MAX_TOKENS", "1500"))

# ---------------------------------------------------------------------------
# Logging (reuse bridge's log function if available, else fallback)
# ---------------------------------------------------------------------------

_log_fn = None

def set_log_fn(fn):
    """Called by bridge to inject its log() function."""
    global _log_fn
    _log_fn = fn


def log(tag: str, msg: str) -> None:
    if _log_fn:
        _log_fn(tag, msg)
    else:
        ts = time.strftime("%H:%M:%S")
        sys.stderr.write(f"[{ts}] [{tag}] {msg}\n")
        sys.stderr.flush()


# ---------------------------------------------------------------------------
# MCP Session Management
# ---------------------------------------------------------------------------

class MCPSession:
    """Manages a persistent MCP session with Ombre Brain."""

    def __init__(self, base_url: str):
        self.base_url = base_url
        self.session_id: Optional[str] = None
        self._lock = threading.Lock()
        self._id_counter = 0

    def _next_id(self) -> int:
        self._id_counter += 1
        return self._id_counter

    def _post(self, body: dict, timeout: int = 30) -> tuple:
        """Send JSON-RPC to /mcp, return (session_id, parsed_results)."""
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.session_id:
            headers["mcp-session-id"] = self.session_id

        req = urllib.request.Request(
            f"{self.base_url}/mcp", data=data, headers=headers, method="POST"
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            sid = resp.headers.get("mcp-session-id", self.session_id)
            raw = resp.read().decode("utf-8", "replace")

            results = []
            for line in raw.split("\n"):
                if line.startswith("data:"):
                    try:
                        results.append(json.loads(line[5:].strip()))
                    except json.JSONDecodeError:
                        pass
            return sid, results

    def ensure_session(self) -> bool:
        """Initialize MCP session if not already connected."""
        with self._lock:
            if self.session_id:
                return True
            try:
                sid, results = self._post({
                    "jsonrpc": "2.0",
                    "id": self._next_id(),
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {},
                        "clientInfo": {"name": "liminal-bridge", "version": "1.0"},
                    },
                })
                self.session_id = sid

                # Send initialized notification
                self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})

                log("memory", f"MCP session established: {sid[:12]}...")
                return True
            except Exception as e:
                log("memory", f"MCP init failed: {e}")
                self.session_id = None
                return False

    def reset_session(self):
        """Force re-init on next call."""
        with self._lock:
            self.session_id = None

    def call_tool(self, name: str, arguments: dict, timeout: int = 60) -> Optional[str]:
        """Call an MCP tool, return the text content or None on error."""
        if not self.ensure_session():
            return None

        body = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }

        try:
            sid, results = self._post(body, timeout=timeout)
            for r in results:
                if "result" in r:
                    content_parts = r["result"].get("content", [])
                    texts = [c.get("text", "") for c in content_parts if c.get("type") == "text"]
                    return "\n".join(texts)
                elif "error" in r:
                    log("memory", f"MCP {name} error: {r['error']}")
                    # Session might be stale, reset
                    self.reset_session()
                    return None
            return None
        except Exception as e:
            log("memory", f"MCP {name} failed: {e}")
            self.reset_session()
            return None


# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------

_mcp: Optional[MCPSession] = None
_auth_cookie: Optional[str] = None
_message_buffer: list = []
_buffer_timer: Optional[threading.Timer] = None
_buffer_lock = threading.Lock()
_flush_callback = None  # Set by bridge


def init(bridge_log_fn=None):
    """Initialize memory module. Called by bridge at startup."""
    global _mcp, _flush_callback

    if not OB_ENABLED:
        log("memory", "Ombre Brain disabled (OB_ENABLED=false)")
        return False

    if bridge_log_fn:
        set_log_fn(bridge_log_fn)

    _mcp = MCPSession(OB_BASE_URL)

    # Test connection
    try:
        req = urllib.request.Request(f"{OB_BASE_URL}/health")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            log("memory", f"OB connected: {data.get('status', '?')}, "
                f"buckets={data.get('buckets', '?')}, decay={data.get('decay_engine', '?')}")
            return True
    except Exception as e:
        log("memory", f"OB health check failed: {e}")
        return False


# ---------------------------------------------------------------------------
# Authentication (for REST API endpoints that need dashboard login)
# ---------------------------------------------------------------------------

def _get_auth_cookie() -> Optional[str]:
    """Login to OB dashboard and return session cookie."""
    global _auth_cookie
    if _auth_cookie:
        return _auth_cookie
    if not OB_DASHBOARD_PASSWORD:
        log("memory", "OB login skipped: no OB_DASHBOARD_PASSWORD set")
        return None
    try:
        data = json.dumps({"password": OB_DASHBOARD_PASSWORD}).encode()
        req = urllib.request.Request(
            f"{OB_BASE_URL}/auth/login",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        # Use direct opener that bypasses proxy for localhost
        with _local_open(req) as resp:
            cookie = resp.headers.get("Set-Cookie", "")
            if "ombre_session=" in cookie:
                _auth_cookie = cookie.split(";")[0]
                log("memory", f"OB login OK: {_auth_cookie[:20]}...")
                return _auth_cookie
            else:
                log("memory", f"OB login: no session cookie in response")
    except Exception as e:
        log("memory", f"OB login failed: {e}")
    return None


def _local_open(req, timeout=15):
    """Open a localhost URL bypassing any HTTP_PROXY."""
    import urllib.request as _ur
    opener = _ur.build_opener(_ur.ProxyHandler({}))
    return opener.open(req, timeout=timeout)


def _api_get(path: str) -> Optional[any]:
    """Authenticated GET to OB REST API."""
    cookie = _get_auth_cookie()
    headers = {}
    if cookie:
        headers["Cookie"] = cookie
    try:
        # Encode non-ASCII characters in the URL path/query
        from urllib.parse import quote as urlquote
        encoded_path = urlquote(path, safe="/?=&:%")
        req = urllib.request.Request(f"{OB_BASE_URL}{encoded_path}", headers=headers)
        with _local_open(req) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as e:
        log("memory", f"API GET {path[:60]} failed: {e}")
        return None


def _hook_get(path: str, params: str = "") -> Optional[str]:
    """GET an OB hook endpoint with token auth."""
    url = f"{OB_BASE_URL}{path}"
    if params:
        url += f"?{params}"
    elif OB_HOOK_TOKEN:
        url += f"?token={OB_HOOK_TOKEN}"
    try:
        req = urllib.request.Request(url)
        with _local_open(req) as resp:
            return resp.read().decode("utf-8", "replace")
    except Exception as e:
        log("memory", f"Hook GET {path} failed: {e}")
        return None


# ---------------------------------------------------------------------------
# Memory Retrieval
# ---------------------------------------------------------------------------

def retrieve_context(current_message: str = "") -> str:
    """
    Retrieve relevant memories for context injection into system prompt.
    Uses breath-hook for surfacing + search for message-specific retrieval.
    Returns formatted text to append to system prompt.
    """
    if not OB_ENABLED:
        return ""

    parts = []

    # 1. Breath hook: surface unresolved/important memories
    breath_text = _hook_get("/breath-hook")
    if breath_text and breath_text.strip() and "[Ombre Brain" in breath_text:
        parts.append(breath_text.strip())

    # 2. Search for message-specific memories (if message provided)
    if current_message and len(current_message) > 2:
        from urllib.parse import quote as urlquote
        search_results = _api_get(f"/api/search?q={urlquote(current_message[:200])}")
        if search_results and isinstance(search_results, list) and len(search_results) > 0:
            search_parts = []
            for item in search_results[:5]:
                name = item.get("name", "")
                content = item.get("content", "")
                if content:
                    search_parts.append(f"- {name}: {content[:200]}")
            if search_parts:
                parts.append("[检索记忆]\n" + "\n".join(search_parts))

    if not parts:
        return ""

    combined = "\n\n".join(parts)

    # Truncate to token budget (rough: 1 token ≈ 2 chars for Chinese)
    max_chars = MEMORY_MAX_TOKENS * 2
    if len(combined) > max_chars:
        combined = combined[:max_chars] + "\n[...truncated]"

    return combined


# ---------------------------------------------------------------------------
# Memory Extraction (cheap LLM)
# ---------------------------------------------------------------------------

_EXTRACT_PROMPT = """你是一个记忆提取器。根据以下对话内容，提取所有可能有长期价值的信息。
原则：宁多勿漏。记错了可以衰减忘掉，漏记了就永远找不回来。

值得记忆的（满足任意一条就记）：
- 对方的个人信息（生日、习惯、偏好、健康、作息、口味、审美）
- 对方的日常动态（今天做了什么、吃了什么、去了哪里、见了谁）
- 情感强度高的时刻（争吵、表白、重大决定）
- 两人之间的约定、承诺、暗语、特殊用词
- 对方的情绪变化及其原因
- 对方表达的观点、态度、立场（即使很随意）
- 对方的计划、打算、期待
- 工作/学习相关的重要事项或情绪
- 对方提到的任何人、事、物的偏好或评价
- 里程碑事件
- 对方说的任何你觉得"以后可能用得上"的细节

只有以下情况才跳过：
- 纯粹的无意义语气词（"嗯""哦""好"且没有任何附加信息）
- 已经被记录过的完全重复的信息

importance 参考：
- 2~3：日常小事、随口提到的偏好、普通动态
- 4~5：有一定情感重量的事、重要偏好、小约定
- 6~7：明显的情绪事件、重要决定、深层偏好
- 8~10：重大事件、核心承诺、高情感时刻

输出 JSON（只输出 JSON，不要其他内容）：
- 如果没有值得记忆的：{{"remember": false}}
- 如果有：{{
    "remember": true,
    "content": "用第一人称中文写的一句话记忆（保留原话中的关键用词）",
    "valence": 0.0到1.0之间的情感效价（0=消极, 1=积极）,
    "arousal": 0.0到1.0之间的唤醒度（0=平静, 1=激动）,
    "importance": 2到10的重要度
  }}

对话内容：
{context}"""


def extract_memorable(message_text: str, recent_context: str = "") -> Optional[dict]:
    """
    Use a cheap LLM to determine if a message is worth remembering.
    Returns extraction result dict or None.
    """
    if not MEMORY_EXTRACT_ENABLED or not MEMORY_EXTRACT_API_KEY:
        return None

    if not message_text or len(message_text.strip()) < 3:
        return None

    context = message_text
    if recent_context:
        context = f"{recent_context}\n\n最新一条：{message_text}"

    prompt = _EXTRACT_PROMPT.format(context=context[:2000])

    body = json.dumps({
        "model": MEMORY_EXTRACT_MODEL,
        "messages": [
            {"role": "system", "content": "你是记忆提取器，只输出 JSON。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "max_tokens": 512,
    }, ensure_ascii=False).encode("utf-8")

    headers = {
        "Authorization": f"Bearer {MEMORY_EXTRACT_API_KEY}",
        "Content-Type": "application/json",
    }
    if "openrouter.ai" in MEMORY_EXTRACT_BASE_URL:
        headers["HTTP-Referer"] = os.environ.get("OPENROUTER_HTTP_REFERER", "http://127.0.0.1:4174")
        headers["X-Title"] = os.environ.get("OPENROUTER_TITLE", "Liminal Memory Extractor")

    try:
        req = urllib.request.Request(
            f"{MEMORY_EXTRACT_BASE_URL}/chat/completions",
            data=body,
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode())

        text = (data.get("choices", [{}])[0].get("message", {}).get("content", "") or "").strip()

        # Parse JSON from response (strip markdown code blocks if present)
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        result = json.loads(text)
        if result.get("remember"):
            log("memory", f"extracted: {result.get('content', '')[:60]}")
            return result
        return None

    except json.JSONDecodeError:
        log("memory", f"extract: invalid JSON from LLM: {text[:100]}")
        return None
    except Exception as e:
        log("memory", f"extract failed: {e}")
        return None


# ---------------------------------------------------------------------------
# Memory Writing
# ---------------------------------------------------------------------------

def store_memory(extraction: dict) -> bool:
    """Write an extracted memory to Ombre Brain via MCP hold."""
    if not _mcp or not OB_ENABLED:
        return False

    content = extraction.get("content", "")
    if not content:
        return False

    # Build hold arguments
    args = {"content": content}

    # Pass emotion coordinates if available
    valence = extraction.get("valence")
    arousal = extraction.get("arousal")
    if valence is not None:
        args["valence"] = float(valence)
    if arousal is not None:
        args["arousal"] = float(arousal)

    result = _mcp.call_tool("hold", args, timeout=30)
    if result:
        log("memory", f"stored: {content[:60]} → {result[:80]}")
        return True
    return False


def store_memory_async(extraction: dict) -> None:
    """Store memory in background thread (non-blocking)."""
    t = threading.Thread(target=store_memory, args=(extraction,), daemon=True)
    t.start()


# ---------------------------------------------------------------------------
# Memory Management (in-chat natural language commands)
# ---------------------------------------------------------------------------

def delete_memory(bucket_id: str) -> bool:
    """Delete a memory bucket."""
    if not _mcp:
        return False
    result = _mcp.call_tool("trace", {"bucket_id": bucket_id, "delete": True})
    return result is not None


def resolve_memory(bucket_id: str) -> bool:
    """Mark a memory as resolved (放下)."""
    if not _mcp:
        return False
    result = _mcp.call_tool("trace", {"bucket_id": bucket_id, "resolved": 1})
    return result is not None


def pin_memory(bucket_id: str) -> bool:
    """Pin a memory as permanent core."""
    if not _mcp:
        return False
    result = _mcp.call_tool("trace", {"bucket_id": bucket_id, "pinned": 1})
    return result is not None


# ---------------------------------------------------------------------------
# Dream
# ---------------------------------------------------------------------------

def trigger_dream(window_hours: int = 48) -> Optional[str]:
    """Trigger OB dream consolidation."""
    return _hook_get("/dream-hook", f"window_hours={window_hours}")


# ---------------------------------------------------------------------------
# Message Buffer
# ---------------------------------------------------------------------------

def buffer_message(text: str, callback) -> None:
    """
    Add a message to the 20-second buffer.
    When the buffer timer expires, callback(flushed_text) is called.
    """
    global _buffer_timer, _flush_callback

    _flush_callback = callback

    with _buffer_lock:
        _message_buffer.append(text)

        # Reset timer
        if _buffer_timer:
            _buffer_timer.cancel()

        _buffer_timer = threading.Timer(MESSAGE_BUFFER_SECONDS, _flush_buffer)
        _buffer_timer.daemon = True
        _buffer_timer.start()


def _flush_buffer() -> None:
    """Flush the message buffer and call the callback."""
    global _message_buffer

    with _buffer_lock:
        if not _message_buffer:
            return
        flushed = "\n".join(_message_buffer)
        _message_buffer = []

    if _flush_callback and flushed.strip():
        log("memory", f"buffer flushed: {len(flushed)} chars, "
            f"{flushed.count(chr(10)) + 1} messages merged")
        try:
            _flush_callback(flushed)
        except Exception as e:
            log("memory", f"buffer flush callback failed: {e}")


def get_buffer_contents() -> str:
    """Get current buffer contents without flushing."""
    with _buffer_lock:
        return "\n".join(_message_buffer)


# ---------------------------------------------------------------------------
# MCP Tools for function calling (registered with LLM)
# ---------------------------------------------------------------------------

MEMORY_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "delete_memory",
            "description": "删除一条记忆。当对方说「别记这个」「忘掉」「删掉那条记忆」时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "要删除的原因或记忆描述，用于定位最近的记忆",
                    }
                },
                "required": ["reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_memory",
            "description": "更新/纠正一条记忆。当对方说「记错了」「应该是XX」时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "要更新的原因",
                    },
                    "new_content": {
                        "type": "string",
                        "description": "正确的记忆内容",
                    },
                },
                "required": ["reason", "new_content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pin_memory_by_query",
            "description": "把一条记忆钉为永久核心。当对方说「永远记住这个」「这个很重要别忘了」时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "要钉住的记忆关键词或描述",
                    }
                },
                "required": ["query"],
            },
        },
    },
]


def handle_tool_call(tool_name: str, arguments: dict) -> str:
    """
    Handle a memory management tool call from the LLM.
    Returns a response string to feed back to the model.
    """
    if tool_name == "delete_memory":
        reason = arguments.get("reason", "")
        # Search for the most recent matching memory
        from urllib.parse import quote as urlquote
        results = _api_get(f"/api/search?q={urlquote(reason[:200])}")
        if results and len(results) > 0:
            bucket_id = results[0].get("id", "")
            if bucket_id and delete_memory(bucket_id):
                return f"已删除记忆: {results[0].get('name', '')}"
        return "没有找到匹配的记忆可以删除"

    elif tool_name == "update_memory":
        reason = arguments.get("reason", "")
        new_content = arguments.get("new_content", "")
        from urllib.parse import quote as urlquote
        results = _api_get(f"/api/search?q={urlquote(reason[:200])}")
        if results and len(results) > 0:
            bucket_id = results[0].get("id", "")
            if bucket_id:
                result = _mcp.call_tool("trace", {
                    "bucket_id": bucket_id, "content": new_content
                })
                if result:
                    return f"已更新记忆: {new_content[:60]}"
        return "没有找到匹配的记忆可以更新"

    elif tool_name == "pin_memory_by_query":
        query = arguments.get("query", "")
        from urllib.parse import quote as urlquote
        results = _api_get(f"/api/search?q={urlquote(query[:200])}")
        if results and len(results) > 0:
            bucket_id = results[0].get("id", "")
            if bucket_id and pin_memory(bucket_id):
                return f"已钉住记忆: {results[0].get('name', '')}"
        return "没有找到匹配的记忆可以钉住"

    return f"未知工具: {tool_name}"
