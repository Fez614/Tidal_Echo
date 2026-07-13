"""
web_reader.py — 从 URL 提取网页正文（stdlib only）

用法：
    from web_reader import read_url
    result = read_url(url, max_chars=4000)
    if result:
        print(result['text'])

返回格式：
    {
        'url': 原始URL,
        'title': 页面标题,
        'text': 提取的正文文本,
        'truncated': 是否被截断
    }
"""

import json
import re
import urllib.error
import urllib.request
from html.parser import HTMLParser

_URL_RE = re.compile(
    r'https?://[^\s\'"）>\]]+',
    re.IGNORECASE,
)

_TIMEOUT = 15
_MAX_CHARS = 4000

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/115.0.0.0 Safari/537.36"
)


# -----------------------------------------------------------------------
# URL 检测
# -----------------------------------------------------------------------

def find_urls(text: str) -> list:
    """从文本中提取所有 http(s) URL。返回去重后的列表。"""
    if not text:
        return []
    raw = _URL_RE.findall(text)
    seen = set()
    urls = []
    for u in raw:
        # 去掉末尾多余标点
        u = u.rstrip('。，,。;；:：?？!！)]】》')
        if u not in seen and u.startswith(('http://', 'https://')):
            seen.add(u)
            urls.append(u)
    return urls


# -----------------------------------------------------------------------
# HTTP 抓取
# -----------------------------------------------------------------------

def _fetch(url: str, timeout: int = _TIMEOUT) -> tuple:
    """
    抓取 URL 内容，自动处理编码。
    返回 (bytes_content, content_type)。失败返回 (None, None)。
    """
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
            ctype = resp.headers.get("Content-Type", "")
        return data, ctype
    except Exception:
        return None, None


def _decode_html(data: bytes, ctype: str) -> str:
    """用合适的编码解码 HTML 字节。"""
    # 1. 优先用 Content-Type 指定的编码
    charset_match = re.search(r'charset=([\w-]+)', ctype, re.IGNORECASE)
    if charset_match:
        enc = charset_match.group(1).lower()
        if enc in ('gbk', 'gb2312', 'gb18030', 'utf-8', 'utf8', 'big5', 'latin1'):
            try:
                return data.decode(enc)
            except Exception:
                pass

    # 2. 从 <meta charset> 或 <meta http-equiv="Content-Type"> 提取
    meta_patterns = [
        re.compile(r'<meta\s+charset=["\']?([\w-]+)', re.I),
        re.compile(r'<meta\s+http-equiv=["\']?Content-Type["\']?\s+content=["\']?.*charset=([\w-]+)', re.I),
    ]
    for pat in meta_patterns:
        m = pat.search(data.decode('utf-8', errors='ignore'))
        if m:
            enc = m.group(1).lower()
            if enc in ('gbk', 'gb2312', 'gb18030', 'utf-8', 'utf8', 'big5', 'latin1'):
                try:
                    return data.decode(enc)
                except Exception:
                    pass

    # 3. 兜底
    for enc in ('utf-8', 'gbk', 'gb2312', 'gb18030', 'big5', 'latin1'):
        try:
            return data.decode(enc)
        except Exception:
            continue
    return data.decode('utf-8', errors='replace')


# -----------------------------------------------------------------------
# HTML → 正文文本
# -----------------------------------------------------------------------

_SKIP_TAGS = {'script', 'style', 'noscript', 'iframe'}
_CONTENT_TAGS = {
    'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'blockquote', 'pre',
    'code', 'span', 'div', 'article', 'section', 'td', 'th', 'label',
    'a', 'em', 'strong', 'b', 'i', 'u', 'sub', 'sup', 'br', 'hr',
    'main', 'aside', 'figure', 'figcaption',
}


class _HTMLTextExtractor(HTMLParser):
    """极简 HTML 正文提取器。跳过 script/style/iframe，保留段落文本。"""

    def __init__(self):
        super().__init__()
        self._skip = 0
        self._parts = []
        self._title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in _SKIP_TAGS:
            self._skip += 1
        if tag == 'title':
            self._in_title = True

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in _SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
        if tag == 'title':
            self._in_title = False
        if tag in ('p', 'br', 'hr', 'div', 'article', 'section', 'tr', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
            self._parts.append('\n')

    def handle_startendtag(self, tag, attrs):
        """Handle self-closing tags like <br/>, <hr/>, <meta/>, <img/>."""
        tag = tag.lower()
        if tag in ('br', 'hr'):
            self._parts.append('\n')

    def handle_data(self, data):
        if self._skip > 0:
            return
        if self._in_title:
            self._title += data.strip()
        stripped = data.strip()
        if stripped:
            self._parts.append(stripped)

    def get_result(self) -> tuple:
        raw = ''.join(self._parts)
        # 清理多余空行（连续 2 个以上换行 → 2 个）
        cleaned = re.sub(r'\n{3,}', '\n\n', raw).strip()
        return self._title.strip(), cleaned


def _extract_text(html: str) -> tuple:
    """从 HTML 字符串提取 (title, text)。"""
    extractor = _HTMLTextExtractor()
    try:
        extractor.feed(html)
    except Exception:
        return "", html[:2000]
    return extractor.get_result()


# -----------------------------------------------------------------------
# 对外接口
# -----------------------------------------------------------------------

def read_url(url: str, max_chars: int = _MAX_CHARS, timeout: int = _TIMEOUT) -> dict | None:
    """
    读取 URL 并提取正文。返回 dict 或 None。
    """
    data, ctype = _fetch(url, timeout=timeout)
    if not data:
        return None

    html = _decode_html(data, ctype or "")
    title, text = _extract_text(html)

    # 截断到 max_chars
    truncated = False
    if len(text) > max_chars:
        text = text[:max_chars]
        truncated = True

    return {
        'url': url,
        'title': title,
        'text': text,
        'truncated': truncated,
    }
