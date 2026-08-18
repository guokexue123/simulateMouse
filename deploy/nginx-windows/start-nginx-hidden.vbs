' 无黑框启动 nginx：把这个 vbs 的快捷方式丢进 shell:startup 就能开机自启（仍然不是服务）
Set ws = CreateObject("WScript.Shell")
ws.CurrentDirectory = "C:\nginx"
ws.Run "nginx.exe", 0, False
