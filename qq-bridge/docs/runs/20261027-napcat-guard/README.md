# 2026-10-27 NapCat / QQ 稳定性修复

## 事实链
1. `NapCatWinBootMain.exe` 是看门狗：QQ 客户端退出 -> 立刻再拉一个。
2. 多启动器并存时每个启动器都带一套 QQ.exe 子进程 -> 9 启动器/36 QQ.exe -> 34 启动器/132 QQ.exe -> 内存耗尽。
3. 批量杀 QQ.exe 触发看门狗立即重启；杀启动器又丢掉唯一注入入口。
4. QQ 客户端未登录时退出 -> 看门狗重启 -> 循环堆积。
5. NapCat 面板登录不是明文 token：body 字段是 `hash` = sha256(token + ".napcat")。

## 关键错误（已修正）
第一版看门狗按“QQ.exe 的父进程不是启动器”判孤儿，把运行中的客户端连同子进程一起杀掉 -> 客户端退出 -> 看门狗再拉起 -> 重复实例。修正版以占用 6099 的进程为锚点保护整棵进程树。

## 交付
- `ops-dfy\tts\napcat_watch4.js`：常驻看门狗（45s/轮）。
- `ops-dfy\tts\napcat_clean_start2.ps1`：干净单实例启动（参数必须带引号，否则路径空格被截断，launcher 静默退出）。
- `ops-dfy\tts\napcat_qr_url.js`：取最新登录二维码链接。
- `ops-dfy\tts\napcat_wait_login.ps1`：等待 OneBot 3000/3001 上线并回读桥接日志。

## 待主人操作
用手机 QQ 扫二维码登录机器人号 <BOT_QQ>（或打开 http://127.0.0.1:6099 手动登录）。
