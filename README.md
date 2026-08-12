# simulateMouse

定时模拟鼠标点击保持机器唤醒，并抓取 Microsoft Teams 桌面客户端里的未读消息，
把内容保存到临时 txt 文件。

## 安装

```bash
pip install -r requirements.txt
```

- `pyautogui`：控制鼠标，全平台可用。
- `uiautomation`：读取 Teams 界面上的未读会话，**仅 Windows**。

## 使用

```bash
python teams_unread_watcher.py                  # 每 60 秒一轮，一直循环
python teams_unread_watcher.py --once           # 只跑一轮
python teams_unread_watcher.py --interval 30    # 改成 30 秒一轮
python teams_unread_watcher.py --click-pos 100,900   # 在指定安全坐标双击
python teams_unread_watcher.py --no-click       # 只抖动鼠标，不点击
python teams_unread_watcher.py --no-open        # 不逐个点开会话，只记录会话摘要
python teams_unread_watcher.py --output d:/unread.txt   # 指定输出文件
```

`Ctrl+C` 退出。把鼠标快速甩到屏幕左上角可触发 pyautogui 的紧急停止。

## 每一轮做什么

1. **鼠标保活**：把光标移开 1 像素再移回来（不这样做系统不会认为有输入），
   然后连续点击两下。
2. **唤醒 Teams**：Windows 上找到 Teams 主窗口，最小化则还原，然后激活置顶；
   macOS 用 `osascript` 激活；Linux 用 `wmctrl -a Teams`。
3. **找未读会话**：遍历 Teams 窗口的 UI Automation 控件树，按控件名匹配
   “3 条未读”“2 unread”“new message”等中英文标记。
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
- **读取未读消息只支持 Windows**，因为依赖 Windows UI Automation。
  macOS / Linux 上脚本只会做鼠标保活和唤醒 Teams，并在 txt 里写一条说明。
- **点开会话会把消息标记为已读**，这是 Teams 自身的行为。不想改变已读状态就用
  `--no-open`，只记录会话名和未读条数。
- Teams 各版本（新版 Teams / classic）控件树结构不同，`UNREAD_PATTERNS` 和
  `read_messages_of_current_chat()` 里的容器判断可能需要按实际界面微调。
  可以用 `uiautomation` 自带的 `automation.py -t 3` 导出控件树来对照。
- 运行期间尽量不要抢占鼠标焦点，否则点击可能落在别的窗口上。
