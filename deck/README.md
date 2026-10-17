# PlayClock · Steam Deck 端

Steam Deck（SteamOS）上的后台记录程序：自动记录模拟器游戏时长，同步到你自己的坚果云。在电脑上的 PlayClock 里会显示为「其他设备」，统计和游戏库都会合在一起。

- **没有界面，只负责记录**。查看时长请用电脑上的 PlayClock。
- **游戏模式、桌面模式下都在后台运行**。它是用户级的 systemd 服务，开机自动启动，SteamOS 更新后也不会消失，安装也不需要 root 权限。
- **休眠不计时**：Deck 休眠时程序会被冻结；唤醒后发现两次检查之间隔了很久，这段时间就不计入，并且马上同步一次。
- **Ryujinx 暂停不计时**：从 Ryujinx 自己的日志里读出游戏名和暂停状态，和 Windows 版的做法一样。
- **导入之前的游玩时长**：来源有两个，一是 Ryujinx 自己的记录，二是 Steam 给每个模拟器快捷方式记的时长（`localconfig.vdf`）。两者记的是同一段游玩，所以同一个游戏取较大的那个，不会相加；已经由本程序计时的部分会扣掉，和电脑上的时长分开计算。
- **只用到 Python 标准库**：SteamOS 自带 Python，不用安装任何东西。

## 支持的模拟器
Ryujinx、yuzu 系（citron / eden / sudachi…）、Citra 系（Azahar / Lime3DS）、Dolphin、PCSX2、DuckStation、PPSSPP、RPCS3、Cemu、Vita3K、melonDS、mGBA、Flycast、shadPS4、xemu、RetroArch（EmuDeck 的 AppImage 和 Flatpak 版本都能识别）。

识别方法：
- **优先用 Steam 库里的名字**。EmuDeck 的游戏都是用 Steam ROM Manager 作为「非 Steam 游戏」加进 Steam 的，从 Steam 启动时，模拟器进程会带上 Steam 设的环境变量 `SteamGameId`。程序用它在 `shortcuts.vdf` 里找到对应的快捷方式，用你在库里看到的那个名字记录。
- 不是从 Steam 启动时，游戏名取自模拟器启动命令里的 ROM 文件名，会自动去掉 `(USA)`、`[v0]` 之类的标签；Ryujinx 的游戏名从它自己的日志里读。
- Switch 游戏还会带上 Title ID（从 Ryujinx 日志、ROM 文件名或快捷方式的启动参数里取），这样在电脑上会和不同语言的同名游戏合并成一个。
- 和电脑上名字不一样的游戏，在 PlayClock 里用「重命名 / 合并」统一成一个名字即可，以后的记录也会自动归到一起。

## 安装（在 Deck 的桌面模式里）
1. 先准备好坚果云的**应用密码**：在坚果云桌面客户端的「设置 → 第三方应用管理」里生成。不能用登录密码。
2. 打开 **Konsole**，粘贴运行：
   ```bash
   curl -fsSL https://raw.githubusercontent.com/RinoWu96/PlayClock/main/deck/install.sh | bash
   ```
3. 按提示输入坚果云账号、应用密码和设备名称（默认是主机名）。连接成功后它就会在后台运行。

已经装过的话，再运行一次同一条命令就会更新到最新版（保留设置和记录）。

## 常用命令
```bash
python3 ~/.local/share/playclock-deck/playclock_deck.py status   # 现在在玩什么、记录了多少、上次同步
python3 ~/.local/share/playclock-deck/playclock_deck.py doctor   # 自检：模拟器进程、Ryujinx 目录、Python 版本
python3 ~/.local/share/playclock-deck/playclock_deck.py sync     # 立即同步
journalctl --user -u playclock-deck -f                           # 运行日志
bash ~/.local/share/playclock-deck/uninstall.sh                  # 卸载（可以选择是否保留本地记录）
```

## 数据保存在哪里
- 设置：`~/.config/playclock/config.json`，权限是 600，只有你的用户能读。里面有坚果云的应用密码，它可以随时在坚果云里撤销。
- 本机记录：`~/.local/share/playclock/sessions.json`。
- 云端：坚果云里的 `PlayClock/devices/<设备ID>.json.gz`，只有这台 Deck 会写这个文件。

## Ryujinx 存档同步（和电脑之间）
像 Steam 云存档一样在 Deck 和电脑之间同步 Ryujinx 存档，存在同一个坚果云的 `PlayClock/saves/ryujinx/` 里。设计见 [docs/SAVE_SYNC_DESIGN.md](../docs/SAVE_SYNC_DESIGN.md)。

```bash
python3 ~/.local/share/playclock-deck/playclock_deck.py saves on        # 开启（然后重启服务）
systemctl --user restart playclock-deck
python3 ~/.local/share/playclock-deck/playclock_deck.py saves           # 每个游戏的状态、本月流量
python3 ~/.local/share/playclock-deck/playclock_deck.py saves sync      # 立即对比一次
python3 ~/.local/share/playclock-deck/playclock_deck.py saves use-cloud <游戏ID>   # 冲突时用云端的
python3 ~/.local/share/playclock-deck/playclock_deck.py saves use-local <游戏ID>   # 冲突时用 Deck 的
```

- **关闭游戏后上传**；游戏开着时存档有变化，最多每 15 分钟上传一次（本月上传超过额度 70% 后只在关闭游戏时上传）。
- **启动前下载**：Ryujinx 刚启动时程序会把它暂停一两秒，先把云端更新的存档拿下来再继续，不需要改 Steam 的快捷方式。
- **休眠前**：尽量把开着的游戏的存档传上去（SteamOS 只给 5 秒，网络也在同时断开，所以不保证）。
- **冲突**（两边都有新进度）不会自动覆盖：一般在电脑上的 PlayClock 里选，也可以用上面的 `use-cloud` / `use-local`。
- 覆盖本机存档前都会备份到 `~/.local/share/playclock/save_backups/`。
