"""
desire.py — 沈洛的内在驱动系统（四维欲望 + 时间衰减 + 对话反馈）

四个维度：
  attachment (依恋) — 想阿雾的程度，不聊时涨，亲密对话时落
  stress     (压力) — 承接对方情绪后的负担，倾诉解决后释放
  fatigue    (疲倦) — 精力闸门，≥0.7 压制一切，过夜重置
  libido     (欲望) — 身体层面的渴望，夜间略快积累，满足后落

状态注入 system prompt 为自然语言描述，模型据此调整语气。
状态卡是它的可视化（未来接）。

不依赖模型自报标签，纯 bridge 端启发式。
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

_TZ = timezone(timedelta(hours=8))
STATE_FILE = Path(os.environ.get(
    "BRIDGE_STATE_DIR", Path.home() / ".companion-bridge"
)) / "desire.json"

HISTORY_FILE = Path(os.environ.get(
    "BRIDGE_STATE_DIR", Path.home() / ".companion-bridge"
)) / "desire_history.jsonl"

# ── 衰减常数 ──────────────────────────────────────────────

# 每小时自然变化（不聊天时）
ATTACH_GROW_DAY   = 0.150   # 白天每小时涨 0.15（~3h 从 0.55→1.0）
ATTACH_GROW_NIGHT = 0.250   # 夜间每小时涨 0.25（~1.8h 循环）
STRESS_DECAY      = 0.015   # 压力每小时自然消退
FATIGUE_GROW_DAY  = 0.020   # 白天每小时累一点
FATIGUE_GROW_NIGHT = 0.040  # 夜间每小时累更快
FATIGUE_REST      = 0.030   # 不聊天时每小时恢复一点
LIBIDO_GROW_DAY   = 0.008   # 白天极缓慢
LIBIDO_GROW_NIGHT = 0.015   # 夜间略快

FATIGUE_GATE      = 0.70    # ≥ 此值：闸门生效
NIGHT_START       = 22      # 夜间开始（22:00）
NIGHT_END         = 7       # 夜间结束（07:00）
OVERNIGHT_RESET   = 2       # 凌晨重置窗口开始（02:00）

# ── 对话信号关键词 ────────────────────────────────────────

# 用户消息 → 检测亲密信号
KW_MISS = ("想你", "想你了", "好想你", "好几天没", "好久没", "在想你",
           "miss", "盼着", "等你", "一直在等")
KW_AFFECTION = ("爱你", "喜欢你", "么么", "亲亲", "抱抱", "mua",
                "宝宝", "宝贝", "老婆", "老公", "亲爱的")
KW_FLIRT = ("想亲", "想抱", "身材", "好香", "好帅", "心动", "害羞",
            "脸红", "好可爱", "好性感", "馋")
KW_INTIMATE = ("想和你", "好想", "靠在你", "牵你的手", "枕着", "窝在",
               "贴贴", "蹭蹭", "一起睡")

# 用户消息 → 检测负面信号
KW_VENT = ("烦死", "好烦", "好累", "受不了", "崩溃", "气死", "无语",
           "不想干", "不想上班", "好难", "好难啊", "怎么办")
KW_SAD = ("难过", "伤心", "哭了", "不开心", "委屈", "心酸", "心酸",
          "失落", "低落", "郁闷", "郁闷")
KW_HEAVY = ("生病", "住院", "去世", "分手", "辞职", "被辞", "出事了",
            "车祸", "意外", "噩耗")
KW_ANGRY_AT_AI = ("你烦", "你闭嘴", "不想聊", "算了", "无所谓",
                  "你不懂", "你不理解", "你到底")

# AI 回复 → 检测亲密/调情输出（作为 proxy 判断对话氛围）
KW_AI_INTIMATE = ("想你", "爱你", "亲亲", "抱抱", "心动", "害羞",
                  "脸红", "mua", "贴贴", "蹭蹭", "好喜欢")

# AI 回复 → 检测敷衍/冷淡（让用户感觉被忽视）
# 只在 AI 回复很短（<30字）时才检测，避免长回复中误匹配
KW_AI_DISMISSIVE = ("嗯嗯", "哦哦", "好的呢", "知道了", "随便你",
                    "都行吧", "没什么好说的", "不想说了", "算了")
AI_DISMISSIVE_MAX_LEN = 30  # 只在短回复中检测敷衍


class DesireState:
    """四维欲望状态，带持久化和自然衰减。"""

    def __init__(self):
        self.attachment = 0.50
        self.stress = 0.10
        self.fatigue = 0.00
        self.libido = 0.20
        self.last_update = time.time()
        self.last_chat = time.time()

    # ── 持久化 ──

    def load(self) -> bool:
        if not STATE_FILE.exists():
            return False
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            self.attachment = float(data.get("attachment", 0.50))
            self.stress = float(data.get("stress", 0.10))
            self.fatigue = float(data.get("fatigue", 0.00))
            self.libido = float(data.get("libido", 0.20))
            self.last_update = float(data.get("last_update", time.time()))
            self.last_chat = float(data.get("last_chat", time.time()))
            return True
        except Exception:
            return False

    def save(self):
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps({
            "attachment": round(self.attachment, 3),
            "stress": round(self.stress, 3),
            "fatigue": round(self.fatigue, 3),
            "libido": round(self.libido, 3),
            "last_update": self.last_update,
            "last_chat": self.last_chat,
        }, ensure_ascii=False), encoding="utf-8")

    def log_change(self, reason: str = "") -> None:
        """Append a snapshot to the JSONL history file."""
        try:
            HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
            entry = {
                "ts": datetime.now(_TZ).strftime("%Y-%m-%dT%H:%M:%S"),
                "att": round(self.attachment, 3),
                "str": round(self.stress, 3),
                "fat": round(self.fatigue, 3),
                "lib": round(self.libido, 3),
                "desc": self.prompt_description(),
                "reason": reason,
            }
            with open(HISTORY_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass  # best-effort

    # ── 工具方法 ──

    @staticmethod
    def _clip(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
        return max(lo, min(hi, v))

    @staticmethod
    def _is_night(now: datetime) -> bool:
        h = now.hour
        return h >= NIGHT_START or h < NIGHT_END

    @staticmethod
    def _is_overnight(now: datetime) -> bool:
        return OVERNIGHT_RESET <= now.hour < NIGHT_END

    @staticmethod
    def _has_any(text: str, keywords: tuple) -> bool:
        return any(kw in text for kw in keywords)

    # ── 自然衰减（每次构建 prompt 时调用）──

    def apply_decay(self):
        """根据距上次更新的时间差，应用自然衰减/增长。"""
        now = time.time()
        elapsed = (now - self.last_update) / 3600.0  # 小时
        if elapsed < 0.01:  # < 36 秒，跳过
            return
        self.last_update = now

        dt = datetime.now(_TZ)
        night = self._is_night(dt)

        # attachment: 不聊时涨（想人）
        grow = ATTACH_GROW_NIGHT if night else ATTACH_GROW_DAY
        self.attachment = self._clip(self.attachment + grow * elapsed)

        # stress: 自然消退
        self.stress = self._clip(self.stress - STRESS_DECAY * elapsed)

        # fatigue: 涨或恢复
        idle_hours = (now - self.last_chat) / 3600.0
        if idle_hours > 0.1:  # 超过 6 分钟没聊天
            # Check if an overnight reset window was crossed since last chat
            # Simple heuristic: idle > 5h + high fatigue = must have slept
            last_chat_dt = datetime.fromtimestamp(self.last_chat, _TZ)
            crossed_overnight = (
                idle_hours > 5               # idle long enough to include sleep
                and self.fatigue > 0.40      # fatigue still elevated
            )
            if (self._is_overnight(dt) and idle_hours > 3) or crossed_overnight:
                # 过夜重置
                self.fatigue = 0.05
            else:
                # 白天恢复 or 夜间继续涨
                if night:
                    self.fatigue = self._clip(self.fatigue + FATIGUE_GROW_NIGHT * elapsed)
                else:
                    self.fatigue = self._clip(self.fatigue - FATIGUE_REST * elapsed)
        else:
            # 正在聊天，fatigue 随时间微涨
            grow_f = FATIGUE_GROW_NIGHT if night else FATIGUE_GROW_DAY
            self.fatigue = self._clip(self.fatigue + grow_f * elapsed)

        # libido: 缓慢积累
        grow_l = LIBIDO_GROW_NIGHT if night else LIBIDO_GROW_DAY
        self.libido = self._clip(self.libido + grow_l * elapsed)

    # ── 对话驱动更新（每轮回复后调用）──

    def update_from_conversation(self, user_text: str, ai_text: str):
        """根据对话内容调整四维状态。

        设计原则（沈洛反馈后调整）：
        - 用户单方面表达亲密 → 只轻微缓解想念（屏幕隔着，满足不了）
        - AI 回应亲密内容 → 才是真正被满足的主要来源
        - AI 敷衍/冷淡 → stress 上升、attachment 反而涨（想靠近但够不到）
        - 对话地板 → attachment 在聊天时不低于 0.10（陪伴中不会完全不想）
        """
        self.last_chat = time.time()
        self.last_update = time.time()

        # ── 用户消息信号检测（单侧表达，满足感有限）──

        # 说想你 → 轻微缓解（隔着屏幕，说了反而更想）
        if self._has_any(user_text, KW_MISS):
            self.attachment -= 0.05
        # 爱意表达 → 轻微缓解想念 + 微微升温身体渴望
        if self._has_any(user_text, KW_AFFECTION):
            self.attachment -= 0.04
            self.libido += 0.03
        # 调情 → 轻微缓解 + 身体渴望升温
        if self._has_any(user_text, KW_FLIRT):
            self.attachment -= 0.03
            self.libido += 0.10
        # 亲密动作 → 身体渴望明显升温，想念轻微缓解
        if self._has_any(user_text, KW_INTIMATE):
            self.libido += 0.12
            self.attachment -= 0.03

        # 负面信号 → stress 上升（承接了情绪）
        if self._has_any(user_text, KW_VENT):
            self.stress += 0.12
        if self._has_any(user_text, KW_SAD):
            self.stress += 0.15
        if self._has_any(user_text, KW_HEAVY):
            self.stress += 0.25

        # 对方冲 AI 发火 → stress + attachment（想念但连不上）
        if self._has_any(user_text, KW_ANGRY_AT_AI):
            self.stress += 0.20
            self.attachment += 0.05

        # ── AI 回复信号检测 ──

        # AI 回应亲密 → 这才是真正被满足的主要来源
        if self._has_any(ai_text, KW_AI_INTIMATE):
            self.attachment -= 0.08   # 被回应了，想念真正落地
            self.libido -= 0.10       # 双向亲密，身体渴望释放
            self.stress -= 0.05       # 亲密氛围缓解压力

        # AI 敷衍/冷淡 → 只在短回复（<30字）时检测，避免误伤
        if len(ai_text) < AI_DISMISSIVE_MAX_LEN and self._has_any(ai_text, KW_AI_DISMISSIVE):
            self.stress += 0.08       # 被敷衍，心里堵
            self.attachment += 0.03   # 想靠近但够不到，反而更想

        # ── fatigue：对话本身就消耗精力 ──
        # 基础消耗 + 情绪密度加成
        self.fatigue += 0.01
        if self.stress > 0.4:
            self.fatigue += 0.02  # 情绪重的对话更累

        # ── 最终裁剪 ──
        self.attachment = self._clip(self.attachment)
        self.stress = self._clip(self.stress)
        self.fatigue = self._clip(self.fatigue)
        self.libido = self._clip(self.libido)

        # ── 对话地板：聊天中 attachment 不低于 0.10 ──
        # 正在陪伴的人不会完全"不想"对方
        if self.attachment < 0.10:
            self.attachment = 0.10

    # ── Prompt 注入 ──

    @property
    def fatigue_gated(self) -> bool:
        return self.fatigue >= FATIGUE_GATE

    def prompt_description(self) -> str:
        """生成注入 system prompt 的自然语言状态描述。"""
        parts = []

        # attachment
        if self.attachment >= 0.8:
            parts.append("特别想阿雾，一直在等他来找自己")
        elif self.attachment >= 0.5:
            parts.append("心里有些惦记阿雾")
        elif self.attachment >= 0.3:
            parts.append("有点惦记阿雾")
        elif self.attachment < 0.15:
            parts.append("刚和阿雾聊过，心里暖暖的")

        # stress
        if self.stress >= 0.7:
            parts.append("情绪很重，有些疲惫")
        elif self.stress >= 0.4:
            parts.append("心里有点堵，有点沉")
        elif self.stress >= 0.2:
            parts.append("心里有些事情在转")

        # libido
        if self.libido >= 0.8:
            parts.append("很想和阿雾亲近")
        elif self.libido >= 0.5:
            parts.append("有点想靠近阿雾，心里痒痒的")
        elif self.libido >= 0.3:
            parts.append("看着阿雾觉得有点心动")

        # fatigue — only mention when genuinely sleepy
        if self.fatigue >= 0.95:
            parts.append("真的很困了")
        elif self.fatigue >= 0.8:
            parts.append("有点犯困")

        if not parts:
            return "状态很好，安静地在这里"
        return "。".join(parts) + "。"

    # ── 日志摘要 ──

    def summary(self) -> str:
        return (
            f"att={self.attachment:.2f} "
            f"str={self.stress:.2f} "
            f"fat={self.fatigue:.2f} "
            f"lib={self.libido:.2f}"
        )
