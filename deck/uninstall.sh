#!/usr/bin/env bash
# Remove PlayClock for Steam Deck. Play records already synced to your WebDAV storage stay there.
set -uo pipefail

systemctl --user disable --now playclock-deck.service 2>/dev/null
rm -f "$HOME/.config/systemd/user/playclock-deck.service"
systemctl --user daemon-reload
rm -rf "$HOME/.local/share/playclock-deck"

read -r -p "Also delete this Deck's local records and settings (~/.local/share/playclock, ~/.config/playclock)? [y/N] " ans < /dev/tty
if [[ "$ans" =~ ^[Yy]$ ]]; then
    rm -rf "$HOME/.local/share/playclock" "$HOME/.config/playclock"
    echo "Removed, including local records."
else
    echo "Removed. Local records kept in ~/.local/share/playclock."
fi
