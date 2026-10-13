# PlayClock 工作原理

本文档说明 PlayClock 的每个功能从哪里取数据、怎么解析、怎么计算。所有逻辑都是普通的确定性代码（正则、文件解析、公开网络接口），不依赖任何 AI 模型。照着本文和 `tests/` 里的测试，任何人都可以复现、修改和验证。

## 1. 识别正在玩的游戏（`winapi.py`、`rules.py`、`tracker.py`）

1. 每 `interval` 秒（默认 5 秒）枚举所有可见的顶层窗口（Win32 `EnumWindows`），拿到窗口标题、所属进程的 exe 名、窗口面积、是否最小化、是否前台。进程 exe 名按 PID 缓存，进程退出后清掉。
2. 用规则匹配 exe 名（通配符，不区分大小写），找出模拟器窗口。
3. 同一进程有多个窗口时，按面积从大到小，取第一个能解析出游戏名的窗口（游戏窗口通常最大，设置对话框较小）。最小化的窗口不算。
4. 用规则里的正则（`pattern`，命名分组 `game`）从标题取出游戏名，再经过 `clean_title()` 清理：去掉 Switch Title ID、`(64-bit)`、PS 序列号、FPS、ROM 扩展名、末尾版本号等。
5. `ignore` 正则命中的标题，表示「模拟器开着但没在玩」。

### 计时规则

- 两次检测间隔正常（≤ max(3×interval, 30 秒)），而且游戏不变：累加这段时间。
- 间隔过长，说明电脑睡眠了或程序卡住了，这段时间不计入，并结束当前会话。
- 游戏关闭后 `resume_gap`（默认 180 秒）内又打开同一个游戏，接着算上一次会话。
- 短于 `min_session`（默认 60 秒）的会话丢弃。
- 数据库每 30 秒写一次；游戏切换、正常退出、Windows 关机或注销（`commitDataRequest`）时也会立刻写。窗口打开时每次检测都写，界面上的数字保持最新。

### 加一个新模拟器

1. 运行这个模拟器，开始一个游戏。
2. 打开 PlayClock「设置 → 识别」，记下显示的 exe 名和窗口标题。
   - 如果这个模拟器还不认识，可以先在 `data/rules.user.json` 里写一条规则，用来测试。
3. 在 `playclock/rules.py` 的 `DEFAULT_RULES` 里加一条，包含这些字段：
   - `name`：模拟器名称。
   - `platform`：对应的主机平台。
   - `exe`：进程名的通配符列表。
   - `pattern`：从标题取游戏名的正则。
   - 需要的话再加 `ignore`。
4. 在 `tests/test_core.py` 的 `test_titles` 里加几行真实标题样例：至少一行运行中的标题（带游戏名），一行空闲时的标题（期望结果为 `None`）。
5. 运行 `.venv\Scripts\python.exe -m pytest -q tests`，全部通过就完成了。

## 2. 数据库（`db.py`）

SQLite 文件 `data/playtime.db`。

| 表 | 内容 |
| --- | --- |
| `sessions` | 每次游玩：`game`、`emulator`（模拟器名 / `Steam` / 手动填的平台）、`start`、`end`、`seconds`、`source`，以及同步用的 `uid`（全局唯一）、`device`（记录它的设备）、`updated`（最后修改时间） |
| `tombstones` | 被删除会话的 `uid`，防止同步时被别的设备「复活」 |
| `meta` | 游戏封面文件名（存放在 `data/covers/`） |
| `aliases` | 重命名或合并：识别出的原名 → 记录用的名称（带 `updated`，用于同步） |
| `steam_names` | 联网查到的 Steam 游戏名缓存 |

`source` 字段区分时长的来源：

- `NULL`：PlayClock 自己计时的。
- `manual`：手动添加的。
- `ryujinx`、`steam:<账号ID>`：从其他平台导入的。

**统计口径**：导入的时长只有累计总数，没有日期。所以它计入「全部」的总时长和排行，不计入今天 / 本周 / 本月、趋势图、日均和游玩天数（见 `db.DATED`）。

## 3. 导入其他平台的历史时长（`importers.py`、`steam.py`）

