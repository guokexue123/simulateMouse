#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""定时模拟鼠标操作并抓取 Teams 未读消息。

每隔一段时间（默认 60 秒）执行一轮：

1. 轻微移动鼠标并连续点击两下，防止系统休眠 / 锁屏；
2. 唤醒（激活并置顶）桌面上的 Microsoft Teams 客户端；
3. 遍历 Teams 会话列表，找出所有带未读标记的会话并读取消息内容；
4. 把未读内容追加写入一个临时 txt 文件。

用法示例::

    python teams_unread_watcher.py                 # 每 60 秒循环一次
    python teams_unread_watcher.py --once          # 只跑一轮
    python teams_unread_watcher.py --interval 30   # 每 30 秒一轮
    python teams_unread_watcher.py --no-click      # 只抖动鼠标，不点击
    python teams_unread_watcher.py --output d:/unread.txt

依赖::

    pip install pyautogui
    pip install uiautomation      # 仅 Windows，读取未读消息需要

注意：默认在鼠标当前位置双击。如果光标停在某个按钮上，双击会真的按下去。
建议先把光标放到桌面空白处，或用 --click-pos X,Y 指定一个安全坐标，
或用 --no-click 关闭点击。
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import platform
import re
import subprocess
import sys
import tempfile
import time

IS_WINDOWS = platform.system() == "Windows"
IS_MAC = platform.system() == "Darwin"

# 会话列表里判断“未读”的关键词 / 模式，兼容中英文界面。
UNREAD_PATTERNS = [
    re.compile(r"(\d+)\s*条?\s*(?:未读|新消息)"),        # “3 条未读”“2条新消息”
    re.compile(r"未读", re.IGNORECASE),
    re.compile(r"(\d+)\s+unread", re.IGNORECASE),        # “3 unread”
    re.compile(r"\bunread\b", re.IGNORECASE),
    re.compile(r"\bnew messages?\b", re.IGNORECASE),
]

TEAMS_WINDOW_PATTERN = re.compile(r"Microsoft Teams|Teams(?:\s*\(work|classic\))?", re.IGNORECASE)


def log(msg: str) -> None:
    stamp = dt.datetime.now().strftime("%H:%M:%S")
    print(f"[{stamp}] {msg}", flush=True)


# --------------------------------------------------------------------------- #
# 1. 鼠标操作
# --------------------------------------------------------------------------- #
def load_pyautogui():
    try:
        import pyautogui
    except ImportError:
        log("未安装 pyautogui，鼠标操作被跳过（pip install pyautogui）")
        return None
    pyautogui.FAILSAFE = True   # 鼠标甩到屏幕左上角可紧急中止
    pyautogui.PAUSE = 0.05
    return pyautogui


def wiggle_and_double_click(pyautogui, click: bool = True, pos: tuple[int, int] | None = None) -> None:
    """抖动鼠标 + 连续点击两下，用来保持机器唤醒状态。"""
    if pyautogui is None:
        return

    if pos is not None:
        x, y = pos
        pyautogui.moveTo(x, y, duration=0.1)
    else:
        x, y = pyautogui.position()
        # 移开 1 像素再移回来，系统才会认为“有输入”。
        pyautogui.moveTo(x + 1, y, duration=0.05)
        pyautogui.moveTo(x, y, duration=0.05)

    if click:
        pyautogui.click(x, y)
        time.sleep(0.12)          # 两次独立单击，而不是一次双击事件
        pyautogui.click(x, y)
        log(f"鼠标已在 ({x}, {y}) 连续点击两下")
    else:
        log(f"鼠标已在 ({x}, {y}) 抖动（未点击）")


# --------------------------------------------------------------------------- #
# 2. 唤醒 Teams 窗口
# --------------------------------------------------------------------------- #
def activate_teams_windows():
    """Windows：找到 Teams 主窗口并激活置顶，返回窗口控件。"""
    import uiautomation as auto

    for win in auto.GetRootControl().GetChildren():
        if win.ControlTypeName != "WindowControl":
            continue
        name = win.Name or ""
        if not TEAMS_WINDOW_PATTERN.search(name):
            continue
        try:
            if win.IsMinimize():
                win.Restore()
            win.SetActive(waitTime=0.5)
            win.SetFocus()
        except Exception as exc:                       # 激活失败不致命，仍可读取控件树
            log(f"激活 Teams 窗口时出现异常（继续尝试读取）：{exc}")
        log(f"已唤醒 Teams 窗口：{name}")
        return win

    log("没有找到 Teams 窗口，请确认 Teams 客户端已经启动。")
    return None


