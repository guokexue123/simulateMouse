#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""通过 Microsoft Graph 官方接口读取 Teams 未读消息，保存到临时 txt 文件。

和 teams_unread_watcher.py（读 Teams 窗口控件树）的区别：这个脚本不碰界面，
直接调官方接口，所以不要求 Teams 客户端开着、也不要求窗口在前台，
更不会因为 Teams 改版就失效。

判断未读的依据来自官方文档：每个会话的 viewpoint.lastMessageReadDateTime
记录了你最后读到哪一刻，把消息的 createdDateTime 跟它比，晚于它的就是未读。

首次运行会走设备代码登录（device code flow）：终端里给出一个网址和一段代码，
在浏览器里输入完成登录。之后 token 缓存在本地，不用每次都登。

用法::

    python teams_graph_unread.py --once
    python teams_graph_unread.py                    # 每 60 秒轮询一次
    python teams_graph_unread.py --client-id <你的应用ID>
    python teams_graph_unread.py --no-keep-awake    # 不做鼠标保活

依赖::

    pip install msal requests
    pip install pyautogui        # 只有需要鼠标保活时才用
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import sys
import tempfile
import time

GRAPH_ROOT = "https://graph.microsoft.com/v1.0"

# 读会话列表要 Chat.ReadBasic，读消息正文要 Chat.Read，取并集即可。
SCOPES = ["Chat.Read"]

# Microsoft Graph 命令行工具的公共客户端 ID（Graph PowerShell 用的就是它）。
# 它是微软自家的第一方应用，多数租户里已经存在，适合先拿来试通流程。
# 如果公司租户限制了它，就自己在 Azure AD 注册一个公共客户端应用，
# 把 client id 用 --client-id 传进来。
DEFAULT_CLIENT_ID = "14d82eec-204b-4c2f-b7e8-296a70dab67e"

# 从未读过的会话，viewpoint 里是这个值。
NEVER_READ = "0001-01-01T00:00:00Z"


def log(msg: str) -> None:
    print(f"[{dt.datetime.now():%H:%M:%S}] {msg}", flush=True)


