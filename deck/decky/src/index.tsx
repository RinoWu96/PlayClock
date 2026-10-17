import {
  ButtonItem,
  ConfirmModal,
  DialogBody,
  DialogButton,
  DialogHeader,
  DropdownItem,
  Field,
  Focusable,
  ModalRoot,
  Navigation,
  PanelSection,
  PanelSectionRow,
  TextField,
  ToggleField,
  showModal,
  staticClasses,
} from "@decky/ui";
import { callable, definePlugin, routerHook, toaster } from "@decky/api";
import { useEffect, useState } from "react";
import { FaClock } from "react-icons/fa";

// The panel only shows and steers the playclock-deck service (deck/playclock_deck.py); see main.py.

interface Live {
  version?: string;
  playing?: { game: string; emulator: string; start: number; minutes: number } | null;
  paused?: string | null;
  today_minutes?: number;
  last_sync?: number;
  sync_result?: string;
  last_action?: { action: string; ok: boolean; message: string; time: number } | null;
}

interface Cloud {
  provider: string;
  url: string;
  user: string;
}

interface Settings {
  save_sync: boolean;
  save_every: number;
  launch_check: boolean;
  sleep_upload: boolean;
  quota_mb: number;
}

interface Game {
  title: string;
  name: string;
  status: string;
  conflict: boolean;
  /** This Deck uploaded the diverging version (so "use the Deck's" is possible here). */
  mine: boolean;
  cloud_device: string;
  cloud_time: number | null;
  restored_from: string;
}

interface State {
  installed: boolean;
  running?: boolean;
  live?: Live;
  name?: string;
  cloud?: Cloud | null;
  settings?: Settings;
  games?: Game[];
  traffic?: { up: number; down: number };
}

const getState = callable<[], State>("get_state");
const setSetting = callable<[key: string, value: boolean | number], State>("set_setting");
const setName = callable<[name: string], State>("set_name");
const setAccount = callable<[url: string, user: string, password: string], { ok: boolean; message: string }>(
  "set_account",
);
const request = callable<[action: string, title: string, keep: string, version: string], { ok: boolean; woke: boolean }>(
  "request",
);
const getVersions = callable<[title: string], Versions>("get_versions");

const notify = (body: string) => toaster.toast({ title: "PlayClock", body });

function dur(minutes: number): string {
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return h ? `${h} 小时 ${m} 分` : `${m} 分钟`;
}

function ago(t?: number | null): string {
  if (!t) return "从未";
  const s = Date.now() / 1000 - t;
  if (s < 60) return "刚刚";
  if (s < 3600) return `${Math.floor(s / 60)} 分钟前`;
  if (s < 86400) return `${Math.floor(s / 3600)} 小时前`;
  const d = new Date(t * 1000);
  return `${d.getMonth() + 1}-${d.getDate()} ${d.getHours()}:${String(d.getMinutes()).padStart(2, "0")}`;
}

const STATUS: Record<string, string> = {
  synced: "已同步",
  upload: "已上传",
  restore: "已从云端恢复",
  pending_restore: "关闭 Ryujinx 后恢复",
  pending_upload: "待上传",
};

/** One short word or phrase per game. */
function statusText(g: Game): string {
  if (g.conflict) return "冲突，需要选择";
  if (g.status === "restore" && g.restored_from) return `已从 ${g.restored_from} 恢复`;
  if (g.status.startsWith("error")) return "出错，稍后重试";
  return STATUS[g.status] ?? g.status;
}

const SAVES_ROUTE = "/playclock/saves";

interface Versions {
  ok: boolean;
  message?: string;
  device?: string;
  cloud?: { file: string; stamp: string; device: string }[];
  backups?: { path: string; stamp: string }[];
}

/** "20261017T093000123Z" (UTC) -> local "10-17 19:30". */
function stampLabel(stamp: string): string {
  const m = /^(\d{4})(\d\d)(\d\d)T(\d\d)(\d\d)(\d\d)/.exec(stamp);
  if (!m) return stamp;
  const d = new Date(Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6]));
  return `${d.getMonth() + 1}-${d.getDate()} ${d.getHours()}:${String(d.getMinutes()).padStart(2, "0")}`;
}

function confirmThen(title: string, text: string, ok: () => void) {
  showModal(<ConfirmModal strTitle={title} strDescription={text} strOKButtonText="恢复" strCancelButtonText="取消" onOK={ok} />);
}

