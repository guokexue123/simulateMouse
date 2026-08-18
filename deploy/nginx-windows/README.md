# Windows PC 上用 nginx 部署静态前端（不装服务，配置放 conf.d）

目标：Windows 机器上跑一个 nginx 进程托管前端打包产物（dist），
**不注册 Windows 服务**，站点配置单独写在 `conf\conf.d\nginx.conf` 里，
主配置只留一行 `include`。

本目录里的文件直接可用：

| 文件 | 放到哪 | 作用 |
|---|---|---|
| `conf.d/nginx.conf` | `C:\nginx\conf\conf.d\nginx.conf` | 站点配置（改 root 和端口就能用） |
| `start-nginx.bat` | 任意位置 | 校验配置 + 启动（已在跑就 reload） |
| `stop-nginx.bat` | 任意位置 | 优雅停止，停不掉就强杀 |
| `reload-nginx.bat` | 任意位置 | 改完配置热加载 |
| `start-nginx-hidden.vbs` | 任意位置 | 无黑框启动，可做开机自启 |

---

## 一、下载安装 nginx

1. 打开 <https://nginx.org/en/download.html>，下载 **nginx/Windows** 的
   `nginx-1.xx.x.zip`（Stable version 那一栏）。
2. 解压到 **`C:\nginx`**。

   > 路径里**不要有空格、中文、括号**（`C:\Program Files\` 会踩坑）。

3. 解压后的目录长这样：

   ```
   C:\nginx
   ├─ conf\        配置文件（nginx.conf、mime.types…）
   ├─ contrib\
   ├─ docs\
   ├─ html\        默认站点，index.html / 50x.html 在这
   ├─ logs\        access.log / error.log（首次启动后才生成）
   ├─ temp\
   └─ nginx.exe
   ```

4. 验证能跑：打开 **CMD**，

   ```bat
   cd /d C:\nginx
   nginx -v
   ```

   输出 `nginx version: nginx/1.xx.x` 就算装好了。Windows 版是绿色包，
   到这一步已经"安装"完了，不需要 installer，也不需要注册服务。

---

## 二、放前端产物

把 `npm run build` 出来的 `dist` 整个目录拷到一个固定位置，比如：

```
C:\www\myapp\
├─ index.html
├─ favicon.ico
└─ assets\
   ├─ index-8f3a1c2d.js
   └─ index-2b7e9a11.css
```

注意 **`index.html` 必须直接在 `C:\www\myapp` 下**，不要多套一层 `dist\`，
否则后面配的 `root` 对不上会 403。

打包前记得确认前端的 base：
- Vite：`vite.config.js` 里 `base: '/'`（部署在根路径时）
- Vue CLI：`vue.config.js` 里 `publicPath: '/'`

如果要部署到子路径（比如 `http://ip:8080/admin/`），base 要改成 `/admin/`。

---

## 三、开启 conf.d 机制

nginx/Windows 默认**没有** `conf.d` 目录，也没有 include，需要自己加。

### 1. 建目录

```bat
mkdir C:\nginx\conf\conf.d
```

### 2. 改主配置 `C:\nginx\conf\nginx.conf`

用 **Notepad++ / VS Code** 打开（别用记事本，容易存成 GBK 或带 BOM）。

改两个地方：

**(a) 把自带的示例 server 注释掉**，免得跟你的站点抢端口：

```nginx
http {
    include       mime.types;
    default_type  application/octet-stream;

    sendfile        on;
    keepalive_timeout  65;

    # server {
    #     listen       80;
    #     server_name  localhost;
    #     ...
    # }

    include conf.d/*.conf;      # <<< 加这一行，放在 http 块的最后
}
```

**(b) 关键点**：`include conf.d/*.conf;` 必须写在 **`http { }` 里面**，
不能写在文件最外层，否则报 `"server" directive is not allowed here`。