def parse_graph_time(value: str | None) -> dt.datetime | None:
    """把 Graph 返回的 ISO 时间转成带时区的 datetime。"""
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def html_to_text(content: str, content_type: str) -> str:
    """消息正文可能是 html，转成纯文本。"""
    if not content:
        return ""
    if content_type != "html":
        return re.sub(r"\s+", " ", content).strip()
    text = re.sub(r"<br\s*/?>", "\n", content, flags=re.IGNORECASE)
    text = re.sub(r"</(?:div|p|li)>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)             # 去掉剩下的标签
    text = html.unescape(text)
    return re.sub(r"[ \t]+", " ", text).strip()


# --------------------------------------------------------------------------- #
# 认证与请求
# --------------------------------------------------------------------------- #
class GraphClient:
    """最小可用的 Graph 客户端：设备代码登录 + 带节流重试的 GET。"""

    def __init__(self, client_id: str, tenant: str, cache_path: str):
        try:
            import msal
            import requests
        except ImportError as exc:
            log(f"缺少依赖：{exc}")
            log(f'请用同一个解释器安装："{sys.executable}" -m pip install msal requests')
            raise SystemExit(1)

        self._requests = requests
        self._cache_path = cache_path
        self._cache = msal.SerializableTokenCache()
        if os.path.exists(cache_path):
            try:
                self._cache.deserialize(open(cache_path, encoding="utf-8").read())
            except Exception as exc:
                log(f"读取 token 缓存失败，将重新登录：{exc}")

        self._app = msal.PublicClientApplication(
            client_id,
            authority=f"https://login.microsoftonline.com/{tenant}",
            token_cache=self._cache,
        )

    def _save_cache(self) -> None:
        if not self._cache.has_state_changed:
            return
        try:
            with open(self._cache_path, "w", encoding="utf-8") as fh:
                fh.write(self._cache.serialize())
            if os.name == "posix":                  # 缓存里有刷新令牌，别让别人读到
                os.chmod(self._cache_path, 0o600)
        except Exception as exc:
            log(f"写入 token 缓存失败（不影响本次运行）：{exc}")

    def token(self) -> str:
        accounts = self._app.get_accounts()
        result = None
        if accounts:
            result = self._app.acquire_token_silent(SCOPES, account=accounts[0])

        if not result:
            flow = self._app.initiate_device_flow(scopes=SCOPES)
            if "user_code" not in flow:
                raise RuntimeError(f"发起设备登录失败：{json.dumps(flow, ensure_ascii=False)}")
            log("=" * 60)
            log(flow["message"])                    # 这句里含网址和验证码
            log("=" * 60)
            result = self._app.acquire_token_by_device_flow(flow)

        if "access_token" not in result:
            raise RuntimeError(
                f"获取 token 失败：{result.get('error')} - {result.get('error_description')}"
            )
        self._save_cache()
        return result["access_token"]

    def get(self, url: str, retries: int = 3) -> dict:
        """GET 一个 Graph 地址，处理 429/503 节流重试。"""
        if url.startswith("/"):
            url = GRAPH_ROOT + url
        for attempt in range(retries + 1):
            resp = self._requests.get(
                url, headers={"Authorization": f"Bearer {self.token()}"}, timeout=30
            )
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code in (429, 503, 504) and attempt < retries:
                wait = int(resp.headers.get("Retry-After", 2 ** (attempt + 1)))
                log(f"被限流（HTTP {resp.status_code}），{wait} 秒后重试")
                time.sleep(wait)
                continue
            raise RuntimeError(f"Graph 请求失败 HTTP {resp.status_code}：{resp.text[:400]}")
        raise RuntimeError("Graph 请求重试次数用尽")

    def get_all(self, url: str, max_pages: int = 20) -> list[dict]:
        """跟着 @odata.nextLink 翻页，返回合并后的 value 列表。"""
        items: list[dict] = []
        for _ in range(max_pages):
            payload = self.get(url)
            items.extend(payload.get("value", []))
            url = payload.get("@odata.nextLink")
            if not url:
                break
        return items


# --------------------------------------------------------------------------- #
# 业务逻辑
# --------------------------------------------------------------------------- #
def chat_display_name(chat: dict, me_id: str | None) -> str:
    """会话标题：群聊用 topic，单聊用对方的名字。"""
    topic = (chat.get("topic") or "").strip()
    if topic:
        return topic
    others = [
        (m.get("displayName") or "").strip()
        for m in chat.get("members", [])
        if m.get("userId") != me_id and (m.get("displayName") or "").strip()
    ]
    if others:
        return "、".join(others)
    return f"({chat.get('chatType', 'chat')} {chat.get('id', '')[:24]}…)"


def find_unread_chats(client: GraphClient, include_hidden: bool = False) -> list[dict]:
    """列出有未读的会话。

    官方判断方式：lastMessagePreview.createdDateTime 晚于
    viewpoint.lastMessageReadDateTime，说明最后一条消息还没读。
    """
    url = "/me/chats?$expand=lastMessagePreview,members&$top=50"
    chats = client.get_all(url)
    log(f"共取到 {len(chats)} 个会话")

    unread = []
    for chat in chats:
        viewpoint = chat.get("viewpoint") or {}
        if viewpoint.get("isHidden") and not include_hidden:
            continue

        preview = chat.get("lastMessagePreview") or {}
        last_msg_at = parse_graph_time(preview.get("createdDateTime"))
        if last_msg_at is None:
            continue                                # 空会话，没消息可读

        raw_read = viewpoint.get("lastMessageReadDateTime")
        last_read_at = parse_graph_time(raw_read)
        never_read = raw_read == NEVER_READ or last_read_at is None

        if never_read or last_msg_at > last_read_at:
            chat["_last_read_at"] = None if never_read else last_read_at
            chat["_never_read"] = never_read
            unread.append(chat)
    return unread


def fetch_unread_messages(client: GraphClient, chat: dict, max_messages: int) -> list[dict]:
    """取这个会话里晚于"最后已读时间"的消息。

    注意：/chats/{id}/messages 的 $filter 对 createdDateTime 只支持 lt，
    没法在服务端写 "gt 最后已读时间"，所以按时间倒序拉回来在本地截断。
    """
    chat_id = chat["id"]
    top = min(max_messages, 50)                     # Graph 限制 $top 最大 50
    url = f"/chats/{chat_id}/messages?$top={top}&$orderby=createdDateTime desc"
    payload = client.get(url)

    last_read_at = chat.get("_last_read_at")
    messages = []
    for msg in payload.get("value", []):
        created = parse_graph_time(msg.get("createdDateTime"))
        # 倒序遍历，遇到已读的就可以停了。
        if last_read_at is not None and created is not None and created <= last_read_at:
            break
        if msg.get("messageType") != "message":     # 跳过"某某加入了会话"这类系统事件
            continue
        if msg.get("deletedDateTime"):
            continue

        body = msg.get("body") or {}
        sender = ((msg.get("from") or {}).get("user") or {}).get("displayName")
        if not sender:
            app = (msg.get("from") or {}).get("application") or {}
            sender = app.get("displayName") or "未知发送者"
        messages.append({
            "time": created,
            "sender": sender,
            "text": html_to_text(body.get("content", ""), body.get("contentType", "text")),
        })
        if len(messages) >= max_messages:
            break

    messages.reverse()                              # 还原成时间正序，读起来顺
    return messages


# --------------------------------------------------------------------------- #
# 输出
# --------------------------------------------------------------------------- #
def make_output_path(explicit: str | None) -> str:
    if explicit:
        return os.path.abspath(explicit)
    fd, path = tempfile.mkstemp(prefix="teams_unread_", suffix=".txt", text=True)
    os.close(fd)
    return path


def append_report(path: str, entries: list[dict]) -> None:
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("=" * 60 + "\n")
        fh.write(f"抓取时间：{dt.datetime.now():%Y-%m-%d %H:%M:%S}\n")
        fh.write(f"数据来源：Microsoft Graph API\n")
        fh.write(f"未读会话数：{len(entries)}\n")
        fh.write("=" * 60 + "\n")
        for i, entry in enumerate(entries, 1):
            fh.write(f"\n[{i}] 会话：{entry['chat']}\n")
            fh.write(f"    未读条数：{len(entry['messages'])}"
                     f"{'（此会话从未读过，仅列最近若干条）' if entry['never_read'] else ''}\n")
            for msg in entry["messages"]:
                stamp = msg["time"].astimezone().strftime("%Y-%m-%d %H:%M") if msg["time"] else "?"
                fh.write(f"      [{stamp}] {msg['sender']}: {msg['text']}\n")
            if not entry["messages"]:
                fh.write("      （没取到正文，可能都是系统消息或已被撤回）\n")
        fh.write("\n")
    log(f"未读内容已写入：{path}")


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def run_once(client: GraphClient, args, output_path: str, me_id: str | None) -> None:
    unread_chats = find_unread_chats(client, include_hidden=args.include_hidden)
    if not unread_chats:
        log("没有未读会话。")
        return

    log(f"检测到 {len(unread_chats)} 个有未读的会话。")
    entries = []
    for chat in unread_chats:
        name = chat_display_name(chat, me_id)
        try:
            messages = fetch_unread_messages(client, chat, args.max_messages)
        except Exception as exc:
            log(f"读取会话「{name}」的消息失败：{exc}")
            messages = []
        log(f"  · {name}：{len(messages)} 条未读")
        entries.append({
            "chat": name,
            "messages": messages,
            "never_read": chat.get("_never_read", False),
        })
    append_report(output_path, entries)


def main() -> int:
    parser = argparse.ArgumentParser(description="用 Microsoft Graph 读取 Teams 未读消息")
    parser.add_argument("--client-id", default=DEFAULT_CLIENT_ID,
                        help="Azure AD 应用（公共客户端）ID，默认用 Graph 命令行工具的公共 ID")
    parser.add_argument("--tenant", default="organizations",
                        help="租户：organizations（默认，公司账号）/ common / 具体租户 ID")
    parser.add_argument("--interval", type=float, default=60.0, help="轮询间隔秒数，默认 60")
    parser.add_argument("--once", action="store_true", help="只跑一轮")
    parser.add_argument("--output", default=None, help="输出文件路径，默认自动生成临时 txt")
    parser.add_argument("--max-messages", type=int, default=30, help="每个会话最多取多少条，默认 30")
    parser.add_argument("--include-hidden", action="store_true", help="包含已隐藏的会话")
    parser.add_argument("--no-keep-awake", action="store_true", help="不做鼠标保活")
    parser.add_argument("--token-cache", default=os.path.expanduser("~/.teams_graph_token.json"),
                        help="token 缓存路径")
    args = parser.parse_args()

    output_path = make_output_path(args.output)
    log(f"启动：间隔 {args.interval} 秒，输出文件 {output_path}")

    client = GraphClient(args.client_id, args.tenant, args.token_cache)

    try:
        me = client.get("/me")
        me_id = me.get("id")
        log(f"已登录：{me.get('displayName')} <{me.get('userPrincipalName')}>")
    except Exception as exc:
        log(f"登录失败：{exc}")
        return 1

    keep_awake = None
    if not args.no_keep_awake:
        try:
            from teams_unread_watcher import load_pyautogui, wiggle_and_double_click
            keep_awake = (load_pyautogui(), wiggle_and_double_click)
        except ImportError:
            log("找不到 teams_unread_watcher.py，跳过鼠标保活")

    try:
        while True:
            if keep_awake and keep_awake[0] is not None:
                keep_awake[1](keep_awake[0], click=True)
            try:
                run_once(client, args, output_path, me_id)
            except Exception as exc:
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