def activate_teams_mac() -> bool:
    try:
        subprocess.run(
            ["osascript", "-e", 'tell application "Microsoft Teams" to activate'],
            check=True, capture_output=True, timeout=15,
        )
        log("已唤醒 Teams（macOS）")
        return True
    except Exception as exc:
        log(f"唤醒 Teams 失败（macOS）：{exc}")
        return False


def activate_teams_linux() -> bool:
    try:
        subprocess.run(["wmctrl", "-a", "Teams"], check=True, capture_output=True, timeout=15)
        log("已唤醒 Teams（Linux / wmctrl）")
        return True
    except Exception as exc:
        log(f"唤醒 Teams 失败（Linux，需要安装 wmctrl）：{exc}")
        return False


# --------------------------------------------------------------------------- #
# 3. 读取未读消息（Windows UI Automation）
# --------------------------------------------------------------------------- #
def _match_unread(name: str) -> str | None:
    """名称命中未读特征时，返回命中的片段，否则返回 None。"""
    if not name:
        return None
    for pattern in UNREAD_PATTERNS:
        hit = pattern.search(name)
        if hit:
            return hit.group(0)
    return None


def _iter_controls(root, max_depth: int = 14, max_nodes: int = 6000):
    """广度优先遍历控件树，带深度和节点数上限，避免在大树上卡死。"""
    import collections

    queue = collections.deque([(root, 0)])
    visited = 0
    while queue and visited < max_nodes:
        node, depth = queue.popleft()
        visited += 1
        yield node, depth
        if depth >= max_depth:
            continue
        try:
            children = node.GetChildren()
        except Exception:
            continue
        for child in children:
            queue.append((child, depth + 1))


def find_unread_items(teams_win) -> list:
    """在 Teams 窗口里找出所有带未读标记的会话项。"""
    unread = []
    seen = set()
    for node, _depth in _iter_controls(teams_win):
        try:
            ctrl_type = node.ControlTypeName
            name = node.Name or ""
        except Exception:
            continue
        if ctrl_type not in ("ListItemControl", "TreeItemControl", "ButtonControl"):
            continue
        hit = _match_unread(name)
        if not hit:
            continue
        key = name.strip()
        if key in seen:
            continue
        seen.add(key)
        unread.append((node, name.strip(), hit))
    return unread


def read_messages_of_current_chat(teams_win, limit: int = 30) -> list[str]:
    """读取当前打开会话的消息文本。

    Teams 把消息暴露成一组 ListItem / Group，控件名里通常已经带上
    “某某 说 内容 时间”这样的可访问性描述，直接取名称即可。
    """
    named_container = None      # 名称里带“消息 / message”的容器，优先级最高
    biggest_container = None    # 兜底：子控件最多的容器
    biggest_count = 0

    for node, _depth in _iter_controls(teams_win):
        try:
            if node.ControlTypeName not in ("ListControl", "GroupControl", "TableControl"):
                continue
            name = (node.Name or "").lower()
            children = node.GetChildren()
        except Exception:
            continue
        if not children:
            continue
        if named_container is None and any(
            k in name for k in ("message", "消息", "conversation", "会话")
        ):
            named_container = node
        if len(children) > biggest_count:
            biggest_container, biggest_count = node, len(children)

    container = named_container or biggest_container
    if container is None:
        return []

    messages: list[str] = []
    for child in container.GetChildren():
        try:
            text = (child.Name or "").strip()
        except Exception:
            continue
        if not text:
            # 名称为空时，向下再取一层子控件的文本拼起来。
            try:
                text = " ".join(
                    (g.Name or "").strip()
                    for g in child.GetChildren()
                    if (g.Name or "").strip()
                ).strip()
            except Exception:
                text = ""
        if text:
            messages.append(re.sub(r"\s+", " ", text))

    return messages[-limit:]


