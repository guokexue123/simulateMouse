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
# 新版 Teams 的未读是“加粗 + 右侧蓝点”，标题文字里不带“未读”二字，
# 标记通常挂在会话项内部的子控件上（名称或 AutomationId 里带 unread / 新活动）。
# 所以这些模式要拿会话项**及其子控件**的文本去匹配，不能只看会话项自己的名字。
UNREAD_PATTERNS = [
    re.compile(r"(\d+)\s*条?\s*(?:未读|新消息|新通知)"),   # “3 条未读”“2条新消息”
    re.compile(r"未读", re.IGNORECASE),
    re.compile(r"新活动"),
    re.compile(r"新消息"),
    re.compile(r"(\d+)\s+unread", re.IGNORECASE),        # “3 unread”
    re.compile(r"\bunread\b", re.IGNORECASE),
    re.compile(r"\bnew (?:messages?|activity|notifications?)\b", re.IGNORECASE),
]

# Teams 客户端的进程名：新版 Teams 是 ms-teams.exe，classic 是 Teams.exe。
# 按进程名匹配比按窗口标题可靠得多——标题里带 "teams" 的窗口太多了
# （比如编辑器打开 teams_unread_watcher.py 时的标题）。
TEAMS_PROCESS_NAMES = {"ms-teams.exe", "teams.exe", "msteams.exe"}

# 进程名拿不到时的兜底：标题必须严格含 "Microsoft Teams"，或整个标题就是 "Teams"。
# 不能只匹配 "teams"，否则会命中任何文件名里带 teams 的窗口。
TEAMS_TITLE_PATTERN = re.compile(r"\bMicrosoft Teams\b|^Teams$", re.IGNORECASE)


def log(msg: str) -> None:
    stamp = dt.datetime.now().strftime("%H:%M:%S")
    print(f"[{stamp}] {msg}", flush=True)


# --------------------------------------------------------------------------- #
# 1. 鼠标操作
# --------------------------------------------------------------------------- #
def import_uiautomation():
    """导入 uiautomation，失败时打印真实原因和当前解释器路径。

    最常见的坑：pip 把包装到了 A 解释器，脚本却是用 B 解释器跑的。
    所以这里一定要把 sys.executable 打出来。
    """
    try:
        import uiautomation
        return uiautomation
    except ImportError as exc:
        log(f"导入 uiautomation 失败：{exc}")
        log(f"当前解释器：{sys.executable}")
        log(f"请用同一个解释器安装：\"{sys.executable}\" -m pip install uiautomation")
        return None
    except Exception as exc:                       # comtypes 初始化失败等非 ImportError 情况
        log(f"导入 uiautomation 时出错（包已安装但加载失败）：{type(exc).__name__}: {exc}")
        log(f"当前解释器：{sys.executable}")
        return None


def load_pyautogui():
    try:
        import pyautogui
    except ImportError as exc:
        log(f"导入 pyautogui 失败：{exc}")
        log(f"请用同一个解释器安装：\"{sys.executable}\" -m pip install pyautogui")
        return None
    pyautogui.FAILSAFE = True   # 鼠标甩到屏幕左上角可紧急中止
    pyautogui.PAUSE = 0.05
    return pyautogui


def wiggle_and_double_click(pyautogui, click: bool = True, pos: tuple[int, int] | None = None) -> None:
    """抖动鼠标 + 连续点击两下，用来保持机器唤醒状态。"""
    if pyautogui is None:
        return

    try:
        if pos is not None:
            x, y = pos
            pyautogui.moveTo(x, y, duration=0.1)
        else:
            x, y = pyautogui.position()
            width, height = pyautogui.size()
            # 光标贴着右/下边缘时往里抖，否则移出屏幕会踩到 fail-safe。
            dx = 1 if x < width - 2 else -1
            dy = 1 if y < height - 2 else -1
            # 光标正好在角落时先挪进来一点，再抖动。
            if (x <= 1 or x >= width - 2) and (y <= 1 or y >= height - 2):
                x, y = max(2, min(x + dx * 3, width - 3)), max(2, min(y + dy * 3, height - 3))
                pyautogui.moveTo(x, y, duration=0.05)
            # 移开 1 像素再移回来，系统才会认为“有输入”。
            pyautogui.moveTo(x + dx, y, duration=0.05)
            pyautogui.moveTo(x, y, duration=0.05)

        if click:
            pyautogui.click(x, y)
            time.sleep(0.12)      # 两次独立单击，而不是一次双击事件
            pyautogui.click(x, y)
            log(f"鼠标已在 ({x}, {y}) 连续点击两下")
        else:
            log(f"鼠标已在 ({x}, {y}) 抖动（未点击）")
    except Exception as exc:
        # 触发 fail-safe 或其它鼠标异常时，不能让整轮就此中断——
        # 后面读取 Teams 未读消息的部分和鼠标保活是相互独立的。
        log(f"鼠标操作失败（本轮跳过保活，继续抓取 Teams）：{type(exc).__name__}: {exc}")


