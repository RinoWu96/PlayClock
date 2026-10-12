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
    "模拟器游戏时长记录": "Emulator playtime tracker",
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
    "模拟器##section": "Emulators",
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
    "通过读取模拟器窗口标题识别正在运行的游戏，不读取、不修改任何游戏文件，不联网。":
        "Detects the running game from emulator window titles. "
        "It never reads or modifies game files and never goes online.",
    "  [前台]": "  [foreground]",
    "  [最小化]": "  [minimized]",
    "（没有发现正在运行的已知模拟器）": "(No known emulator is running)",
}
