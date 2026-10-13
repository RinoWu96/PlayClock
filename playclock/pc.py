"""PC games added by hand (non-Steam): a game's exe, matched by full path, is tracked like an emulator called "PC".

Stored per device in config.json as "pc_games": [{"path": "D:\\Games\\X\\x.exe", "name": "X"}], since the same game
lives at different paths on different PCs. Steam games are refused: their time comes from the Steam import, and
tracking them here as well would count it twice.
"""
import os
from pathlib import PureWindowsPath

PC = "PC"   # the "emulator" recorded for these sessions; platform_of("PC") is "PC" too

# folder names under a game's own folder that don't name the game (…\Elden Ring\Game\eldenring.exe)
GENERIC_DIRS = {"bin", "bin64", "bin32", "binaries", "win64", "win32", "wingdk", "x64", "x86", "game", "games",
                "release", "retail", "shipping", "build", "client", "program", "app", "data", "exe", "launcher"}


def norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


def is_steam_game(path: str) -> bool:
    return f"{os.sep}steamapps{os.sep}common{os.sep}" in norm(path)


def suggest_name(path: str) -> str:
    """Folder name of the game: the nearest parent that isn't a generic bin/x64/Game folder, else the exe name."""
    p = PureWindowsPath(path)
    for parent in p.parents:
        name = parent.name
        if not name or parent == parent.parent:
            break
        if name.lower() not in GENERIC_DIRS:
            return name
    return p.stem


def lookup(pc_games: list[dict] | None) -> dict[str, str]:
    """normalised exe path -> game name"""
    return {norm(g["path"]): g["name"] for g in pc_games or [] if g.get("path") and g.get("name")}
