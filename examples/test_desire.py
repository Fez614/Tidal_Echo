#!/usr/bin/env python3
"""
test_desire.py — 模拟对话场景，验证欲望系统四维变化逻辑

跑一遍就能看出：
  - 不同对话类型对四维的影响
  - 关键词触发是否合理
  - 过夜重置、疲劳闸门、衰减曲线是否正常

用法: python test_desire.py
"""

import sys
import os
import time

# 确保能 import 同目录的 desire 模块
sys.path.insert(0, os.path.dirname(__file__))
import desire


def fresh_state():
    """创建一个干净的状态"""
    s = desire.DesireState()
    return s


def show(label, s, reason=""):
    desc = s.prompt_description()
    gate = " [闸门!]" if s.fatigue_gated else ""
    print(f"  {label}")
    print(f"    att={s.attachment:.3f}  str={s.stress:.3f}  "
          f"fat={s.fatigue:.3f}{gate}  lib={s.libido:.3f}")
    print(f"    → {desc}")
    if reason:
        print(f"    [{reason}]")
    print()


def simulate_conversation(s, user_text, ai_text, label=""):
    """模拟一轮对话"""
    before = (s.attachment, s.stress, s.fatigue, s.libido)
    s.update_from_conversation(user_text, ai_text)
    after = (s.attachment, s.stress, s.fatigue, s.libido)
    deltas = [after[i] - before[i] for i in range(4)]
    names = ["att", "str", "fat", "lib"]

    print(f"  💬 {label}")
    print(f"    你说: 「{user_text[:60]}」")
    print(f"    她说: 「{ai_text[:60]}」")
    print(f"    变化: ", end="")
    for n, d in zip(names, deltas):
        if abs(d) > 0.001:
            sign = "+" if d > 0 else ""
            print(f"{n}{sign}{d:.3f}  ", end="")
    print()
    show("结果", s)


def simulate_hours_pass(s, hours, label=""):
    """模拟时间流逝"""
    s.last_update = time.time() - hours * 3600
    s.apply_decay()
    show(f"⏰ {hours}小时后 {label}", s)


def main():
    print("=" * 60)
    print("欲望系统模拟测试")
    print("=" * 60)

    # ── 场景 1: 甜蜜撒娇 ──
    print("\n📍 场景 1: 甜蜜撒娇")
    print("-" * 40)
    s = fresh_state()
    show("初始", s)
    simulate_conversation(s,
        "好想你啊宝宝，一直在等你找我",
        "我也想你呀，过来让我抱抱",
        "想+亲密")

    simulate_conversation(s,
        "mua～亲亲你",
        "亲亲宝贝，好喜欢你",
        "撒娇+mua")

    # ── 场景 2: 吐槽工作 ──
    print("\n📍 场景 2: 吐槽倾诉")
    print("-" * 40)
    s = fresh_state()
    show("初始", s)
    simulate_conversation(s,
        "烦死了今天被领导骂了一顿，好累好崩溃",
        "怎么了？跟我说说发生什么了",
        "烦+累+崩溃")

    simulate_conversation(s,
        "就是那个项目的事，好难啊怎么办，感觉好委屈",
        "你已经做得很好了，别太委屈自己",
        "难+委屈")

    # ── 场景 3: 调情暧昧 ──
    print("\n📍 场景 3: 调情暧昧")
    print("-" * 40)
    s = fresh_state()
    show("初始", s)
    simulate_conversation(s,
        "你今天好帅啊，好心动，想和你贴贴",
        "那你过来呀，害羞什么",
        "帅+心动+贴贴")

    simulate_conversation(s,
        "想和你一起睡，靠在你肩膀上",
        "来吧，我搂着你，心跳好快",
        "亲密+一起睡")

    # ── 场景 4: 冲AI发火 ──
    print("\n📍 场景 4: 冲AI发火")
    print("-" * 40)
    s = fresh_state()
    show("初始", s)
    simulate_conversation(s,
        "你烦不烦啊，算了不想聊了，你根本不懂我",
        "对不起，我刚才没理解你的意思，你再说一次好吗",
        "发火+算了+不懂")

    # ── 场景 5: 日常闲聊 ──
    print("\n📍 场景 5: 日常闲聊（无关键词触发）")
    print("-" * 40)
    s = fresh_state()
    show("初始", s)
    simulate_conversation(s,
        "今天中午吃了个麻辣烫",
        "好吃吗？辣的还是微辣的",
        "纯日常")

    simulate_conversation(s,
        "还行吧，就是有点贵",
        "现在什么都贵，下次试试自己做",
        "纯日常")

    # ── 场景 6: 疲劳闸门测试 ──
    print("\n📍 场景 6: 疲劳闸门测试")
    print("-" * 40)
    s = fresh_state()
    s.fatigue = 0.65
    show("接近闸门(0.65)", s)
    simulate_conversation(s,
        "好累啊不想上班",
        "那就先摸鱼呗",
        "累+烦 → fatigue应该过闸门")
    show("闸门状态", s, f"fatigue={s.fatigue:.3f}, gate={'ON' if s.fatigue_gated else 'OFF'}")

    # ── 场景 7: 过夜重置 ──
    print("\n📍 场景 7: 过夜重置")
    print("-" * 40)
    s = fresh_state()
    s.fatigue = 0.75
    s.stress = 0.4
    s.last_chat = time.time() - 8 * 3600  # 8小时前
    show("聊到凌晨1点，fatigue高", s)
    simulate_hours_pass(s, 8, "睡到中午")

    # ── 场景 8: 连续对话衰减 ──
    print("\n📍 场景 8: 长时间不聊的自然衰减")
    print("-" * 40)
    s = fresh_state()
    s.stress = 0.5
    s.fatigue = 0.6
    s.last_chat = time.time() - 48 * 3600
    show("高stress+高fatigue", s)
    simulate_hours_pass(s, 24, "一天没聊")
    simulate_hours_pass(s, 24, "又过了一天")
    show("48小时后", s)

    print("=" * 60)
    print("测试完成")
    print("=" * 60)


if __name__ == "__main__":
    main()