/** Full-screen page: this Deck's games on the left, the chosen one's status, actions and history on the right. */
function SavesPage() {
  const [state, setState] = useState<State | null>(null);
  const [selected, setSelected] = useState<string>("");
  const [versions, setVersions] = useState<Versions | null>(null);
  const [loading, setLoading] = useState(false);
  const refresh = () => getState().then(setState).catch((e) => console.error("[PlayClock] get_state failed", e));

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 3000); // only while the page is open
    return () => clearInterval(timer);
  }, []);
  useEffect(() => setVersions(null), [selected]);

  const games = state?.games ?? [];
  const game = games.find((g) => g.title === selected) ?? games[0];
  const act = async (action: string, title: string, keep = "", version = "") => {
    await request(action, title, keep, version);
    notify("已提交，正在处理…");
    setTimeout(refresh, 2500);
  };
  const loadVersions = async (title: string) => {
    setLoading(true);
    try {
      setVersions(await getVersions(title));
    } finally {
      setLoading(false);
    }
  };
  const last = state?.live?.last_action;

  return (
    <div style={{ padding: "56px 24px 24px", height: "100%", boxSizing: "border-box", overflowY: "auto" }}>
      <DialogHeader>存档管理</DialogHeader>
      {last && (
        <div style={{ opacity: 0.8, marginBottom: "12px" }}>
          {`${ago(last.time)}：${last.ok ? "" : "失败："}${last.message}`}
        </div>
      )}
      {!state ? (
        <div>正在读取…</div>
      ) : games.length === 0 ? (
        <div>这台 Deck 上还没有 Ryujinx 存档。</div>
      ) : (
        <div style={{ display: "flex", gap: "24px", alignItems: "flex-start" }}>
          <Focusable style={{ display: "flex", flexDirection: "column", gap: "6px", width: "40%" }}>
            {games.map((g) => (
              <DialogButton
                key={g.title}
                onClick={() => setSelected(g.title)}
                style={{ textAlign: "left", opacity: game?.title === g.title ? 1 : 0.75 }}
              >
                <div>{g.name}</div>
                <div style={{ fontSize: "12px", opacity: 0.7 }}>{statusText(g)}</div>
              </DialogButton>
            ))}
          </Focusable>
          {game && (
            <Focusable style={{ flex: 1, display: "flex", flexDirection: "column", gap: "8px" }}>
              <div style={{ fontSize: "20px", fontWeight: "bold" }}>{game.name}</div>
              <div>{statusText(game)}</div>
              <div style={{ opacity: 0.7 }}>
                {game.cloud_device ? `云端最新：${game.cloud_device}，${ago(game.cloud_time)}` : "云端还没有这个游戏"}
              </div>
              <DialogButton onClick={() => act("sync_game", game.title)}>立即同步这个游戏</DialogButton>
              {game.conflict && (
                <>
                  <div style={{ marginTop: "8px" }}>两台设备都有新进度，只能保留一份（另一份会留在备份里）：</div>
                  <DialogButton onClick={() => act("resolve", game.title, "cloud")}>用云端的</DialogButton>
                  {game.mine && (
                    <DialogButton onClick={() => act("resolve", game.title, "local")}>用这台 Deck 的</DialogButton>
                  )}
                </>
              )}
              <div style={{ marginTop: "12px", fontWeight: "bold" }}>历史版本</div>
              {!versions && (
                <DialogButton disabled={loading} onClick={() => loadVersions(game.title)}>
                  {loading ? "正在读取…" : "查看历史版本"}
                </DialogButton>
              )}
              {versions && !versions.ok && <div>{versions.message}</div>}
              {versions?.ok && (versions.cloud ?? []).length + (versions.backups ?? []).length === 0 && (
                <div style={{ opacity: 0.7 }}>还没有历史版本。</div>
              )}
              {versions?.ok &&
                (versions.cloud ?? []).map((v) => (
                  <DialogButton
                    key={v.file}
                    onClick={() =>
                      confirmThen(game.name, "用这个云端版本替换现在的存档？现在的存档会先备份。需要先关闭 Ryujinx。", () =>
                        act("restore_version", game.title, "", v.file),
                      )
                    }
                  >
                    {`云端 · ${v.device === versions.device ? "这台 Deck" : "其他设备"} · ${stampLabel(v.stamp)}`}
                  </DialogButton>
                ))}
              {versions?.ok &&
                (versions.backups ?? []).map((b) => (
                  <DialogButton
                    key={b.path}
                    onClick={() =>
                      confirmThen(game.name, "用这份本机备份替换现在的存档？现在的存档会先备份。需要先关闭 Ryujinx。", () =>
                        act("restore_version", game.title, "", b.path),
                      )
                    }
                  >
                    {`本机备份 · ${stampLabel(b.stamp)}`}
                  </DialogButton>
                ))}
            </Focusable>
          )}
        </div>
      )}
    </div>
  );
}

