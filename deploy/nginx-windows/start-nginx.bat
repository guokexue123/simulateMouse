@echo off
rem 启动 nginx（不装服务，直接跑进程）
set NGINX_HOME=C:\nginx
cd /d %NGINX_HOME%
nginx -t
if errorlevel 1 (
    echo [ERROR] nginx config test failed, not starting.
    pause
    exit /b 1
)
tasklist /fi "imagename eq nginx.exe" | find /i "nginx.exe" >nul
if not errorlevel 1 (
    echo [INFO] nginx already running, reloading instead.
    nginx -s reload
    exit /b 0
)
start "" nginx.exe
echo [OK] nginx started at %NGINX_HOME%
