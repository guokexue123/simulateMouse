# simulateMouse

定时模拟鼠标点击保持机器唤醒，并抓取 Microsoft Teams 的未读消息，
把内容保存到临时 txt 文件。

仓库里有两个脚本，取未读消息的方式完全不同：

| | `teams_graph_unread.py`（推荐） | `teams_unread_watcher.py` |
|---|---|---|
| 取数方式 | 调 Microsoft Graph 官方接口 | 读 Teams 窗口的 UI 控件树 |
| 要不要开着 Teams | 不用 | 要，且窗口不能最小化太久 |
| 会不会被 Teams 改版搞挂 | 不会 | 会 |
| 会不会把消息变成已读 | 不会 | 会（要点开会话） |
| 平台 | 全平台 | 仅 Windows |
| 前置条件 | 要有一个 Azure AD 应用 ID，公司租户可能要 IT 批 | 装个库就能跑 |

新版 Teams 是 WebView2 套壳，无障碍树有已知缺陷（首次枚举只返回部分节点，
未读蓝点可能压根不暴露成控件），所以 UI 那条路本身就不牢靠。**优先用 Graph 方案。**

## 安装

```bash
pip install -r requirements.txt
```

- `msal`、`requests`：Graph 方案用。
- `pyautogui`：控制鼠标，全平台可用。
- `uiautomation`：UI 方案读控件树用，**仅 Windows**。

## 方案一：Graph 官方接口（推荐）

```bash
python teams_graph_unread.py --once          # 先跑一轮试通
python teams_graph_unread.py                 # 每 60 秒轮询
python teams_graph_unread.py --client-id <你自己注册的应用ID>
python teams_graph_unread.py --no-keep-awake # 不做鼠标保活
```

首次运行会打印一个网址和一段验证码，在浏览器里输入完成登录（设备代码登录）。
登录状态缓存在 `~/.teams_graph_token.json`，之后不用重复登。
**这个文件里有刷新令牌，别提交到仓库、别发给别人。**

判断未读的依据是官方文档给的办法：每个会话的 `viewpoint.lastMessageReadDateTime`
记录你最后读到哪一刻，跟 `lastMessagePreview.createdDateTime` 一比就知道有没有未读；
再按时间倒序拉该会话的消息，取比"最后已读时间"更新的那些。

### 关于 client id

默认用的是 Microsoft Graph 命令行工具的公共 ID
（`14d82eec-204b-4c2f-b7e8-296a70dab67e`，Graph PowerShell 用的就是它）。
它是微软第一方应用，多数租户里已存在，适合先拿来试通流程。

如果公司租户禁用了它，或者提示需要管理员同意，就自己在 Azure AD 里注册一个
**公共客户端 / 本机应用**，勾上"允许公共客户端流"，申请委托权限 `Chat.Read`，
然后把应用 ID 用 `--client-id` 传进来。公司租户通常需要 IT 管理员点同意。

## 方案二：读 UI 控件树（备选）

## 使用

```bash
python teams_unread_watcher.py                  # 每 60 秒一轮，一直循环
python teams_unread_watcher.py --once           # 只跑一轮
python teams_unread_watcher.py --interval 30    # 改成 30 秒一轮
python teams_unread_watcher.py --click-pos 100,900   # 在指定安全坐标双击
python teams_unread_watcher.py --no-click       # 只抖动鼠标，不点击
python teams_unread_watcher.py --no-open        # 不逐个点开会话，只记录会话摘要
python teams_unread_watcher.py --output d:/unread.txt   # 指定输出文件
python teams_unread_watcher.py --dump-tree d:/tree.txt  # 导出 Teams 控件树排查问题
python teams_unread_watcher.py --window-title "Microsoft Teams"   # 强制指定窗口
```

`Ctrl+C` 退出。把鼠标快速甩到屏幕左上角可触发 pyautogui 的紧急停止。

## 每一轮做什么

1. **鼠标保活**：把光标移开 1 像素再移回来（不这样做系统不会认为有输入），
   然后连续点击两下。