# --------------------------------------------------------------------------- #
# 2. 唤醒 Teams 窗口
# --------------------------------------------------------------------------- #
def process_name_of(pid: int) -> str:
    """由进程 ID 取可执行文件名，取不到返回空串。"""
    import ctypes
    from ctypes import wintypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)
    ]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]

    handle = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(buf))
        if k32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value)
        return ""
    finally:
        k32.CloseHandle(handle)


def looks_like_teams(win, title_override: str | None) -> bool:
    """判断一个顶层窗口是不是 Teams 客户端。"""
    title = win.Name or ""
    if title_override:
        return title_override.lower() in title.lower()

    try:
        pname = process_name_of(win.ProcessId).lower()
    except Exception:
        pname = ""
    if pname:
        # 进程名可信，直接以它为准：标题带 teams 的其它程序不会被误判。
        return pname in TEAMS_PROCESS_NAMES
    # 拿不到进程名才退回标题匹配。
    return bool(TEAMS_TITLE_PATTERN.search(title))


def activate_teams_windows(auto, title_override: str | None = None):
    """Windows：找到 Teams 主窗口并激活置顶，返回窗口控件。"""
    for win in auto.GetRootControl().GetChildren():
        if win.ControlTypeName != "WindowControl":
            continue
        name = win.Name or ""
        if not looks_like_teams(win, title_override):
            continue
        try:
            if win.IsMinimize():
                win.Restore()
            win.SetActive(waitTime=0.5)
            win.SetFocus()
        except Exception as exc:                       # 激活失败不致命，仍可读取控件树
            log(f"激活 Teams 窗口时出现异常（继续尝试读取）：{exc}")
        try:
            pname = process_name_of(win.ProcessId)
        except Exception:
            pname = "?"
        log(f"已唤醒 Teams 窗口：{name}（进程 {pname}）")
        return win

    log("没有找到 Teams 窗口，请确认 Teams 客户端已经启动。")
    log("当前所有顶层窗口（标题 / 进程名）：")
    for win in auto.GetRootControl().GetChildren():
        if win.ControlTypeName != "WindowControl":
            continue
        title = (win.Name or "").strip()
        if not title:
            continue
        try:
            pname = process_name_of(win.ProcessId)
        except Exception:
            pname = "?"
        log(f"    {title}  /  {pname}")
    log("如果上面能看到 Teams，用 --window-title 指定标题里的关键词。")
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


def control_evidence(node, max_depth: int = 4) -> list[str]:
    """收集会话项自身及其子控件上所有可读的标识文本。

    未读蓝点没有文字，但承载它的控件往往在 Name / AutomationId / ClassName
    或无障碍描述里留下线索，所以这几处都要收。
    """
    parts: list[str] = []
    for sub, _depth in _iter_controls(node, max_depth=max_depth, max_nodes=200):
        for attr in ("Name", "AutomationId", "ClassName"):
            try:
                value = getattr(sub, attr, "") or ""
            except Exception:
                continue
            if value:
                parts.append(str(value))
        try:                                   # 无障碍描述里有时才写着“未读”
            legacy = sub.GetLegacyIAccessiblePattern()
            for value in (legacy.Description, legacy.Value):
                if value:
                    parts.append(str(value))
        except Exception:
            pass
    return parts


