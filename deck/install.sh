#!/usr/bin/env bash
# PlayClock for Steam Deck — install as a user service (no root, survives SteamOS updates).
#   curl -fsSL https://raw.githubusercontent.com/RinoWu96/PlayClock/main/deck/install.sh | bash
set -euo pipefail

BRANCH="${PLAYCLOCK_BRANCH:-main}"
APP_DIR="$HOME/.local/share/playclock-deck"
UNIT_DIR="$HOME/.config/systemd/user"
SRC_URL="https://raw.githubusercontent.com/RinoWu96/PlayClock/$BRANCH/deck/playclock_deck.py"

mkdir -p "$APP_DIR" "$UNIT_DIR"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
if [ -n "$HERE" ] && [ -f "$HERE/playclock_deck.py" ]; then
    cp "$HERE/playclock_deck.py" "$APP_DIR/"
    cp "$HERE/../playclock/savesync.py" "$APP_DIR/"   # Ryujinx save sync, shared with the Windows app
else
    echo "Downloading PlayClock Deck ($BRANCH)…"
    curl -fsSL "$SRC_URL" -o "$APP_DIR/playclock_deck.py"
    curl -fsSL "https://raw.githubusercontent.com/RinoWu96/PlayClock/$BRANCH/playclock/savesync.py" \
        -o "$APP_DIR/savesync.py"
fi

# Ask for the WebDAV account once (reads from the terminal even when piped from curl).
python3 "$APP_DIR/playclock_deck.py" setup < /dev/tty

cat > "$UNIT_DIR/playclock-deck.service" <<'UNIT'
[Unit]
Description=PlayClock — records emulator play time

[Service]
ExecStart=/usr/bin/env python3 %h/.local/share/playclock-deck/playclock_deck.py run
Restart=always
RestartSec=15
Nice=10

[Install]
WantedBy=default.target
UNIT

systemctl --user daemon-reload
systemctl --user enable --now playclock-deck.service
echo
echo "PlayClock is running in the background, in Game Mode too."
echo "  status:   python3 $APP_DIR/playclock_deck.py status"
echo "  checks:   python3 $APP_DIR/playclock_deck.py doctor"
echo "  log:      journalctl --user -u playclock-deck -f"
echo "  remove:   bash $APP_DIR/uninstall.sh"
cp "${HERE:-.}/uninstall.sh" "$APP_DIR/" 2>/dev/null || \
    curl -fsSL "https://raw.githubusercontent.com/RinoWu96/PlayClock/$BRANCH/deck/uninstall.sh" -o "$APP_DIR/uninstall.sh" || true
