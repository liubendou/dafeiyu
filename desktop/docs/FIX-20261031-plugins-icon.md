# 2026-10-31 插件市场 + 图标 + 隐藏任务栏图标（0.4.7 / 0.4.8）

主人要求：AstrBot 有插件市场，把它做进大肥鱼桌面端；窗口图标换成给的那张图；再加「隐藏图标」。

## 一、图标

素材：主人给的 `微信图片_2026-10-22_000523_000.jpg`（1440×1441）。
处理：深色圆角底 + 白色圆角小托板 + 原图，避免浅色桌面上糊成一团。生成脚本
`C:\Windows\Temp\dfy-archive-20261031\dfy-icon.py`（真机 AstrBot 自带的 Pillow 跑的）。

落到 `desktop\assets\`：

| 文件 | 用途 |
|---|---|
| `icon.png` / `icon-512.png` | 512，主图 |
| `icon-256/128/64/48/32/16.png` | 各档尺寸 |
| `icon.ico` | 256/128/64/48/32/16 多尺寸，Windows 会自己挑 |
| `tray.png` / `tray-64.png` / `tray-16.png` | 托盘（原图直接缩，不加底板，托盘很小） |

主进程改动（`src/main/index.mjs`）：

- `ASSETS = path.join(ROOT, "assets")`，`assetPath(name)` 找不到就返回 null（不报错）；
- 窗口 `icon: assetPath("icon.png")`；托盘 `nativeImage.createFromPath(assetPath("tray.png"))`，取不到才退回原来的小圆点；
- `app.setAppUserModelId("Dafeiyu.Desktop")`，任务栏不再挂到 electron.exe 名下。

**打包坑**：`packaging/build_release.py` 的 `DEFAULT_INCLUDE` 里没有 `assets`，第一次打出来的 0.4.7
包里一个图标都没有（装的还是旧窗口）。已把 `"assets"` 加进清单，0.4.7/0.4.8 包里 `assets/` 12 个文件齐全。

## 二、隐藏任务栏图标

托盘右键菜单新增复选项「隐藏任务栏图标（只留托盘）」：

- 状态存 `%APPDATA%\dafeiyu-desktop\prefs.json`（`{"hideTaskbar": true}`），重启后保持；
- 勾选 → `win.setSkipTaskbar(true)`，窗口还在、托盘还在，任务栏那格消失；再点一次恢复；
- 默认不隐藏（`prefs.json` 不存在 = 不勾）。

## 三、AstrBot 插件市场

### 数据从哪来

- 注册表：`https://raw.githubusercontent.com/AstrBotDevs/AstrBot_Plugins_Collection/main/plugins.json`
  （AstrBot 面板自己用的那一份）。**实测 `api.soulter.top` 在这台机器上直连超时**，所以定死 GitHub raw。
- 响应是 gzip，且顶层是 `{plugin_id: {...}}` 的字典（不是列表），`market._fetch/_entries` 都处理了。
- 本机已装：扫 `C:\Users\<user>\astrbot\data\plugins\` 每个目录的 `metadata.yaml`，
  只取 `name/author/desc/version/repo` 五个标量，用正则读（不引 YAML 依赖）。
- 「已装」判定三路：`repo` 相同 / 目录名 / 显示名。

### 接口（新文件 `core\dfy_core\market.py`）

`astrbot_dir(cfg)`、`plugins_dir(cfg)`、`cache_path(cfg)`（=`runtime\plugins-market.json`）、
`installed(cfg)`、`market(cfg, refresh=False, ttl=600)`、`view(cfg, refresh=False, q="")`。

`GET /v1/plugins`（GUI 令牌；服务令牌 403，实测）→
`{items, count, installed, installed_count, installed_dir, market{at,source,age_s,error,refreshing}, registry, note}`；
`?refresh=1` 强刷、`?q=` 过滤。

### 刷新为什么要后台线程

2026-10-31 现场实测：这台机器下载注册表 **30–60 s**（一次 2 s 是赶巧），同步刷新会把窗口卡死。
所以改成：

- 有缓存时 `?refresh=1` 立刻返回旧数据 + `refreshing: true`，后台线程去拉（上限 120 s）；
- 页面每 3 s 轮询一次，最多 20 次，拉完自动换新；
- 一次缓存都没有的首屏才同步等，最多 12 s；
- 拉失败保留旧缓存并把原因写进 `market.error`，不编数据。

实测：`REFRESH 0.07s refreshing=True` → `DONE after 39.9s at=18:40:36`。

### 页面

`src/renderer/pages/PluginsPage.tsx`，左侧导航新增「插件」。搜索框（前端过滤 id/name/author/desc）、
「刷新注册表」、「打开 AstrBot 面板装」（走主进程新增的 `open-external`，只放行
`http(s)://127.0.0.1|localhost[:端口]`）。卡片显示名字、id、作者、已装版本、说明、仓库。

**一键安装没做**：装完必须重启 AstrBot 才生效，重启是主人的事，页面只把路指到
`http://127.0.0.1:6185/extension-marketplace`。

## 四、验证

- `dfy-gate7.ps1`：GATE OK — stage1 38 / send 26 / stage2 57 / arena 135 / stage3 112 /
  stage4 158 / watchdog 10 / arena_tools 58；tsc + vite 通过；accept 67 PASS / 0 FAIL / 6 NA。
- 打包：0.4.7 → 0.4.8 均 light 89 文件 / full 3064 文件，`assets/` 12 个全在。
- 安装：`guard: rollback\20261031-184202--0.4.7`，`current=0.4.8`，health 通过。
- 重启：单实例（先停干净旧窗口），`healthz=200`，核心 pid 1616。
- `/v1/plugins` 实测：`count=1332`、`installed_count=1`（`astrbot_plugin_dafeiyu_social` 0.1.0，作者「六本豆」）、
  中文搜索 `q=点名` → 8 条、服务令牌访问 → HTTP 403、刷新 0.07 s 返回 + 后台 39.9 s 完成。

## 五、没做的

- 市场内一键安装/更新（要动 AstrBot 插件目录并重启，按规矩不代按）；
- 分页：1332 条一次全给（约 470 KB JSON），目前靠前端过滤，条数再涨要加分页；
- `data\plugins.json`（AstrBot 自己的市场缓存）在这台机器上不存在，所以已装清单只能靠扫目录。