def find_unread_items(teams_win) -> list:
    """在 Teams 窗口里找出所有带未读标记的会话项。"""
    unread = []
    seen = set()
    for node, _depth in _iter_controls(teams_win):
        try:
            ctrl_type = node.ControlTypeName
            name = (node.Name or "").strip()
        except Exception:
            continue
        if ctrl_type not in ("ListItemControl", "TreeItemControl", "ButtonControl"):
            continue
        if not name:
            continue

        # 先看会话项自己的名字，再看子控件——蓝点的标记只会出现在后者。
        hit = _match_unread(name)
        if not hit:
            for evidence in control_evidence(node):
                hit = _match_unread(evidence)
                if hit:
                    break
        if not hit:
            continue
        if name in seen:
            continue
        seen.add(name)
        unread.append((node, name, hit))
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


def dump_chat_items(teams_win, path: str) -> None:
    """只导出会话列表项及其子控件的全部属性。

    未读检测不生效时用这个：在文件里找到那个确实有新消息的会话，
    看它比已读会话多出哪个子控件 / 多出哪个属性值，那就是未读标记。
    """
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f"会话列表导出时间：{dt.datetime.now():%Y-%m-%d %H:%M:%S}\n")
        fh.write(f"窗口：{teams_win.Name}\n")
        fh.write("每个会话项下面缩进的是它的子控件；未读标记通常藏在子控件里。\n\n")
        count = 0
        for node, _depth in _iter_controls(teams_win):
            try:
                if node.ControlTypeName not in ("ListItemControl", "TreeItemControl"):
                    continue
                name = (node.Name or "").strip()
            except Exception:
                continue
            if not name:
                continue
            count += 1
            matched = _match_unread(name) or "（自身名称无未读特征）"
            fh.write(f"[会话 {count}] {name}\n")
            fh.write(f"    自身名称匹配：{matched}\n")
            for sub, depth in _iter_controls(node, max_depth=4, max_nodes=200):
                if sub is node:
                    continue
                try:
                    parts = [f"type={sub.ControlTypeName}"]
                    for attr in ("Name", "AutomationId", "ClassName"):
                        value = getattr(sub, attr, "") or ""
                        if value:
                            parts.append(f"{attr}={value!r}")
                except Exception:
                    continue
                fh.write(f"    {'  ' * depth}{' '.join(parts)}\n")
            fh.write("\n")
    log(f"会话列表已导出（{count} 个会话）：{path}")


def dump_control_tree(teams_win, path: str) -> None:
    """把 Teams 窗口的控件树导出到文件，用来对照实际界面调匹配规则。

    不同 Teams 版本（新版 / classic）控件结构差别很大，未读检测不生效时，
    先看这棵树里未读会话到底长什么样，再改 UNREAD_PATTERNS。
    """
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f"控件树导出时间：{dt.datetime.now():%Y-%m-%d %H:%M:%S}\n")
        fh.write(f"窗口：{teams_win.Name}\n\n")
        count = 0
        for node, depth in _iter_controls(teams_win):
            try:
                name = (node.Name or "").strip()
                ctype = node.ControlTypeName
            except Exception:
                continue
            fh.write(f"{'  ' * depth}{ctype}: {name}\n")
            count += 1
    log(f"控件树已导出（{count} 个节点）：{path}")


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
        auto = import_uiautomation()
        if auto is None:
            log("无法读取 Teams 未读消息，本轮跳过。")
            return
        teams_win = activate_teams_windows(auto, args.window_title)
        if teams_win is None:
            return
        if args.dump_tree:
            dump_control_tree(teams_win, args.dump_tree)
        if args.dump_chats:
            dump_chat_items(teams_win, args.dump_chats)
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
    parser.add_argument("--window-title", default=None,
                        help="按标题关键词强制指定 Teams 窗口（默认按进程名 ms-teams.exe 匹配）")
    parser.add_argument("--dump-tree", default=None, metavar="PATH",
                        help="把 Teams 整棵控件树导出到文件")
    parser.add_argument("--dump-chats", default=None, metavar="PATH",
                        help="只导出会话列表项及其子控件属性，排查未读检测优先用这个")
    args = parser.parse_args()

    output_path = make_output_path(args.output)
    pyautogui = load_pyautogui()

    log(f"启动：间隔 {args.interval} 秒，输出文件 {output_path}")
    log(f"当前解释器：{sys.executable}")
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
