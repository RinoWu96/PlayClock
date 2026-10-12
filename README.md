# PlayClock · 模拟器游戏时长记录

托盘常驻，每隔几秒读取模拟器窗口标题识别正在运行的游戏，把游玩时长记到本地 SQLite。

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
| `playclock/importers.py` | 导入模拟器自带的时长记录（Ryujinx `games/*/gui/metadata.json`）；只补差额、可重复导入 |
| `playclock/ui/` | 主题、自绘组件（封面、卡片、图表）、各页面、对话框（SteamGridDB 引导、封面搜索、导入预览） |
| `playclock/app.py` | 主窗口、托盘、单实例、开机自启 |