def collect_unread_windows(teams_win, open_each: bool = True) -> list[dict]:
    """返回 [{'chat': 会话标题, 'badge': 未读标记, 'messages': [...]}, ...]"""
    results = []
    unread_items = find_unread_items(teams_win)
    if not unread_items:
        log("没有检测到未读会话。")
        return results

    log(f"检测到 {len(unread_items)} 个未读会话。")
    for node, name, badge in unread_items:
        entry = {"chat": name, "badge": badge, "messages": []}
        if open_each:
            try:
                node.Click(simulateMove=False, waitTime=0.8)
                time.sleep(0.8)                       # 等消息区域渲染完
                entry["messages"] = read_messages_of_current_chat(teams_win)
            except Exception as exc:
                log(f"打开会话「{name}」失败：{exc}")
        results.append(entry)
    return results


# --------------------------------------------------------------------------- #
# 4. 写入临时文件
# --------------------------------------------------------------------------- #
def make_output_path(explicit: str | None) -> str:
    if explicit:
        return os.path.abspath(explicit)
    fd, path = tempfile.mkstemp(prefix="teams_unread_", suffix=".txt", text=True)
    os.close(fd)
    return path


def append_report(path: str, entries: list[dict], note: str | None = None) -> None:
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("=" * 60 + "\n")
        fh.write(f"抓取时间：{now}\n")
        if note:
            fh.write(f"说明：{note}\n")
        fh.write(f"未读会话数：{len(entries)}\n")
        fh.write("=" * 60 + "\n")
        for i, entry in enumerate(entries, 1):
            fh.write(f"\n[{i}] 会话：{entry['chat']}\n")
            fh.write(f"    未读标记：{entry['badge']}\n")
            if entry["messages"]:
                fh.write("    消息内容：\n")
                for msg in entry["messages"]:
                    fh.write(f"      - {msg}\n")
            else:
                fh.write("    （未能读取到消息正文，仅记录会话摘要）\n")
        fh.write("\n")
    log(f"未读内容已写入：{path}")


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def run_once(args, pyautogui, output_path: str) -> None:
    wiggle_and_double_click(pyautogui, click=not args.no_click, pos=args.click_pos)

    if IS_WINDOWS:
        try:
            import uiautomation  # noqa: F401
        except ImportError:
            log("未安装 uiautomation，无法读取 Teams 未读消息（pip install uiautomation）")
            return
        teams_win = activate_teams_windows()
        if teams_win is None:
            return
        entries = collect_unread_windows(teams_win, open_each=not args.no_open)
        if entries:
            append_report(output_path, entries)
    else:
        if IS_MAC:
            activate_teams_mac()
        else:
            activate_teams_linux()
        log("当前系统不支持自动读取 Teams 未读消息（该功能依赖 Windows UI Automation）。")
        append_report(
            output_path, [],
            note=f"{platform.system()} 上只执行了鼠标保活与唤醒 Teams，未读消息读取仅支持 Windows。",
        )


def parse_pos(value: str | None) -> tuple[int, int] | None:
    if not value:
        return None
    try:
        x, y = value.split(",")
        return int(x), int(y)
    except ValueError:
        raise argparse.ArgumentTypeError("--click-pos 格式应为 X,Y，例如 100,200")


def main() -> int:
    parser = argparse.ArgumentParser(description="定时模拟鼠标点击并抓取 Teams 未读消息")
    parser.add_argument("--interval", type=float, default=60.0, help="循环间隔秒数，默认 60")
    parser.add_argument("--once", action="store_true", help="只执行一轮后退出")
    parser.add_argument("--no-click", action="store_true", help="只抖动鼠标，不执行双击")
    parser.add_argument("--no-open", action="store_true", help="不逐个打开未读会话，只记录会话摘要")
    parser.add_argument("--click-pos", type=parse_pos, default=None, help="双击坐标 X,Y，默认在当前位置")
    parser.add_argument("--output", default=None, help="输出文件路径，默认自动生成临时 txt")
    args = parser.parse_args()

    output_path = make_output_path(args.output)
    pyautogui = load_pyautogui()

    log(f"启动：间隔 {args.interval} 秒，输出文件 {output_path}")
    if not args.no_click and args.click_pos is None:
        log("提示：将在鼠标当前位置双击，请先把光标移到空白区域，或用 --click-pos 指定坐标。")

    try:
        while True:
            try:
                run_once(args, pyautogui, output_path)
            except Exception as exc:                   # 单轮失败不中断整个循环
                log(f"本轮执行出错：{exc}")
            if args.once:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        log("收到 Ctrl+C，退出。")

    log(f"结果文件：{output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
