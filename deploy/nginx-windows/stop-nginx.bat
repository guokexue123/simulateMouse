@echo off
rem 停止 nginx（先优雅退出，退不掉再强杀）
set NGINX_HOME=C:\nginx
cd /d %NGINX_HOME%
nginx -s quit
timeout /t 2 /nobreak >nul
tasklist /fi "imagename eq nginx.exe" | find /i "nginx.exe" >nul
if not errorlevel 1 (
    echo [WARN] graceful quit failed, killing nginx.exe
    taskkill /f /im nginx.exe
)
echo [OK] nginx stopped
