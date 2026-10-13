# PlayClock · 模拟器游戏时长记录

托盘常驻的游戏时长记录：读取模拟器窗口标题识别正在运行的游戏并计时；还可导入 Steam、Ryujinx 自带的时长记录，以及手动添加主机上的时长。数据全部保存在本地 SQLite。

工作原理、数据格式、统计口径和「怎么加一个新模拟器」见 [docs/HOW_IT_WORKS.md](docs/HOW_IT_WORKS.md)。推送代码后 GitHub Actions 会在 Windows 上自动跑测试。

- 运行：`dist\PlayClock\PlayClock.exe`（数据在它旁边的 `data\`：`playtime.db`、`rules.user.json`、`config.json`、`covers\`）
- 环境：`uv venv --python 3.11 .venv` 后 `uv pip install --python .venv\Scripts\python.exe -r requirements.txt`
- 开发：`.venv\Scripts\python.exe main.py`；测试：`.venv\Scripts\python.exe -m pytest -q tests`
- 打包：`build.bat`（输出到 `dist\PlayClock`；不要把 distpath 设成项目根目录——Windows 不区分大小写，会和 `playclock\` 源码包撞名）

## 代码结构

| 文件 | 作用 |
| --- | --- |
| `playclock/winapi.py` | ctypes 枚举可见顶层窗口 + 进程 exe 名 |
| `playclock/rules.py` | 内置规则 `DEFAULT_RULES`（随版本更新，加新模拟器改这里并在 `tests/test_core.py` 加标题样例）；用户规则在 `data/rules.user.json`，同名覆盖、`disabled` 停用 |
| `playclock/tracker.py` | 轮询、选中当前游戏、累计秒数（睡眠间隙不计、短会话丢弃、短中断续接） |
| `playclock/db.py` | 会话、别名（重命名/合并）、封面元数据、各类统计查询 |
| `playclock/i18n.py` | 界面中英文：源文本是中文，`EN` 表是英文；`tests/test_i18n.py` 会检查漏翻 |
| `playclock/covers.py` | SteamGridDB 客户端（用户自己的 API Key，QNetworkAccessManager 异步） |
| `playclock/steam.py` | 读本机 Steam 的时长（`localconfig.vdf`）和游戏名 / 类型（二进制 `appinfo.vdf`），封面用 Steam 横幅 |
| `playclock/sync.py` | 多设备同步：每台设备一个文件放在用户自己的 WebDAV（坚果云）上，按更新时间合并；密码用 DPAPI 加密 |
| `playclock/importers.py` | 导入模拟器自带的时长记录（Ryujinx `games/*/gui/metadata.json`）；只补差额、可重复导入 |
| `playclock/ui/` | 主题、自绘组件（封面、卡片、图表）、各页面、对话框（SteamGridDB 引导、封面搜索、导入预览） |
| `playclock/app.py` | 主窗口、托盘、单实例、开机自启 |

## 许可

PlayClock 自己的代码没有附带开源许可证，保留所有权利：可以查看和使用发布的程序，但未经许可不能复制、修改或再分发代码。打包的第三方组件（PySide6 / Qt，LGPL-3.0 等）见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
