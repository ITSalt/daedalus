// The model the history summariser runs on, chosen in Settings → Limits & budget.
//
// It sits apart from the settings screen so the one surprising rule can be tested on its own: a
// preset that was chosen here and later deleted stays in the configuration (the runtime quietly
// summarises with the session's own model instead), so the select must still show it — as missing —
// rather than silently displaying the first option and leaving the operator to believe it is in use.

import type { Preset } from "./api";
import { t } from "./i18n";

export type CompactionOption = { value: string; label: string; missing?: boolean };

/** The empty choice first, then every preset under the model picker's own label, then a missing current one. */
export function compactionOptions(presets: Record<string, Preset> | undefined, current: string): CompactionOption[] {
  const options: CompactionOption[] = [{ value: "", label: t("settings.compaction.model.own") }];
  for (const [id, p] of Object.entries(presets ?? {})) options.push({ value: id, label: p.label || `${p.provider}/${p.model}` });
  if (current && !(current in (presets ?? {}))) options.push({ value: current, label: t("settings.compaction.model.missing", { id: current }), missing: true });
  return options;
}

export function CompactionModelSelect({ presets, value, onSave }: { presets: Record<string, Preset> | undefined; value: string; onSave: (preset: string) => void }) {
  const options = compactionOptions(presets, value);
  const missing = options.some((o) => o.missing);
  return (
    <div>
      <label className="field" htmlFor="compaction-preset">{t("settings.compaction.model")}</label>
      <select
        id="compaction-preset"
        className="field"
        value={value}
        aria-invalid={missing || undefined}
        onChange={(e) => e.target.value !== value && onSave(e.target.value)}
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>
      {missing && <div className="sub">{t("settings.compaction.model.missing.hint")}</div>}
      <div className="sub faint">{t("settings.compaction.model.hint")}</div>
    </div>
  );
}
