"""Emulator rules: which processes are emulators and how to get the game name from a window title.

Built-in rules (DEFAULT_RULES) ship with the program and update with it. The user's own rules live in
data/rules.user.json: a user rule with the same name as a built-in one replaces it, {"name": ..., "disabled": true}
turns a built-in rule off, and other user rules are added (checked before the built-in ones).
Each rule:
  name      emulator display name
  exe       list of process-name globs (case-insensitive), e.g. "duckstation*.exe"
  pattern   regex with a named group (?P<game>...); omitted -> the whole title is the game
  ignore    regexes; a title matching any of them is "no game running" (menus, settings...)
  fallback  label recorded when the emulator has a window but no game is recognised
            (null = don't record anything)
  platform  console the emulator plays (shown in the library instead of the emulator name)
"""
import fnmatch
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

# Sub-windows that are never a game when the whole title is used as the game name.
COMMON_IGNORE = [
    r"(?i)\b(settings?|configur\w*|properties|controllers?|about|cheats?|debugger|memory cards?|game list)\b",
    r"(设置|配置|属性|控制器|关于|金手指|调试)",
]

DEFAULT_RULES = [
    {"name": "Ryujinx", "platform": "Switch", "exe": ["ryujinx*.exe"],
     "pattern": r"^Ryujinx\b.*?\s[-|]\s+(?P<game>.+)$"},
    {"name": "yuzu & forks", "platform": "Switch", "exe": ["yuzu*.exe", "suyu*.exe", "sudachi*.exe", "citron*.exe", "eden*.exe", "torzu*.exe"],
     "pattern": r"^[^|]+\|\s*(?:v?\d+(?:\.\d+)+[\w\-]*\s*\|\s*)?(?P<game>[^|]+?)\s*\|"},
    {"name": "Citra & forks", "platform": "3DS", "exe": ["citra-qt*.exe", "citra*.exe", "lime3ds*.exe", "azahar*.exe", "mandarine*.exe"],
     # "Citra Nightly 1985 | Game"; build segments like "HEAD-1a2b3c4" or "2104" are skipped
     "pattern": r"^[^|]+(?:\|\s*(?:(?i:HEAD|master|nightly|canary)\S*|v?\d[\w.\-]*)\s*(?=\|))*\|\s*(?P<game>[^|]+?)\s*(?:\||$)",
     "ignore": [r"(?i)^[^|]+(?:\|\s*(?:HEAD|master|nightly|canary)\S*\s*)+$"]},
    {"name": "Dolphin", "platform": "GameCube / Wii", "exe": ["dolphin*.exe"],
     "pattern": r"^Dolphin[^|]*\|.*\|\s*(?P<game>[^|]+?)\s*$"},
    {"name": "PCSX2", "platform": "PS2", "exe": ["pcsx2*.exe"],
     "ignore": [r"(?i)^PCSX2\b"]},
    {"name": "DuckStation", "platform": "PS1", "exe": ["duckstation*.exe"],
     "ignore": [r"(?i)^DuckStation\b"]},
    {"name": "PPSSPP", "platform": "PSP", "exe": ["ppsspp*.exe"],
     "pattern": r"^PPSSPP[^-]*-\s*(?:[A-Z]{4}\d{5}\s*:\s*)?(?P<game>.+)$"},
    {"name": "RPCS3", "platform": "PS3", "exe": ["rpcs3*.exe"],
     "pattern": r"\|\s*(?P<game>[^|]+?\[[A-Z]{4}\d{5}\])\s*$"},
    {"name": "Cemu", "platform": "Wii U", "exe": ["cemu*.exe"],
     "pattern": r"\[TitleId:[^\]]*\]\s*(?P<game>.+)$"},
    {"name": "Vita3K", "platform": "PS Vita", "exe": ["vita3k*.exe"],
     "pattern": r"^Vita3K[^|]*\|\s*(?P<game>[^|]+?)\s*(?:\||$)"},
    {"name": "Xenia", "platform": "Xbox 360", "exe": ["xenia*.exe"],
     "pattern": r"^Xenia[^:]*:\s*(?P<game>.+?)(?:\s*<[^>]*>)?\s*$"},
    {"name": "melonDS", "platform": "NDS", "exe": ["melonds*.exe"],
     "pattern": r"^melonDS[^-]*-\s*(?P<game>.+)$"},
    {"name": "mGBA", "platform": "GBA", "exe": ["mgba*.exe"],
     "pattern": r"^mGBA - (?P<game>.+?)(?: - [\d.]+)?$", "ignore": [r"^mGBA - [\d.]+$"]},
    {"name": "Snes9x", "platform": "SNES", "exe": ["snes9x*.exe"],
     "pattern": r"^Snes9[xX][^-]*-\s*(?P<game>.+)$"},
    {"name": "Project64", "platform": "N64", "exe": ["project64*.exe"],
     "pattern": r"^(?P<game>.+?)\s*-\s*Project64"},
    {"name": "Mesen", "platform": "NES", "exe": ["mesen*.exe"],
     "pattern": r"^Mesen[^-]*-\s*(?P<game>.+)$"},
    {"name": "Flycast", "platform": "Dreamcast", "exe": ["flycast*.exe"],
     "pattern": r"^Flycast[^-]*-\s*(?P<game>.+)$"},
    {"name": "shadPS4", "platform": "PS4", "exe": ["shadps4*.exe"],
     "pattern": r"^shadPS4[^|]*\|\s*(?P<game>[^|]+?)\s*(?:\||$)"},
    {"name": "RetroArch", "platform": "RetroArch", "exe": ["retroarch*.exe"],
     "pattern": r"\|\|\s*(?P<game>[^|]+?)\s*(?:\|\||$)",
     "ignore": [r"(?i)\|\|\s*[\d.]+\s*fps"],
     "fallback": "RetroArch (unrecognized game)"},
]