const mb = (b: number) => `${(b / 1024 / 1024).toFixed(1)} MB`;

function AccountModal(props: { closeModal?: () => void; cloud: Cloud | null | undefined; onSaved: () => void }) {
  const [url, setUrl] = useState(props.cloud?.url ?? "https://dav.jianguoyun.com/dav/");
  const [user, setUser] = useState(props.cloud?.user ?? "");
  const [pass, setPass] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const save = async () => {
    setBusy(true);
    setMsg("正在连接…");
    try {
      const r = await setAccount(url, user, pass);
      if (r.ok) {
        props.onSaved();
        props.closeModal?.();
        notify("云端账号已更新");
      } else setMsg(r.message);
    } catch (e) {
      setMsg(String(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <ModalRoot closeModal={props.closeModal} onCancel={props.closeModal}>
      <DialogHeader>云端账号</DialogHeader>
      <DialogBody>
        <TextField label="服务器（WebDAV 地址）" value={url} onChange={(e) => setUrl(e.target.value)} />
        <TextField label="账号（坚果云登录邮箱或手机号）" value={user} onChange={(e) => setUser(e.target.value)} />
        <TextField
          label="应用密码"
          description="坚果云：在桌面客户端「设置 → 第三方应用管理」里生成，不是登录密码。只改服务器时可以留空。"
          bIsPassword
          value={pass}
          onChange={(e) => setPass(e.target.value)}
        />
        {msg && <div style={{ marginTop: "8px" }}>{msg}</div>}
        <Focusable style={{ display: "flex", gap: "8px", marginTop: "12px" }}>
          <DialogButton disabled={busy} onClick={save}>
            {busy ? "正在连接…" : "连接并保存"}
          </DialogButton>
          <DialogButton onClick={props.closeModal}>取消</DialogButton>
        </Focusable>
      </DialogBody>
    </ModalRoot>
  );
}

function NameModal(props: { closeModal?: () => void; name: string; onSaved: (s: State) => void }) {
  const [name, setValue] = useState(props.name);
  return (
    <ModalRoot closeModal={props.closeModal} onCancel={props.closeModal}>
      <DialogHeader>设备名</DialogHeader>
      <DialogBody>
        <TextField label="在电脑上的 PlayClock 里显示的名字" value={name} onChange={(e) => setValue(e.target.value)} />
        <Focusable style={{ display: "flex", gap: "8px", marginTop: "12px" }}>
          <DialogButton
            onClick={async () => {
              props.onSaved(await setName(name));
              props.closeModal?.();
            }}
          >
            保存
          </DialogButton>
          <DialogButton onClick={props.closeModal}>取消</DialogButton>
        </Focusable>
      </DialogBody>
    </ModalRoot>
  );
}

function Content() {
  const [state, setState] = useState<State | null>(null);
  const refresh = () =>
    getState()
      .then(setState)
      .catch((e) => console.error("[PlayClock] get_state failed", e));

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 3000); // only while the panel is open
    return () => clearInterval(timer);
  }, []);

  if (!state) return <PanelSection>正在读取…</PanelSection>;
  if (!state.installed)
    return (
      <PanelSection>
        <PanelSectionRow>
          <Field
            label="没有找到 PlayClock 后台程序"
            description="先在桌面模式的 Konsole 里运行安装命令：curl -fsSL https://raw.githubusercontent.com/RinoWu96/PlayClock/main/deck/install.sh | bash"
          />
        </PanelSectionRow>
      </PanelSection>
    );

  const live = state.live ?? {};
  const conflicts = (state.games ?? []).filter((g) => g.conflict).length;
  const s = state.settings!;
  const change = async (key: keyof Settings, value: boolean | number) => setState(await setSetting(key, value));
  const ask = async (action: string, title = "", keep = "") => {
    await request(action, title, keep, "");
    notify(action === "sync" ? "正在同步…" : "已提交，正在处理…");
    setTimeout(refresh, 2500);
  };
  const playing = live.playing
    ? `${live.playing.game}（${live.playing.emulator}）· 本次 ${dur(live.playing.minutes)}`
    : live.paused
      ? `${live.paused}（暂停中）`
      : "现在没有在玩";

  return (
    <>
      <PanelSection title="游戏时长">
        {!state.running && (
          <PanelSectionRow>
            <Field label="后台服务没有运行" description="记录和同步暂停了。可以在桌面模式里运行：systemctl --user restart playclock-deck" />
          </PanelSectionRow>
        )}
        <PanelSectionRow>
          <Field label="正在玩" description={playing} />
        </PanelSectionRow>
        <PanelSectionRow>
          <Field label="今天" description={dur(live.today_minutes ?? 0)} />
        </PanelSectionRow>
        <PanelSectionRow>
          <Field
            label="上次同步"
            description={`${ago(live.last_sync)}${live.last_action ? ` · ${live.last_action.ok ? "" : "失败："}${live.last_action.message}` : ""}`}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" disabled={!state.running} onClick={() => ask("sync")}>
            立即同步
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>

      <PanelSection title="云端">
        <PanelSectionRow>
          <Field
            label={state.cloud ? state.cloud.provider : "未连接"}
            description={state.cloud ? `${state.cloud.user}\n${state.cloud.url}` : "还没有设置云端账号"}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            onClick={() => showModal(<AccountModal cloud={state.cloud} onSaved={refresh} />)}
          >
            修改账号…
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>

      <PanelSection title="Ryujinx 存档同步">
        <PanelSectionRow>
          <ToggleField
            label="在 Deck 和电脑之间同步存档"
            checked={s.save_sync}
            disabled={!state.cloud}
            onChange={(v) => change("save_sync", v)}
          />
        </PanelSectionRow>
        {s.save_sync && (
          <PanelSectionRow>
            <Field
              label={`${(state.games ?? []).length} 个游戏`}
              description={conflicts ? `${conflicts} 个有冲突，需要在存档管理里选择` : "都已同步"}
            />
          </PanelSectionRow>
        )}
        {s.save_sync && (
          <PanelSectionRow>
            <ButtonItem
              layout="below"
              onClick={() => {
                Navigation.CloseSideMenus();
                Navigation.Navigate(SAVES_ROUTE);
              }}
            >
              存档管理…
            </ButtonItem>
          </PanelSectionRow>
        )}
        {s.save_sync && state.traffic && (
          <PanelSectionRow>
            <Field label="本月流量" description={`上传 ${mb(state.traffic.up)} · 下载 ${mb(state.traffic.down)}`} />
          </PanelSectionRow>
        )}
      </PanelSection>

      {s.save_sync && (
        <PanelSection title="存档同步设置">
          <PanelSectionRow>
            <DropdownItem
              label="游戏中上传"
              description="存档有变化时上传；关闭游戏时总会上传"
              rgOptions={[
                { data: 5, label: "最多每 5 分钟" },
                { data: 15, label: "最多每 15 分钟" },
                { data: 30, label: "最多每 30 分钟" },
                { data: 0, label: "只在关闭游戏时" },
              ]}
              selectedOption={s.save_every}
              onChange={(o) => change("save_every", o.data)}
            />
          </PanelSectionRow>
          <PanelSectionRow>
            <ToggleField
              label="启动前检查云端"
              description="Ryujinx 启动时暂停一两秒，先拿到最新的存档"
              checked={s.launch_check}
              onChange={(v) => change("launch_check", v)}
            />
          </PanelSectionRow>
          <PanelSectionRow>
            <ToggleField
              label="休眠前上传"
              description="尽量在 Deck 休眠前把开着的游戏的存档传上去（不保证成功）"
              checked={s.sleep_upload}
              onChange={(v) => change("sleep_upload", v)}
            />
          </PanelSectionRow>
          <PanelSectionRow>
            <DropdownItem
              label="每月上传额度"
              description="超过 70% 后只在关闭游戏时上传"
              rgOptions={[
                { data: 1024, label: "1 GB（坚果云免费版）" },
                { data: 2048, label: "2 GB" },
                { data: 5120, label: "5 GB" },
                { data: 0, label: "不限制" },
              ]}
              selectedOption={s.quota_mb}
              onChange={(o) => change("quota_mb", o.data)}
            />
          </PanelSectionRow>
        </PanelSection>
      )}

      <PanelSection title="这台 Deck">
        <PanelSectionRow>
          <Field label="设备名" description={state.name || "（主机名）"} />
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            onClick={() => showModal(<NameModal name={state.name ?? ""} onSaved={setState} />)}
          >
            修改设备名…
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <Field description={`PlayClock Deck ${live.version ?? ""}`} />
        </PanelSectionRow>
      </PanelSection>
    </>
  );
}

export default definePlugin(() => {
  routerHook.addRoute(SAVES_ROUTE, SavesPage, { exact: true });
  return {
    name: "PlayClock",
    titleView: <div className={staticClasses.Title}>PlayClock</div>,
    content: <Content />,
    icon: <FaClock />,
    onDismount() {
      routerHook.removeRoute(SAVES_ROUTE);
    },
  };
});