**原则：只补差额，可以重复导入。** 对每个游戏、每个平台，导入量 = 该平台自己记录的总时长 − PlayClock 在该平台上自己计时的时长（不小于 0）。结果存成一条带 `source` 的会话，再次导入时替换这条记录。差额要按平台分开算，因为同一个游戏可能在多个平台上玩过。

### Ryujinx

`%APPDATA%\Ryujinx\games\<TitleID>\gui\metadata.json`（便携版放在 `portable\games\...`）：

- `title`：游戏名。
- `timespan_played`：.NET TimeSpan 格式，`[d.]hh:mm:ss[.fffffff]`。
- `last_played_utc`：ISO 时间。

### Steam（不需要账号、Key，也不联网）

- **安装位置**：注册表 `HKCU\Software\Valve\Steam\SteamPath`。
- **账号**：`userdata\<账号ID>\config\localconfig.vdf`，文本 KeyValues 格式。
  - 路径 `UserLocalConfigStore/Software/Valve/Steam/apps/<appid>` 下有 `Playtime`（分钟）和 `LastPlayed`（Unix 时间）。
  - 昵称在 `PersonaName`。
  - 账号 ID + 76561197960265728 = SteamID64。
- **游戏名和类型**：`appcache\appinfo.vdf`，二进制 KeyValues 格式。
  - 文件头：`magic`（u32，`0x07564429` = v29，`0x07564428` = v28）、`universe`（u32）；v29 还有一个 `string_table_offset`（i64）。
  - 之后每个应用依次是：`appid`（u32，为 0 表示结束）、`size`（u32）、`info_state`（u32）、`last_updated`（u32）、`pics_token`（u64）、`sha1`（20 字节）、`change_number`（u32）、`binary_sha1`（20 字节），后面才是 KeyValues 数据。
  - KeyValues 的类型字节：`0x00` 子节、`0x01` 字符串、`0x02` int32、`0x03` float、`0x07` uint64、`0x08` 结束、`0x0A` int64。
  - v29 里的键名是字符串表的 u32 下标；字符串表的格式是 `count`（u32）后接若干以 `\0` 结尾的字符串。
  - 取 `appinfo/common/name`、`type` 和 `name_localized/schinese`。
  - `type` 是 tool、application、dlc 等的不算游戏，跳过。
- **本地查不到名字的应用**：用 Steam 商店公开接口 `store.steampowered.com/api/appdetails?appids=<id>&filters=basic` 查名字，结果缓存进 `steam_names` 表。
- **封面**：先找 `appcache\librarycache\<appid>\header.jpg`（或其下一级子目录里的同名文件）；没有的话，从 `shared.cloudflare.steamstatic.com/store_item_assets/steam/apps/<appid>/header.jpg` 下载。
- **自动同步**：大约每 60 秒检查一次 `localconfig.vdf` 的修改时间。文件有变化才重新导入；没变化时只是一次 `stat` 调用。

## 4. 封面（`covers.py`）

- **SteamGridDB**：用用户自己的 API Key（`Authorization: Bearer <key>`）调用 `/search/autocomplete/<名字>` 和 `/grids/game/<id>`，只取横版尺寸（920×430 或 460×215）。
- **中文、日文名先查英文名**：调维基百科 API，参数为 `action=query&generator=search&gsrlimit=1&prop=langlinks&lllang=en`（简繁中文查 zh 站，带假名的查 ja 站），取第一条搜索结果对应的英文条目名。如果原名末尾有英文部分（比如「HD-2D Remake」）而英文名里没有，就补回去。用英文名在 SteamGridDB 搜不到时，再用原名搜。
- 只有 SteamGridDB 请求会带 API Key；访问维基百科、Steam 商店和 Steam 图床时都不发送。

## 5. 多设备同步（`sync.py`）

同步不经过任何 PlayClock 自己的服务器，而是使用用户自己的 WebDAV 存储（默认是坚果云 `https://dav.jianguoyun.com/dav/`，也可以用 NAS、Nextcloud 等）。