_CLEANUP = [
    re.compile(r"\((?:64|32)-bit\)", re.I),
    re.compile(r"\(0[0-9A-Fa-f]{15}\)"),               # Switch title id
    re.compile(r"\[TitleId:[^\]]*\]", re.I),
    re.compile(r"\[[A-Z]{4}-?\d{5}\]"),                # PS serial in brackets
    re.compile(r"\([A-Z]{4}-?\d{5}\)"),                # PS serial in parens
    re.compile(r"\([A-Z0-9]{6}\)\s*$"),                # GameCube/Wii id (GALE01)
    re.compile(r"\[[A-Z]{2,3} v\d+\]"),                # Cemu region/version tag
    re.compile(r"\(?\b\d+(?:\.\d+)?\s*(?:fps|VPS)\)?", re.I),
    re.compile(r"FPS:\s*\d+(?:\.\d+)?", re.I),
    re.compile(r"\.(?:nds|gba|gbc|gb|nes|fds|sfc|smc|n64|z64|v64|iso|cso|chd|cue|bin|pbp|rvz|wbfs|gcm|gcz|wad|3ds|cci|cxi|cia|nsp|xci|nsz|xcz|elf|md|gen|sms|gg|pce|zip|7z)\s*$", re.I),
    re.compile(r"\s+v\d+(?:\.\d+)+\s*$"),              # trailing game version
]


def clean_title(s: str) -> str:
    for _ in range(2):
        for rx in _CLEANUP:
            s = rx.sub(" ", s)
        s = re.sub(r"\s+", " ", s).strip(" -|:·\t")
    return s


@dataclass
class Rule:
    name: str
    exe: list[str]
    pattern: re.Pattern | None = None
    ignore: list[re.Pattern] = field(default_factory=list)
    fallback: str | None = None
    platform: str | None = None

    @classmethod
    def from_dict(cls, d: dict) -> "Rule":
        return cls(
            name=d["name"],
            exe=[e.lower() for e in d["exe"]],
            pattern=re.compile(d["pattern"]) if d.get("pattern") else None,
            ignore=[re.compile(p) for p in d.get("ignore", [])]
            + ([] if d.get("pattern") else [re.compile(p) for p in COMMON_IGNORE]),
            fallback=d.get("fallback"),
            platform=d.get("platform"),
        )

    def matches_exe(self, exe: str) -> bool:
        return any(fnmatch.fnmatchcase(exe.lower(), g) for g in self.exe)

    def game_from_title(self, title: str) -> str | None:
        """Game name, or None if this title doesn't show a running game."""
        if any(rx.search(title) for rx in self.ignore):
            return None
        if self.pattern is None:
            game = clean_title(title)
        else:
            m = self.pattern.search(title)
            game = clean_title(m.group("game")) if m else ""
        return game or None


USER_TEMPLATE = {
    "说明": [
        "在 rules 里添加自己的识别规则；name 与内置规则相同则覆盖内置规则。",
        "停用某条内置规则：{\"name\": \"RetroArch\", \"disabled\": true}",
        "字段：name 名称；exe 进程名通配符列表；pattern 正则，用 (?P<game>...) 取出游戏名，省略则整个标题即游戏名；",
        "ignore 正则列表，匹配到的标题视为没在玩游戏；fallback 识别不到游戏名时记录的名称（可省略）。",
        "内置规则可参考同目录的 rules.builtin.json（只读参考，每次启动都会重新生成，改它不会生效）。",
    ],
    "help": [
        "Add your own detection rules under \"rules\". A rule with the same name as a built-in one replaces it.",
        "Disable a built-in rule: {\"name\": \"RetroArch\", \"disabled\": true}",
        "Fields: name; exe = list of process-name globs; pattern = regex whose (?P<game>...) group is the game name "
        "(omit it to use the whole title); ignore = regexes for titles that mean no game is running; "
        "fallback = name to record when no game name is found (optional).",
        "See rules.builtin.json next to this file for the built-in rules (reference only, regenerated on every start).",
    ],
    "rules": [],
}


def ensure_user_file(path: Path) -> None:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(USER_TEMPLATE, ensure_ascii=False, indent=2), encoding="utf-8")


def write_builtin_reference(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(DEFAULT_RULES, ensure_ascii=False, indent=2), encoding="utf-8")


def merge_rules(user: list[dict], builtin: list[dict] = DEFAULT_RULES) -> list[dict]:
    """User rules first (minus disabled markers), then built-ins not replaced or disabled by name."""
    names = {r["name"] for r in user}
    return [r for r in user if not r.get("disabled")] + [r for r in builtin if r["name"] not in names]


def platform_map(rules: list["Rule"]) -> dict[str, str]:
    """Emulator name -> platform. Names that aren't emulators (Steam, manual entries) map to themselves."""
    return {r.name: r.platform for r in rules if r.platform}


def load_rules(user_path: Path | None = None) -> list[Rule]:
    """Built-in rules merged with the user's file. Raises on a malformed user file."""
    user = []
    if user_path and user_path.exists():
        data = json.loads(user_path.read_text(encoding="utf-8"))
        user = data.get("rules", []) if isinstance(data, dict) else data
    return [Rule.from_dict(d) for d in merge_rules(user)]
