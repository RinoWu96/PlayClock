"""Minimal i18n: source strings are Chinese; EN maps them to English.

tr("中文 {n}", n=3) -> the active language's text, formatted.
- A key may carry a context suffix "文本##ctx" when one Chinese text needs two different English texts;
  the Chinese display drops the suffix.
- An English value may be a (singular, plural) tuple, chosen by the `n` keyword.
"""
import time

LANG = "zh"
LANGS = ("zh", "en")


def set_language(lang: str) -> None:
    global LANG
    LANG = lang if lang in LANGS else "zh"


def resolve(setting: str) -> str:
    """'auto' -> the system UI language (Chinese or English)."""
    if setting in LANGS:
        return setting
    from PySide6.QtCore import QLocale
    return "zh" if QLocale.system().language() == QLocale.Chinese else "en"


def tr(key: str, **kw) -> str:
    if LANG == "en":
        text = EN.get(key, key.split("##")[0])
        if isinstance(text, tuple):
            text = text[0] if kw.get("n") == 1 else text[1]
    else:
        text = key.split("##")[0]
    return text.format(**kw) if kw else text


# --------------------------------------------------------------------------- formatting


def fmt_dur(sec: float, short: bool = False) -> str:
    sec = int(sec or 0)
    h, m = sec // 3600, sec % 3600 // 60
    if short:
        return f"{h}h {m:02d}m" if h else f"{m}m"
    if LANG == "en":
        if h:
            return f"{h}h {m}m" if m else f"{h}h"
        if m:
            return f"{m} min"
        return f"{sec} sec" if sec else "0 min"
    if h:
        return f"{h} 小时 {m} 分" if m else f"{h} 小时"
    if m:
        return f"{m} 分钟"
    return f"{sec} 秒" if sec else "0 分钟"


def fmt_ago(ts: float) -> str:
    if not ts:
        return "—"
    d = time.time() - ts
    en = LANG == "en"
    if d < 120:
        return "just now" if en else "刚刚"
    if d < 3600:
        return f"{int(d // 60)} min ago" if en else f"{int(d // 60)} 分钟前"
    today, then = time.localtime(), time.localtime(ts)
    days = (time.mktime((today.tm_year, today.tm_mon, today.tm_mday, 0, 0, 0, 0, 0, -1))
            - time.mktime((then.tm_year, then.tm_mon, then.tm_mday, 0, 0, 0, 0, 0, -1))) // 86400
    if days <= 0:
        return f"{int(d // 3600)} h ago" if en else f"{int(d // 3600)} 小时前"
    if days == 1:
        return "yesterday" if en else "昨天"
    if days < 365:
        return f"{int(days)} days ago" if en else f"{int(days)} 天前"
    return time.strftime("%Y-%m-%d", then)


def weekday(i: int) -> str:
    """0 = Monday."""
    return ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")[i] if LANG == "en" else "周" + "一二三四五六日"[i]


# --------------------------------------------------------------------------- English table