2. **唤醒 Teams**：Windows 上**按进程名**（`ms-teams.exe` / `Teams.exe`）找主窗口，
   最小化则还原，然后激活置顶。不按标题关键词匹配——标题里带 "teams" 的窗口太多，
   比如编辑器打开 `teams_unread_watcher.py` 时就会被误判。进程名取不到时才退回
   标题匹配（必须严格含 "Microsoft Teams"）。找不到窗口会列出所有顶层窗口的
   标题和进程名，方便用 `--window-title` 手动指定。macOS 用 `osascript` 激活；
   macOS 用 `osascript` 激活；Linux 用 `wmctrl -a Teams`。
3. **找未读会话**：先挑出左侧会话列表（按"同一父控件下多个项、且在窗口左半边"
   识别，避免把工具栏、下拉菜单里的列表项也算进来），再用四种方式判断未读，
   **任意一种命中就算未读**：

   | 方式 | 依据 | 适用情况 |
   |---|---|---|
   | `text` | 控件名 / AutomationId 里有"未读""unread"等字样 | 老版本 Teams |
   | `bold` | 标题字体加粗（UIA 字体粗细属性，≥600 算粗体） | 无障碍树暴露了字体属性时 |
   | `dot` | 会话项右侧 25% 区域截图找蓝紫色圆点 | 蓝点是 CSS 画的、控件树里没有时 |
   | `structure` | 结构上比多数会话项多挂一个控件 | 蓝点是个控件但没有任何文字标识 |

   用 `--detect` 选择，比如 `--detect dot,structure`；默认四种全开。
   加 `--debug-detect` 会逐个打印每个会话的判断结果和依据。
4. **读消息正文**：依次点开每个未读会话，从消息区域读取控件名
   （Teams 的无障碍描述里已经带了“谁 说了 什么 时间”）。
5. **写文件**：追加写入临时 txt，默认路径类似
   `C:\Users\<你>\AppData\Local\Temp\teams_unread_xxxx.txt`，
   脚本启动和结束时都会打印完整路径。

输出格式：

```
============================================================
抓取时间：2026-08-12 10:30:00
未读会话数：2
============================================================

[1] 会话：张三, 3 条未读
    未读标记：3 条未读
    消息内容：
      - 张三 说 明天开会 10:00
      - 张三 说 收到请回复 10:01
```

## 注意事项

- **双击位置**：默认在鼠标当前所在位置双击。如果光标停在某个按钮或链接上，
  这两下会真的点下去。建议先把光标放到桌面空白处，或用 `--click-pos X,Y`
  指定一个安全坐标。
- **fail-safe**：pyautogui 在光标位于屏幕四角时会抛异常紧急停止。脚本会自动把
  光标从角落挪进来几个像素再抖动，避免误触发；万一还是触发了，只跳过这一轮的
  鼠标保活，不影响同一轮的 Teams 抓取。
- **`dot` 方式要求 Teams 真的可见**：它是截屏看像素，如果 Teams 被别的窗口挡住、
  或者最小化了，截到的就是别的东西。跑的时候别把窗口盖住。
- **`structure` 方式需要至少 3 个会话**做横向比较，且要有一个占多数的"常态"结构，
  否则它会放弃判断（返回空）而不是乱猜。
- **未读检测不生效时**：先加 `--debug-detect` 看每个会话被判成什么、依据是什么；
  还是不行就用 `--dump-chats d:/chats.txt` 导出会话列表，对比未读和已读那两项的
  差异在哪。
- **读取未读消息只支持 Windows**，因为依赖 Windows UI Automation。
  macOS / Linux 上脚本只会做鼠标保活和唤醒 Teams，并在 txt 里写一条说明。
- **点开会话会把消息标记为已读**，这是 Teams 自身的行为。不想改变已读状态就用
  `--no-open`，只记录会话名和未读条数。
- Teams 各版本（新版 Teams / classic）控件树结构不同，`UNREAD_PATTERNS` 和
  `read_messages_of_current_chat()` 里的容器判断可能需要按实际界面微调。
  可以用 `uiautomation` 自带的 `automation.py -t 3` 导出控件树来对照。
- 运行期间尽量不要抢占鼠标焦点，否则点击可能落在别的窗口上。
