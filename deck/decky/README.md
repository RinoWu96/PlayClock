# PlayClock · Decky 插件（Steam Deck 快捷菜单）

PlayClock Deck 后台服务（`deck/playclock_deck.py`）的控制面板：今天玩了多久、立即同步、当前用的云端和修改账号、Ryujinx 存档同步的状态 / 冲突 / 设置。记录和同步本身仍由后台服务完成（桌面模式、Decky 坏了时也照常工作）；插件只读它的状态文件、改它的设置（`~/.config/playclock/config.json`，服务会自动重新读取）、通过 `~/.local/share/playclock/requests.json` + SIGUSR1 请它马上行动。插件不需要 root。

构建：GitHub Actions（`.github/workflows/decky.yml`）用 Node 20 + pnpm 9 编译，产物 `PlayClock-Decky.zip`。
