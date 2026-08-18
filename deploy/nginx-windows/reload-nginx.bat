@echo off
rem 改完 conf.d\nginx.conf 后热加载，不断连接
set NGINX_HOME=C:\nginx
cd /d %NGINX_HOME%
nginx -t
if errorlevel 1 (
    echo [ERROR] nginx config test failed, keep running old config.
    pause
    exit /b 1
)
nginx -s reload
echo [OK] nginx reloaded
