/** Matches the backend's `PresetName` length limit. */
const AGENT_PRESET_NAME_MAX_LENGTH = 120

/**
 * Return the URL slug the backend derives from an agent preset name.
 *
 * @param name Preset display name.
 * @returns Lowercase, hyphenated slug.
 */
export function agentPresetSlug(name: string): string {
  // Fold accents like python-slugify: `Café` -> `cafe`.
  return name
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "")
}

/**
 * Number a preset name until its slug is free, e.g. `Alert Triage 2`.
 *
 * @param name The wanted display name.
 * @param takenSlugs Slugs of the workspace's existing presets.
 * @returns The name itself when free, else the first free numbered variant.
 */
export function uniqueAgentPresetName(
  name: string,
  takenSlugs: Iterable<string>
): string {
  const taken = new Set(takenSlugs)
  const base = name.trim()
  if (!taken.has(agentPresetSlug(base))) return base
  for (let n = 2; ; n++) {
    const suffix = ` ${n}`
    const candidate = `${base.slice(0, AGENT_PRESET_NAME_MAX_LENGTH - suffix.length).trimEnd()}${suffix}`
    if (!taken.has(agentPresetSlug(candidate))) return candidate
  }
}