`include` 的相对路径基准是**主配置所在目录**（`C:\nginx\conf`），
所以 `conf.d/*.conf` 展开后就是 `C:\nginx\conf\conf.d\*.conf`，
不是 `C:\nginx\conf.d\`。拿不准就写绝对路径，最省心：

```nginx
include C:/nginx/conf/conf.d/*.conf;
```

> **Windows 路径必须用正斜杠 `/` 或双反斜杠 `\\`**，
> 单个 `\` 会被当转义符，报 `invalid parameter`。

### 3. 写站点配置 `C:\nginx\conf\conf.d\nginx.conf`

把本目录的 `conf.d/nginx.conf` 拷过去，只改两处：

```nginx
listen 8080;              # 端口，80 被 IIS/Skype 占了就换
root   C:/www/myapp;      # 你的 dist 目录，正斜杠
```

完整内容见 `conf.d/nginx.conf`，里面已经带好：

- `try_files $uri $uri/ /index.html;` —— SPA 路由刷新不 404（**最容易漏的一条**）
- 静态资源 30 天缓存 + `index.html` 不缓存 —— 发版后不用教用户清缓存
- gzip 压缩
- `/api/` 反向代理到本机后端（不用就删掉那段）
- `charset utf-8` —— 中文文件名/内容不乱码

> **配置文件必须存成 UTF-8 无 BOM**。带 BOM 会报
> `unknown directive "﻿server"`（那个不可见的 `﻿` 就是 BOM）。
> VS Code 右下角选 "UTF-8"（不是 "UTF-8 with BOM"）。

---

## 四、启动

nginx/Windows 有个坑：**双击 `nginx.exe` 不可靠**，工作目录不对时会
`CreateFile() "…/conf/nginx.conf" failed`。一律用 CMD 从安装目录启动。

```bat
cd /d C:\nginx

nginx -t          :: 1. 先校验配置
start nginx       :: 2. 启动（start 让它脱离当前 CMD 窗口）
```

`nginx -t` 输出这两行才算通过：

```
nginx: the configuration file C:\nginx/conf/nginx.conf syntax is ok
nginx: configuration file C:\nginx/conf/nginx.conf test is successful
```

确认进程起来了：

```bat
tasklist /fi "imagename eq nginx.exe"
```

正常会看到 **2 个** `nginx.exe`：1 个 master + 1 个 worker
（Windows 上 `worker_processes` 只能是 1）。

浏览器打开 <http://localhost:8080> 应该就能看到页面了。

直接用本目录的脚本更省事：双击 `start-nginx.bat`，
它会先 `nginx -t`，配置错了就停下报错，不会把坏配置跑起来。

---

## 五、日常操作命令

全部要在 `C:\nginx` 目录下执行：

```bat
cd /d C:\nginx

nginx -t              :: 校验配置（改完必做）
nginx -s reload       :: 热加载，不断连接
nginx -s quit         :: 优雅停止（处理完当前请求再退）
nginx -s stop         :: 立即停止
nginx -V              :: 看编译参数和已启用模块
```

`-s quit` 卡住不退（Windows 上偶发）就强杀：

```bat
taskkill /f /im nginx.exe
```

对应脚本：`reload-nginx.bat`、`stop-nginx.bat`。

---

## 六、让局域网别人能访问

1. **放行防火墙端口**（管理员 CMD）：

   ```bat
   netsh advfirewall firewall add rule name="nginx-8080" dir=in action=allow protocol=TCP localport=8080
   ```

   或者：控制面板 → Windows Defender 防火墙 → 高级设置 → 入站规则 → 新建规则 → 端口 → TCP 8080 → 允许。

2. 查本机 IP：`ipconfig`，找 IPv4 地址，比如 `192.168.1.50`。

3. 同事访问 `http://192.168.1.50:8080`。

---

## 七、开机自启（仍然不装服务）

三选一，都不是 Windows 服务：

**A. 启动文件夹（最简单）**
1. `Win + R` → 输入 `shell:startup` → 回车
2. 把 `start-nginx-hidden.vbs` 的**快捷方式**拖进去

vbs 版本不会弹黑框。**限制：必须登录进桌面才会启动**，
锁屏/未登录状态不跑。

**B. 任务计划程序（不用登录也行）**
1. `Win + R` → `taskschd.msc`
2. 创建任务 → 常规：勾"不管用户是否登录都要运行"、"使用最高权限运行"
3. 触发器：新建 → 开始任务选"启动时"，延迟 30 秒（等网络起来）
4. 操作：启动程序 → 程序 `C:\nginx\nginx.exe`，**起始于填 `C:\nginx`**（这个必填，不填必挂）

**C. 手动**：每次要用时双击 `start-nginx.bat`。

---

## 八、排错速查

先看日志，答案基本都在里面：

```bat
type C:\nginx\logs\error.log
type C:\nginx\logs\site.error.log
```

| 现象 | 原因 / 解法 |
|---|---|
| `bind() to 0.0.0.0:80 failed (10013)` | 端口被占。`netstat -ano \| findstr :80` 查 PID，`tasklist \| findstr <PID>` 看是谁（常见 IIS、Skype、SQL Reporting）。停掉它或换端口。 |
| `unknown directive "﻿server"` | 配置文件带 BOM，用 VS Code 另存为 UTF-8（无 BOM）。 |
| `invalid parameter` / 路径报错 | Windows 路径用了单反斜杠，改成 `C:/www/myapp`。 |
| **403 Forbidden** | ① `root` 目录下没有 `index.html`（多套了一层 `dist`）；② 目录权限不够，给 `Users` 读权限；③ 目录里确实没有索引文件。 |
| **404，但首页正常，一刷新就 404** | SPA 路由问题，`location /` 里少了 `try_files $uri $uri/ /index.html;`。 |
| 页面白屏，控制台 404 找不到 js/css | 前端打包 `base`/`publicPath` 配错，要跟部署路径一致。 |
| 改了配置没生效 | 忘了 `nginx -s reload`；或者启动的是别处的 nginx.exe，`tasklist` 确认只有一份在跑。 |
| 中文文件名/内容乱码 | server 块里加 `charset utf-8;`。 |
| `nginx -s reload` 报找不到 pid | 当前目录不是 `C:\nginx`，先 `cd /d C:\nginx`。 |
| 双击 nginx.exe 一闪而过 | 正常现象（它是后台进程）；但如果 `tasklist` 里没有，说明配置错了，用 `nginx -t` 看报错。 |

---

## 九、Windows 版的已知限制

- `worker_processes` 只能是 **1**，写多了也没用（Windows 版不支持多 worker）。
- 单 worker 的 `worker_connections` 上限约 **1024**，并发能力远不如 Linux。
- 没有 `sendfile` 等零拷贝优化。

**结论**：内网工具站、演示环境、几十人的小规模访问完全够用；
对外的生产环境还是上 Linux。
