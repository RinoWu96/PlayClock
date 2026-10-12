import ast
import re
from pathlib import Path

from playclock import i18n

SRC = Path(__file__).resolve().parent.parent / "playclock"
CJK = re.compile(r"[一-鿿]")
# Chinese that is data, not UI: title-matching regexes, the user-rules help text, legacy-name migration,
# the language picker's own "中文" label, and the i18n module itself.
ALLOWED_FILES = {"i18n.py", "rules.py"}
ALLOWED_LITERALS = {"中文", "yuzu 系", "Citra 系 (3DS)", "RetroArch（未识别游戏）", "鿿"}  # last: CJK range bound


def tr_calls(tree):
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "tr" and node.args):
            yield node.args[0]


def test_every_tr_key_has_english():
    missing = []
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for arg in tr_calls(tree):
            keys = []
            if isinstance(arg, ast.Constant):
                keys = [arg.value]
            elif isinstance(arg, ast.IfExp):  # tr("a" if x else "b")
                keys = [n.value for n in ast.walk(arg) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
            missing += [f"{path.name}: {k!r}" for k in keys if CJK.search(k) and k not in i18n.EN]
    assert not missing, "\n".join(missing)


def test_no_untranslated_chinese_in_ui():
    leftovers = []
    for path in SRC.rglob("*.py"):
        if path.name in ALLOWED_FILES:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        translated = {id(n) for arg in tr_calls(tree) for n in ast.walk(arg)}
        nav = {id(n) for node in ast.walk(tree) if isinstance(node, ast.Assign)
               and any(getattr(t, "id", None) == "NAV" for t in node.targets) for n in ast.walk(node)}
        # docstrings and regex patterns are not UI text
        docs = {id(node.body[0].value) for node in ast.walk(tree)
                if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)) and node.body
                and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant)}
        regex = {id(node.args[0]) for node in ast.walk(tree) if isinstance(node, ast.Call) and node.args
                 and getattr(node.func, "attr", None) in {"compile", "search", "match", "fullmatch", "sub"}}
        nav |= docs | regex
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant) and isinstance(node.value, str) and CJK.search(node.value)
                    and id(node) not in translated and id(node) not in nav
                    and node.value not in ALLOWED_LITERALS):
                leftovers.append(f"{path.name}:{node.lineno}: {node.value!r}")
    assert not leftovers, "\n".join(leftovers)


def test_nav_labels_translated():
    from playclock.app import MainWindow
    assert all(text in i18n.EN for _, text in MainWindow.NAV)


def test_formatting():
    try:
        i18n.set_language("en")
        assert i18n.fmt_dur(3 * 3600 + 5 * 60) == "3h 5m"
        assert i18n.fmt_dur(45 * 60) == "45 min"
        assert i18n.tr("{n} 款游戏", n=1) == "1 game" and i18n.tr("{n} 款游戏", n=3) == "3 games"
        assert i18n.tr("最近游玩##last") == "Last played"
        assert i18n.weekday(0) == "Mon"
        i18n.set_language("zh")
        assert i18n.fmt_dur(3 * 3600 + 5 * 60) == "3 小时 5 分"
        assert i18n.tr("最近游玩##last") == "最近游玩"
        assert i18n.tr("{n} 款游戏", n=3) == "3 款游戏"
    finally:
        i18n.set_language("zh")
