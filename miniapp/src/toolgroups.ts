// The host's tool groups as the app words them: what each is called, how much of the month's work
// needed it, and which chip a session's group wears. Pure, so the settings page, the session panel
// and the chat say the same thing and a test can read it without a browser.

import type { SessionToolGroup, ToolGroupLoad } from "./api";
import { DICT, plural, t } from "./i18n";

export const LOADS: ToolGroupLoad[] = ["eager", "auto", "lazy"];

/** The group's name for a person: "Browser", "Браузер". An MCP server's group or one this build does
 *  not know yet shows its id rather than a bracketed key. */
export function groupName(name: string): string {
  return DICT[`tgroup.${name}`] ? t(`tgroup.${name}`) : name;
}

export function groupAbout(name: string, fallback: string): string {
  return DICT[`tgroup.${name}.about`] ? t(`tgroup.${name}.about`) : fallback;
}

/** A load mode as a button says it: "Always", "When it fits", "On demand". */
export function loadWord(load: ToolGroupLoad): string {
  return t(`tgroup.load.${load}`);
}

/**
 * The share of sessions that called any tool of the group, as a whole percent — but never "0 %" for a
 * group somebody did use: a group used once in two hundred sessions reads "<1 %", so rounding cannot
 * make a rare group look like a dead one.
 */
export function usageShare(used: number, total: number): { pct: number; label: string } {
  if (total <= 0 || used <= 0) return { pct: 0, label: "0" };
  const pct = (100 * used) / total;
  if (pct < 1) return { pct, label: "<1" };
  return { pct, label: String(Math.round(pct)) };
}

export function usageLine(used: number, total: number, days: number): string {
  if (total <= 0) return t("tgroup.usage.none", { days });
  return t("tgroup.usage", { pct: usageShare(used, total).label, days });
}

export type Chip = { word: string; tone: "" | "accent" | "ok" | "attn" };

/** The chip a session's group wears: what the model has in front of it right now. A group the core
 *  places by the window, before any run of the session has placed it, wears its mode instead of a
 *  guess at what the first run will do. */
export function stateChip(group: Pick<SessionToolGroup, "state" | "pending"> & Partial<Pick<SessionToolGroup, "load" | "loaded" | "tools">>): Chip {
  if (group.state === "undecided") return { word: loadWord(group.load ?? "auto"), tone: "" };
  if (group.state === "loaded" && group.pending) return { word: t("tgroup.state.pending"), tone: "attn" };
  // "Loaded" is the whole group; one tool a search brought in is not the browser loaded.
  if (group.state === "loaded" && group.loaded !== undefined && group.tools !== undefined && group.loaded < group.tools) {
    return { word: t("tgroup.state.partial", { n: group.loaded, total: group.tools }), tone: "ok" };
  }
  if (group.state === "loaded") return { word: t("tgroup.state.loaded"), tone: "ok" };
  if (group.state === "deferred") return { word: t("tgroup.state.deferred"), tone: "accent" };
  if (group.state === "off") return { word: t("tgroup.state.off"), tone: "" };
  return { word: t("tgroup.state.advertised"), tone: "" };
}

/** The group a ToolSearch call asked for whole: `group="browser"` or `select:"group:browser"`. */
export function searchedGroup(args: Record<string, unknown>): string {
  if (typeof args.group === "string" && args.group) return args.group;
  for (const key of ["select", "query"]) {
    const value = args[key];
    if (typeof value === "string" && value.startsWith("select:group:")) return value.slice("select:group:".length).trim();
    if (typeof value === "string" && value.startsWith("group:")) return value.slice("group:".length).trim();
  }
  return "";
}

/** "Browser (12 tools)" — the detail of a group-load step. */
export function groupDetail(group: string, tools: number): string {
  return tools > 0 ? `${groupName(group)} (${plural("tgroup.tools", tools)})` : groupName(group);
}
