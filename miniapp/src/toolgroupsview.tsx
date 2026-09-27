// The tool groups on screen: the installation's choice in Settings → Tools, and one session's view of
// them in its Details. A group on demand costs the model one line of the prompt instead of its
// definitions on every request; these two places are where the operator decides which groups are
// worth that line and sees which ones a session has actually loaded.

import { useCallback, useEffect, useState } from "react";
import { api, SessionToolGroup, ToolGroupCatalogue, ToolGroupLoad } from "./api";
import { plural, t } from "./i18n";
import { errorText, fmtTok } from "./ui";
import { LOADS, groupAbout, groupName, loadWord, stateChip, usageLine, usageShare } from "./toolgroups";

function LoadPicker({ label, value, onPick, busy }: { label: string; value: ToolGroupLoad; onPick: (load: ToolGroupLoad) => void; busy?: boolean }) {
  return (
    <div className="segmented inline tgroup-load" role="radiogroup" aria-label={label}>
      {LOADS.map((load) => (
        <button key={load} role="radio" aria-checked={value === load} className={value === load ? "on" : ""} disabled={busy} onClick={() => value !== load && onPick(load)} title={t(`tgroup.load.${load}.hint`)}>
          {loadWord(load)}
        </button>
      ))}
    </div>
  );
}

/** Settings → Tools: every group with what it costs and how often it was needed, and its mode. */
export function ToolGroupsSettings({ toast }: { toast: (text: string) => void }) {
  const [data, setData] = useState<ToolGroupCatalogue | null>(null);
  const [busy, setBusy] = useState("");
  useEffect(() => {
    api.get<ToolGroupCatalogue>("/api/tool-groups").then(setData).catch((e) => toast(errorText(e)));
  }, [toast]);
  async function pick(name: string, load: ToolGroupLoad) {
    setBusy(name);
    try {
      setData(await api.put<ToolGroupCatalogue>(`/api/tool-groups/${encodeURIComponent(name)}`, { load }));
      toast(t("tgroup.saved", { name: groupName(name), load: loadWord(load) }));
    } catch (e) {
      toast(errorText(e));
    } finally {
      setBusy("");
    }
  }
  return (
    <div className="card tgroups">
      <div className="section-title" style={{ marginTop: 0 }}>{t("tgroup.title")}</div>
      <div className="sub">{t("tgroup.sub")}</div>
      {data === null && <div className="sub">{t("common.loading")}</div>}
      {data?.groups.map((g) => {
        const share = usageShare(g.usage.sessions, data.sessions);
        return (
          <div key={g.name} className="tgroup" data-group={g.name}>
            <div className="tgroup-head">
              <b className="tgroup-name">{groupName(g.name)}</b>
              <span className="sub num">{plural("tgroup.tools", g.tools.length)} · ≈{fmtTok(g.tokens)} {t("tgroup.tokens")}</span>
            </div>
            <div className="sub tgroup-about">{groupAbout(g.name, g.description)}</div>
            <div className="tgroup-use" title={t("tgroup.usage.detail", { sessions: g.usage.sessions, total: data.sessions, runs: g.usage.runs, calls: g.usage.calls })}>
              <span className="tgroup-bar" aria-hidden><span style={{ width: `${Math.min(100, Math.max(share.pct > 0 ? 2 : 0, share.pct))}%` }} /></span>
              <span className="sub">{usageLine(g.usage.sessions, data.sessions, data.days)}</span>
            </div>
            <div className="tgroup-foot">
              <LoadPicker label={groupName(g.name)} value={g.load} onPick={(load) => pick(g.name, load)} busy={busy === g.name} />
              <span className={`sub tgroup-default ${g.load === g.default ? "" : "changed"}`}>{t(g.load === g.default ? "tgroup.default.is" : "tgroup.default.was", { load: loadWord(g.default) })}</span>
            </div>
          </div>
        );
      })}
      <div className="sub tgroup-note">{t("tgroup.note")}</div>
    </div>
  );
}

/** A session's Details: each group's state for this session, "Load now" for one on demand, and the
 *  session's own mode over the settings. */
export function SessionToolGroups({ sessionId, toast }: { sessionId: string; toast: (text: string) => void }) {
  const [groups, setGroups] = useState<SessionToolGroup[] | null>(null);
  const [open, setOpen] = useState("");
  const load = useCallback(() => {
    // An older host answers this path with something else; the panel is then empty, not the screen broken.
    api.get<{ groups: SessionToolGroup[] }>(`/api/sessions/${sessionId}/tool-groups`).then((r) => setGroups(Array.isArray(r?.groups) ? r.groups : [])).catch(() => setGroups([]));
  }, [sessionId]);
  useEffect(load, [load]);
  async function loadNow(name: string) {
    try {
      const r = await api.post<{ groups: SessionToolGroup[] }>(`/api/sessions/${sessionId}/tool-groups/${encodeURIComponent(name)}/load`);
      setGroups(r.groups);
      toast(t("tgroup.loadnow.done", { name: groupName(name) }));
    } catch (e) {
      toast(errorText(e));
    }
  }
  async function setMode(name: string, mode: ToolGroupLoad | null) {
    try {
      const r = await api.put<{ groups: SessionToolGroup[] }>(`/api/sessions/${sessionId}/tool-groups/${encodeURIComponent(name)}`, { load: mode });
      setGroups(r.groups);
    } catch (e) {
      toast(errorText(e));
    }
  }
  if (groups === null) return <div className="sub">{t("common.loading")}</div>;
  return (
    <div className="stgroups">
      <div className="sub">{t("tgroup.session.note")}</div>
      {groups.map((g) => {
        const chip = stateChip(g);
        const expanded = open === g.name;
        return (
          <div key={g.name} className={`stgroup ${expanded ? "open" : ""}`} data-group={g.name}>
            <div className="dt-row">
              <button className="linkbtn grow truncate stgroup-name" aria-expanded={expanded} onClick={() => setOpen(expanded ? "" : g.name)} title={groupAbout(g.name, g.description)}>
                {groupName(g.name)}
                {g.source === "session" && <span className="sub"> · {t("tgroup.session.own")}</span>}
              </button>
              <span className={`chip ${chip.tone}`} data-state={g.state}>{chip.word}</span>
              {g.state === "deferred" && (
                <button className="btn small" onClick={() => loadNow(g.name)}>{t("tgroup.loadnow")}</button>
              )}
            </div>
            {expanded && (
              <div className="stgroup-body">
                <div className="sub">{groupAbout(g.name, g.description)} · {plural("tgroup.tools", g.tools)}</div>
                <LoadPicker label={groupName(g.name)} value={g.load} onPick={(mode) => setMode(g.name, mode)} />
                {g.source === "session" ? (
                  <button className="linkbtn" onClick={() => setMode(g.name, null)}>{t("tgroup.session.follow")}</button>
                ) : (
                  <span className="sub">{t(g.source === "settings" ? "tgroup.session.fromsettings" : "tgroup.session.fromdefault")}</span>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