EN = {
    # app / navigation / tray
    "游戏时长记录": "Game playtime tracker",
    "概览": "Overview",
    "游戏库": "Library",
    "历史记录": "History",
    "统计": "Stats",
    "设置": "Settings",
    "打开主界面": "Open PlayClock",
    "退出": "Quit",
    "正在游玩：{game}\n本次 {t}": "Playing: {game}\nThis session {t}",
    "等待游戏启动": "Waiting for a game",
    "●  正在记录": "●  Recording",
    "○  等待游戏启动": "○  Waiting for a game",
    "本次 {t}": "This session {t}",
    "检测出错：{e}": "Detection error: {e}",
    "已最小化到系统托盘，会继续在后台记录。右键托盘图标可以退出。":
        "PlayClock keeps tracking from the system tray. Right-click the tray icon to quit.",
    # menus and dialogs
    "查看详情": "View details",
    "设置封面…": "Set cover…",
    "重命名 / 合并…": "Rename / merge…",
    "删除全部记录": "Delete all records",
    "删除这条记录": "Delete this session",
    "为「{game}」选择封面": "Choose a cover for “{game}”",
    "图片": "Images",
    "无法读取这张图片。": "Couldn't read this image.",
    "重命名 / 合并": "Rename / merge",
    "「{game}」的新名称：\n输入已有游戏的名称即可把两者合并，以后识别到的同名游戏也会自动归入。":
        "New name for “{game}”:\nEnter an existing game's name to merge the two. "
        "Future detections of this name are merged automatically.",
    "确定删除「{game}」的全部游玩记录吗？\n此操作无法撤销。":
        "Delete all play records for “{game}”?\nThis can't be undone.",
    "确定删除这条记录吗？": "Delete this session?",
    "设置开机启动失败：{e}": "Couldn't change the startup setting: {e}",
    "规则文件有错误，未加载：\n{e}": "The rules file has an error and was not loaded:\n{e}",
    "已加载 {n} 条规则。": ("Loaded {n} rule.", "Loaded {n} rules."),
    "已导出 {n} 条记录。": ("Exported {n} session.", "Exported {n} sessions."),
    "自定义规则文件有错误，本次只使用内置规则：\n{path}\n\n{e}":
        "Your custom rules file has an error, so only the built-in rules are used this time:\n{path}\n\n{e}",
    # widgets
    "正在游玩 · {emu}": "Now playing · {emu}",
    "累计 {t}": "{t} in total",
    "本次游玩": "This session",
    "现在没有在玩游戏": "Not playing anything right now",
    "在模拟器里启动游戏后会自动开始计时，无需任何操作。\n关闭窗口后程序会留在系统托盘继续记录。":
        "Start a game in a supported emulator and tracking begins automatically.\n"
        "Closing this window keeps PlayClock running in the system tray.",
    "● 游玩中": "● Playing",
    "这段时间还没有记录": "Nothing recorded in this period",
    "暂无数据": "No data yet",
    # home
    "今天": "Today",
    "本周": "This week",
    "本月": "This month",
    "累计": "All time",
    "最近游玩": "Recently played",
    "查看全部游戏": "View all games",
    "近 14 天": "Last 14 days",
    "夜深了": "Up late",
    "早上好": "Good morning",
    "中午好": "Good afternoon",
    "下午好": "Good afternoon",
    "晚上好": "Good evening",
    "比昨天多 {t}": "{t} more than yesterday",
    "比昨天少 {t}": "{t} less than yesterday",
    "日均 {t}": "{t} per day",
    "{n} 款游戏": ("{n} game", "{n} games"),
    "还没有任何记录。启动模拟器玩一会儿，这里就会出现你的游戏。":
        "Nothing recorded yet. Play something in an emulator and your games will show up here.",
    "合计 {t} · 日均 {a}": "{t} total · {a} per day",
    # library
    "最多时长": "Most played",
    "最近游玩##sort": "Recent",
    "名称": "Name",
    "搜索游戏": "Search games",
    "{n} 款游戏 · 合计 {t}": ("{n} game · {t} total", "{n} games · {t} total"),
    "没有匹配的游戏": "No matching games",
    "还没有记录到任何游戏": "No games recorded yet",
    # game detail
    "返回": "Back",
    "设置封面": "Set cover",
    "移除封面": "Remove cover",
    "删除记录": "Delete records",
    "游玩次数": "Sessions",
    "平均每次": "Avg. session",
    "首次游玩": "First played",
    "最近游玩##last": "Last played",
    "每日时长": "Daily playtime",
    "每月时长": "Monthly playtime",
    "{n} 天": ("{n} day", "{n} days"),
    "游玩记录": "Play sessions",
    "游戏": "Game",
    "模拟器": "Emulator",
    "开始": "Start",
    "结束": "End",
    "时长": "Duration",
    "时长(分钟)": "Duration (min)",
    "● 正在游玩  ·  ": "● Playing now  ·  ",
    "总游玩时长": "Total playtime",
    "{n} 次": ("{n} session", "{n} sessions"),
    # history
    "每一次游玩的详细时间": "Every play session in detail",
    "全部游戏": "All games",
    "显示最近 {n} 条": ("Showing the latest {n} session", "Showing the latest {n} sessions"),
    "（最多 500 条，可导出 CSV 查看全部）": " (up to 500 — export CSV to see all)",
    # stats
    "近 {n} 天": "{n} days",
    "近 12 个月": "12 months",
    "全部": "All",
    "总时长": "Total",
    "日均": "Daily avg.",
    "游玩天数": "Days played",
    "游戏数": "Games",
    "游戏排行": "Top games",
    "{n} 款": ("{n} game", "{n} games"),
    # settings
    "常规": "General",
    "语言": "Language",
    "跟随系统": "System",
    "开机时自动启动（最小化到托盘）": "Start with Windows (minimized to tray)",
    "只在模拟器窗口位于前台时计时": "Only count time while the emulator window is in the foreground",
    "模拟器窗口最小化时不计时。": "Time is never counted while the emulator window is minimized.",
    "计时": "Tracking",
    " 秒": " sec",
    "检测间隔": "Check every",
    "短于此时长的游玩不记录": "Ignore sessions shorter than",
    "中断多久内重开算同一次": "Continue a session if reopened within",
    "电脑睡眠或休眠期间的时间不会被计入。": "Time while the PC is asleep or hibernating is never counted.",
    "识别": "Detection",
    "自定义规则": "Custom rules",
    "重新加载规则": "Reload rules",
    "当前检测到的模拟器窗口（游戏名识别不准时，可对照这里的标题修改规则）：":
        "Emulator windows detected right now (if a game name comes out wrong, adjust the rules using these titles):",
    "数据": "Data",
    "打开数据文件夹": "Open data folder",
    "导出 CSV": "Export CSV",
    "关于": "About",
    "支持：": "Supported: ",
    "、": ", ",
    "\n内置规则随软件版本更新；自己添加或修改的规则保存在 rules.user.json，更新时不会被覆盖。":
        "\nBuilt-in rules update with the app; rules you add or change live in rules.user.json and are never overwritten.",
    "所有记录都只保存在本机：{path}": "All records stay on this PC: {path}",
    "通过读取模拟器窗口标题识别正在运行的游戏，不读取、不修改任何游戏文件。只有使用 SteamGridDB 封面功能时才会联网。":
        "Detects the running game from emulator window titles and never reads or modifies game files. "
        "It only goes online when you use SteamGridDB cover art.",
    "  [前台]": "  [foreground]",
    "  [最小化]": "  [minimized]",
    "（没有发现正在运行的已知模拟器）": "(No known emulator is running)",
    # covers (SteamGridDB)
    "封面": "Cover art",
    "在线搜索封面": "Find cover online",
    "在线搜索封面…": "Find cover online…",
    "本地图片": "From file",
    "断开": "Disconnect",
    "连接 SteamGridDB": "Connect SteamGridDB",
    "连接 SteamGridDB…": "Connect SteamGridDB…",
    "更换 API Key": "Change API key",
    "已连接 SteamGridDB，可以在游戏详情页在线搜索封面。":
        "Connected to SteamGridDB. Find covers online from any game's page.",
    "连接免费的 SteamGridDB 封面库后，可以在线搜索封面，并为新游戏自动配图。":
        "Connect the free SteamGridDB cover library to search covers online and add them to new games automatically.",
    "为新识别到的游戏自动下载封面": "Automatically download covers for newly detected games",
    "为没有封面的游戏补全封面": "Add covers to games that have none",
    "所有游戏都已经有封面了。": "Every game already has a cover.",
    "已为 {n} 款游戏补全封面。": ("Added a cover to {n} game.", "Added covers to {n} games."),
    "API Key 无效，请检查后重新填写。": "That API key isn't valid. Please check it and try again.",
    "网络错误：{e}": "Network error: {e}",
    "SteamGridDB 返回了无法识别的数据。": "SteamGridDB sent a response PlayClock couldn't read.",
    "SteamGridDB 返回了错误。": "SteamGridDB returned an error.",
    "SteamGridDB 是免费的社区游戏封面库，主机游戏也很全。连接后可以在线搜索封面，还能为新游戏自动配上封面。只需 3 步，大约 1 分钟。":
        "SteamGridDB is a free community library of game artwork with great console coverage. "
        "Once connected you can search covers online and new games get one automatically. "
        "It takes 3 steps and about a minute.",
    "打开网站": "Open website",
    "登录 SteamGridDB": "Sign in to SteamGridDB",
    "在网站右上角点「Login」，可以直接用 Steam 账号登录，完全免费。":
        "Click “Login” at the top right. You can sign in with your Steam account, and it's free.",
    "打开 API 页面": "Open API page",
    "生成 API Key": "Generate an API key",
    "登录后再点一次「打开 API 页面」（没登录时会跳到登录页），点击「Generate API Key」，复制生成的密钥。也可以在网站右上角头像 → Preferences → API 找到。":
        "Once signed in, click “Open API page” again (it shows the login page until you sign in), click “Generate API Key” and copy the key. You can also get there from your avatar at the top right → Preferences → API.",
    "粘贴到这里": "Paste it here",
    "密钥只保存在这台电脑上，不会上传到别处。": "The key is stored only on this PC and isn't sent anywhere else.",
    "粘贴 API Key": "Paste API key",
    "粘贴": "Paste",
    "显示": "Show",
    "搜索封面时会把游戏名发送给 SteamGridDB（中文、日文名还会发给维基百科查英文名）；不使用封面功能时 PlayClock 不会联网。":
        "Searching for covers sends game names to SteamGridDB (and Chinese/Japanese names to Wikipedia to look up "
        "the English title). PlayClock doesn't go online otherwise.",
    "验证并保存": "Verify and save",
    "取消": "Cancel",
    "请先粘贴 API Key。": "Paste your API key first.",
    "正在验证…": "Verifying…",
    "搜索": "Search",
    "正在搜索…": "Searching…",
    "没有找到匹配的游戏，换个名字试试（英文名通常更容易搜到）。":
        "No matching games. Try another name (English titles usually work best).",
    "选择左侧的游戏，再点击喜欢的封面。": "Pick a game on the left, then click the cover you like.",
    "已按英文名「{name}」搜索。": "Searched by the English title “{name}”.",
    "正在加载封面…": "Loading covers…",
    "共 {n} 张封面，点击即可使用。": ("{n} cover. Click it to use it.", "{n} covers. Click one to use it."),
    "这个游戏还没有横版封面，试试其他搜索结果。": "This game has no landscape covers yet. Try another result.",
    "加载中…": "Loading…",
    "正在下载封面…": "Downloading cover…",
    "下载失败。": "Download failed.",
    # history import
    "导入历史时长": "Import play history",
    "把模拟器自己记录的游玩时长导入 PlayClock，安装之前玩的时间也能算进统计。只补上 PlayClock 没记录到的部分，可以放心重复导入。":
        "Import the playtime your emulators recorded themselves, so time from before PlayClock counts too. "
        "Only time PlayClock doesn't already have is added, so importing again is safe.",
    "从 Ryujinx 导入…": "Import from Ryujinx…",
    "导入": "Import",
    "不再提示": "Don't show again",
    "在 Ryujinx 里发现 {n} 款游戏共 {t} 的游玩记录，导入后统计会更完整。":
        ("Ryujinx has {t} of play history for {n} game. Import it to complete your stats.",
         "Ryujinx has {t} of play history across {n} games. Import it to complete your stats."),
    "导入 {emu} 的游玩记录": "Import {emu} play history",
    "{emu} 自己也会记录每个游戏的累计时长。导入后，安装 PlayClock 之前玩的时间也能算进统计。只会补上 PlayClock 没记录到的部分，重复导入也不会重复计算。":
        "{emu} keeps its own playtime total for each game. Importing it brings time from before PlayClock into "
        "your stats. Only the part PlayClock didn't record is added, and importing again never double counts.",
    "选择其他文件夹…": "Choose another folder…",
    "选择 {emu} 数据文件夹": "Choose the {emu} data folder",
    "{emu} 记录": "{emu} total",
    "PlayClock 已记录": "Tracked by PlayClock",
    "将导入": "Will import",
    "没有在默认位置找到 {emu} 的数据。如果你用的是便携版，请选择它的文件夹。":
        "No {emu} data found in the usual place. If you use a portable install, choose its folder.",
    "数据来源：{path}": "Source: {path}",
    "共 {n} 款游戏，将导入 {t}。": ("{n} game, {t} to import.", "{n} games, {t} to import."),
    "这个文件夹里没有找到游玩记录。": "No play history found in this folder.",
    "已导入 {n} 款游戏的游玩记录。": ("Imported play history for {n} game.", "Imported play history for {n} games."),
    # Steam, platforms, manual time
    "平台": "Platform",
    "从 Steam 导入…": "Import from Steam…",
    "这台电脑上没有找到 Steam 的游玩记录。": "No Steam play history was found on this PC.",
    "以后自动同步这个账号的 Steam 时长": "Keep this account's Steam playtime in sync automatically",
    "检测到这台电脑上的 Steam，可以把 Steam 游戏的游玩时长一起导入。":
        "Steam is installed on this PC. Import your Steam playtime to see everything in one place.",
    "Steam 自动同步：已开启（账号 {acc}），Steam 记录有变化时会自动更新。":
        "Steam sync is on (account {acc}). PlayClock updates whenever Steam's records change.",
    "Steam 自动同步：未开启。导入 Steam 时可以选择开启。": "Steam sync is off. You can turn it on when importing from Steam.",
    "正在联网查询 {n} 个游戏的名字…": ("Looking up the name of {n} game online…", "Looking up the names of {n} games online…"),
    "另有 {n} 个应用查不到名字，已跳过。": ("{n} more app has no known name and was skipped.",
                                     "{n} more apps have no known name and were skipped."),
    "添加时长": "Add time",
    "添加游玩时长": "Add playtime",
    "记录在主机或其他设备上玩的时间，比如 Switch、PS5。时长可以对照主机「个人资料」或 PS App 里显示的数字填写。":
        "Record time played on consoles or other devices, like a Switch or PS5. "
        "Use the figure your console's profile page or the PS App shows.",
    "游戏名": "Game name",
    " 小时": " h",
    " 分钟": " min",
    "最后游玩日期": "Last played on",
    "添加": "Add",
    "请填写游戏名、平台和时长。": "Enter a game name, platform and duration.",
    # cloud sync (WebDAV / 坚果云)
    "云同步": "Cloud sync",
    "通过你自己的坚果云（或任意 WebDAV 网盘）在多台电脑之间同步游玩记录，不经过任何第三方服务器。封面图暂不同步，每台设备会各自补全。":
        "Sync your play records between PCs through your own Jianguoyun (Nutstore) or any WebDAV storage — no "
        "third-party server involved. Cover images aren't synced yet; each device fills in its own.",
    "未开启。": "Off.",
    "正在同步…": "Syncing…",
    "上次同步失败：{e}": "Last sync failed: {e}",
    "已连接 {user}，上次同步：{t}。": "Connected as {user}. Last synced {t}.",
    "已连接 {user}，等待首次同步。": "Connected as {user}. Waiting for the first sync.",
    "其他设备：{names}": "Other devices: {names}",
    "立即同步": "Sync now",
    "停止同步": "Stop syncing",
    "修改账号": "Change account",
    "连接坚果云…": "Connect Jianguoyun…",
    "连接坚果云": "Connect Jianguoyun",
    "停止云同步吗？本机和坚果云上的记录都会保留。":
        "Stop cloud sync? Records on this PC and in your Jianguoyun storage are kept.",
    "同步完成。": "Sync complete.",
    # PC games added by hand
    "PC 游戏": "PC games",
    "从正在运行的程序添加…": "Add from running programs…",
    "选择 exe…": "Choose exe…",
    "不在 Steam 里的电脑游戏：添加它的 exe 后，运行时会自动计时，平台记为 PC。Steam 游戏请用「从 Steam 导入」，不要在这里添加，否则会重复计算。":
        "PC games outside Steam: add a game's exe and it is timed automatically while it runs, as platform PC. "
        "Use \"Import from Steam\" for Steam games instead — adding them here would count their time twice.",
    "还没有添加 PC 游戏。": "No PC games added yet.",
    "移除": "Remove",
    "添加 PC 游戏": "Add PC game",
    "选择游戏的 exe": "Choose the game's exe",
    "程序 (*.exe)": "Programs (*.exe)",
    "这是 Steam 游戏：它的时长由「从 Steam 导入」获取，在这里再添加会重复计算。":
        "This is a Steam game: its time comes from \"Import from Steam\", so adding it here would count it twice.",
    "这个程序已经添加过了：「{game}」。": "This program is already added as \"{game}\".",
    "游戏名称：\n{path}\n可以选已有的游戏，时长会记在一起。":
        "Game name:\n{path}\nPick an existing game to add the time to it.",
    "没有找到可以添加的程序。请先启动游戏，再回来点这个按钮。":
        "No program to add. Start the game first, then come back and click this button.",
    "选择正在运行的游戏（先启动游戏再来选）：": "Choose the running game (start it first):",
    "不再为「{game}」自动计时吗？已有的游玩记录会保留。":
        "Stop timing \"{game}\" automatically? Its existing records are kept.",
    # Ryujinx save sync
    "存档同步（Ryujinx）": "Save sync (Ryujinx)",
    "立即同步存档": "Sync saves now",
    "在这台电脑和 Steam Deck 之间同步 Ryujinx 存档": "Sync Ryujinx saves between this PC and the Steam Deck",
    "和 Steam 云存档一样：关闭游戏后上传这个游戏的存档；云端有更新的存档时，在 Ryujinx 关闭的情况下自动恢复，恢复前先备份本机存档。两边都有新进度时不会自动覆盖，由你选择保留哪一份。":
        "Like Steam Cloud: a game's save is uploaded after you close it; a newer save in the cloud is restored "
        "automatically while Ryujinx is closed, after backing up the local one. When both devices have new "
        "progress, nothing is overwritten: you choose which one to keep.",
    "需要先开启上面的云同步。": "Turn on cloud sync above first.",
    "没有找到 Ryujinx 的存档文件夹。": "Ryujinx's save folder wasn't found.",
    "存档文件夹：{root}\n本月流量：上传 {up}，下载 {down}": "Save folder: {root}\nThis month: {up} up, {down} down",
    "正在同步存档…": "Syncing saves…",
    "上次存档同步失败：{e}": "Last save sync failed: {e}",
    "已同步": "In sync",
    "已上传": "Uploaded",
    "已从云端恢复": "Restored from the cloud",
    "已从 {device} 恢复": "Restored from {device}",
    "冲突：两边都有新进度": "Conflict: new progress on both devices",
    "冲突：另一台设备上传了不同的进度": "Conflict: another device uploaded different progress",
    "这台电脑还没有它的存档（先在 Ryujinx 里启动一次）": "No save on this PC yet (start it once in Ryujinx)",
    "云端有更新的存档，关闭 Ryujinx 后恢复": "Newer save in the cloud: restored when Ryujinx is closed",
    "待上传": "Waiting to upload",
    "选择保留哪一份…": "Choose which to keep…",
    "历史版本…": "History…",
    "历史版本": "History",
    "存档同步失败：{e}": "Save sync failed: {e}",
    "存档同步完成。": "Saves are in sync.",
    "存档操作失败：{e}": "Save operation failed: {e}",
    "已从云端恢复存档：{games}": "Saves restored from the cloud: {games}",
    "这些游戏在两台设备上都有新进度，请在 设置 → 存档同步 里选择保留哪一份：{games}":
        "These games have new progress on both devices. Choose which to keep under Settings → Save sync: {games}",
    "云端有更新的存档，关闭 Ryujinx 后会自动恢复：{games}":
        "Newer saves are in the cloud and will be restored once Ryujinx is closed: {games}",
    "云端有更新的存档还没恢复：{games}。请先关闭 Ryujinx，恢复后再开始游戏。":
        "Newer saves from the cloud aren't restored yet: {games}. Close Ryujinx first, then start the game.",
    "{device} 上的《{game}》还开着（可能在休眠）。先在 {device} 上退出游戏，否则两边进度会冲突。":
        "\"{game}\" is still open on {device} (maybe asleep). Quit it there first, or the two devices' progress "
        "will conflict.",
    "这台电脑": "This PC",
    "请先关闭 Ryujinx 再操作存档。": "Close Ryujinx first, then try again.",
    "《{game}》现在没有需要处理的冲突。": "\"{game}\" has no conflict to resolve now.",
    "云端当前的版本：{device}，{time}": "The cloud's current version: {device}, {time}",
    "{device} 的版本：{time}": "{device}'s version: {time}",
    "选择保留哪一份存档": "Choose which save to keep",
    "《{game}》在两台设备上都有新进度，只能保留一份。\n没选中的那份不会删除：本机的会留在备份里，也可以在「历史版本」里找回。":
        "\"{game}\" has new progress on both devices; only one can be kept.\nThe other isn't lost: this PC's stays "
        "in its backups, and versions can be brought back under History.",
    "云端 · {device} · {time}": "Cloud · {device} · {time}",
    "本机备份 · {time}": "Local backup · {time}",
    "《{game}》还没有历史版本。": "\"{game}\" has no earlier versions yet.",
    "用哪个版本替换《{game}》现在的存档？\n现在的存档会先备份。":
        "Which version should replace \"{game}\"'s current save?\nThe current save is backed up first.",
    "已恢复《{game}》的存档。": "\"{game}\"'s save was restored.",
    "手机查看": "On your phone",
    "开发中": "In development",
    "微信小程序（开发中）": "WeChat mini program (in development)",
    "以后可以在手机上查看正在玩的游戏、每日时长、游戏库和封面，数据来自你开启云同步后的坚果云。现在还不能使用。":
        "Later you'll be able to see what you're playing, daily time, your library and covers on your phone, "
        "from your Jianguoyun storage once cloud sync is on. It isn't available yet.",
    "微信扫码打开 PlayClock 小程序": "Scan with WeChat to open the PlayClock mini program",
    "在手机上查看正在玩的游戏、每日时长、游戏库和封面。先在上面开启云同步，再在小程序的「设置」里填同一个坚果云账号和应用密码（建议另外生成一个）。":
        "See what you're playing, daily time, your library and covers on your phone. Turn on cloud sync above, then "
        "enter the same Jianguoyun account and an app password (preferably a separate one) in the mini program's settings.",
    "小程序只读取坚果云里 PlayClock 文件夹的汇总文件，不修改任何数据。":
        "The mini program only reads the summary file in the PlayClock folder of your Jianguoyun storage and never changes anything.",
    "坚果云账号或应用密码不正确。": "The account or app password isn't correct.",
    "游玩记录会保存在你自己的坚果云里，在多台电脑之间自动同步。坚果云免费版的流量对 PlayClock 来说绰绰有余。只需 3 步。":
        "Your play records are kept in your own Jianguoyun (Nutstore) storage and sync between PCs automatically. "
        "The free plan is more than enough for PlayClock. It takes 3 steps.",
    "登录坚果云网页版": "Sign in to Jianguoyun on the web",
    "还没有账号的话可以免费注册。": "You can register for free if you don't have an account.",
    "打开坚果云": "Open Jianguoyun",
    "生成应用密码": "Create an app password",
    "点右上角的账户名 → 账户信息 → 安全选项 → 第三方应用管理 → 添加应用，名称填 PlayClock，生成一个「应用密码」。注意：这里不能填坚果云的登录密码。":
        "Click your account name at the top right → Account info → Security → Third-party apps → Add app, "
        "name it PlayClock and create an app password. Your login password won't work here.",
    "打开安全选项": "Open security settings",
    "填写账号和应用密码": "Enter your account and app password",
    "密码会加密保存在这台电脑上。": "The password is stored encrypted on this PC.",
    "坚果云登录邮箱或手机号": "Jianguoyun login email or phone number",
    "应用密码": "App password",
    "账号": "Account",
    "服务器": "Server",
    "使用其他 WebDAV 网盘（NAS、Nextcloud 等）时，把服务器改成它的 WebDAV 地址即可。":
        "For other WebDAV storage (a NAS, Nextcloud…), change the server to its WebDAV address.",
    "请填写账号和应用密码。": "Enter your account and app password.",
    "连接失败：{e}": "Couldn't connect: {e}",
    "网络连接失败，稍后会自动重试。": "Couldn't reach the server. PlayClock will try again later.",
    "无法连接服务器，请检查网络和服务器地址。": "Couldn't reach the server. Check your connection and the server address.",
    "从 Steam 等平台导入的历史时长没有具体日期，只计入总时长和排行，不计入趋势、日均和游玩天数。":
        "Imported history (from Steam and others) has no dates, so it counts toward totals and rankings "
        "but not the trend chart, daily average or days played.",
    # floating panel
    "游戏浮窗": "Game overlay",
    "游戏浮窗：置顶显示正在记录的游戏和计时": "Game overlay: keep the game being recorded and its timer on top",
    "Ctrl+Alt+P 随时显示 / 隐藏，游戏里也能用；浮窗可以拖动。独占全屏的游戏会挡住浮窗，请把游戏设成无边框窗口。":
        "Ctrl+Alt+P shows or hides it at any time, even in a game; drag it anywhere. Exclusive-fullscreen games "
        "cover it, so set the game to borderless windowed.",
    "快捷键 Ctrl+Alt+P 被其他程序占用了，请用托盘菜单或这里的开关显示浮窗。":
        "Another program already uses Ctrl+Alt+P. Use the tray menu or this switch to show the overlay.",
    "选择正在运行的游戏": "Pick a running game",
    "隐藏浮窗（Ctrl+Alt+P）": "Hide overlay (Ctrl+Alt+P)",
    "记录中": "Recording",
    "游戏已暂停，不计时": "Game paused, not counting",
    "游戏不在前台，暂停计时": "Game not in front, not counting",
    "未识别到游戏": "No game detected",
    "点 + 从正在运行的程序里选择": "Press + to pick from running programs",
    "PlayClock 认不出的游戏，选一次之后就会自动计时。": "For games PlayClock can't recognise: pick it once and it's timed automatically from then on.",
    "今天 {t}": "Today {t}",
    "缩小为横条（右键可切换模式和配色）": "Shrink to a bar (right-click for layout and colours)",
    "卡片模式": "Card",
    "横条模式（只显示计时）": "Bar (timer only)",
    "配色：封面颜色": "Colours: from the cover",
    "配色：深色": "Colours: dark",
    "已暂停": "Paused",
    "不在前台": "Not in front",
    "未识别游戏": "No game",
    "已把 {n} 个重复的游戏名（不同语言）合并到一起。": ("Merged {n} duplicate game name (another language) into one.",
                                         "Merged {n} duplicate game names (other languages) into one."),
}