- **文件布局**：WebDAV 上的 `PlayClock/devices/<设备ID>.json.gz`，每台设备一个文件，只由这台设备自己写入，所以不会有写冲突。设备 ID 是第一次运行时生成的 UUID，保存在 `config.json` 的 `device_id` 里。
- **文件内容**：gzip 压缩的 JSON，字段如下：
  - `format`：1。
  - `device`、`name`：设备 ID 和电脑名。
  - `sessions`：本设备记录的所有会话，每条带 `uid`、`game`、`emulator`、`start`、`end`、`seconds`、`source`、`device`、`updated`。
  - `aliases`：本设备知道的全部改名、合并记录，含 `updated`。
  - `tombstones`：本设备知道的全部删除标记，即被删会话的 `uid`。
- **合并规则**（`merge_payload`）：
  1. 先应用改名：比本地更新的别名覆盖本地别名，并把对应会话改到新名字下。
  2. 再应用删除标记：删掉对应的会话，并记住这个 `uid`，以后再收到它就忽略。
  3. 最后合并会话：同一个 `uid` 以 `updated` 较新的为准。

  这样不管各设备按什么顺序同步，最后结果都一样；重复合并也不会出错。
- **导入记录的 uid**：由 `(范围, 来源, 游戏名)` 的 SHA-1 算出，同样的导入在不同设备上会得到同一个 uid。Steam 的时长是整个账号的，范围为空，所以两台设备都导入同一个 Steam 账号也不会重复计算。Ryujinx 的时长每台设备各自记录，范围是设备 ID，所以电脑和 Deck 上的 Ryujinx 时长会相加。
- **扣除差额只看本机**：「本机 PlayClock 自己计时的时长」只统计 `device` 等于本机的会话，所以别的设备同步过来的计时，不会影响本机导入时扣多少。
- **一次同步的过程**（`run_sync`）：
  1. 如果本机文件有变化（按内容哈希判断），就上传（PUT）。
  2. 列出目录（PROPFIND，Depth 1），只下载版本标记（ETag）变了的其他设备文件。
  3. 回到主线程合并。

  网络操作都在后台线程里做，数据库只在主线程读写。
- **频率**：启动 5 秒后、每 10 分钟、打开窗口时、以及点「立即同步」时各同步一次。
- **密码**：用 Windows DPAPI（`CryptProtectData`）加密后保存在 `config.json` 里，只有当前 Windows 用户能解密。
- **测试**：`tests/test_sync.py` 用 wsgidav 在本机启动一个真正的 WebDAV 服务器，跑完整的双设备同步。
- **给小程序的文件**（`summary.py`）：微信小程序不能发 PROPFIND，所以每次同步电脑端还会写两样东西，小程序只用普通 GET 读取：
  - `PlayClock/summary.json`：汇总好的统计（正在玩、每日时长、游戏列表等），内容没变就不重新上传。每个有封面的游戏带 `"cover": "covers/<id>.jpg"`。
  - `PlayClock/covers/<id>.jpg`：封面缩略图，从 `data/covers/<id>.png` 居中裁成 460×215（Steam 横幅比例）的 JPEG（质量 82）。只上传远端还没有的，每次同步最多 60 张，封面多时分几次传完。换封面会得到新的文件名，所以小程序端按文件名永久缓存。
  - 小程序（`miniprogram/utils/covers.js`）用 `wx.downloadFile`（带 Basic 认证）把缩略图下载到 `wx.env.USER_DATA_PATH/covers/`，同时最多 4 个；下载失败的 5 分钟后重试；不再用到的缓存文件会删除。没下载到之前显示首字母头像。
  - 同步只会写 `devices/<本机>.json.gz`、`summary.json` 和 `covers/*`，不会删除或改动 `PlayClock` 文件夹里的其他文件。

## 6. 后台占用

- 窗口只在打开时存在；关闭后整个销毁，同时清空封面缓存，并调用 `SetProcessWorkingSetSize(-1, -1)` 让系统换出空闲内存。
- 后台只有一个 `interval` 秒的检测定时器，没有每秒运行的任务。
- 参考测量（只开托盘时）：专用内存约 24 MB，工作集约 7 MB，CPU 约 0。

## 7. 隐私

所有记录都只保存在本机的 `data/` 文件夹里。只有以下情况会联网：

- 封面功能：访问 SteamGridDB；中文、日文名还会访问维基百科。
- 查不到名字的 Steam 应用：访问 Steam 商店公开接口。
- 下载 Steam 封面：访问 Steam 图床。
- 开启云同步后：只访问用户自己填写的 WebDAV 地址。
