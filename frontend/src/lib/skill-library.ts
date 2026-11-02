import type { LibrarySkillRead } from "@/client"

export interface LibraryProvider {
  /** Group name, or the skill slug for a standalone skill. */
  name: string
  /** URL segment for the provider page. */
  slug: string
  /** Standalone skills have no group and list as their own entry. */
  standalone: boolean
  /** What the group is and why its skills exist. */
  description: string | null
  /** One-line summary for the library list. */
  summary: string | null
  /** Distinct upstream repositories the group's skills come from. */
  repos: string[]
  skills: LibrarySkillRead[]
  installedCount: number
}

const UNSOURCED_PROVIDER = "Tracecat"

/**
 * Return the URL segment for a provider name.
 *
 * @param name Provider display name.
 * @returns Lowercase, hyphenated slug.
 */
export function libraryProviderSlug(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "")
}

/**
 * Return the upstream repository page for a library skill source.
 *
 * @param repo GitHub `owner/name`.
 * @returns The repository URL.
 */
export function librarySourceRepoUrl(repo: string): string {
  return `https://github.com/${repo}`
}

const TITLE_WORDS: Record<string, string> = {
  aws: "AWS",
  cti: "CTI",
  iam: "IAM",
  soc2: "SOC 2",
}

/**
 * Turn a skill slug into a display title.
 *
 * @param slug Hyphenated skill slug, e.g. `alert-triage`.
 * @returns Space-delimited title case, e.g. `Alert Triage`.
 */
export function librarySkillTitle(slug: string): string {
  return slug
    .split("-")
    .map(
      (word) =>
        TITLE_WORDS[word] ?? word.charAt(0).toUpperCase() + word.slice(1)
    )
    .join(" ")
}

export type LibrarySkillCategory =
  | "investigation"
  | "detection"
  | "exposure"
  | "compliance"

/**
 * Icon-only taxonomy for Tracecat's standalone skills; not shown as text or a
 * filter. Third-party standalone skills show their maintainer's logo instead.
 */
const SKILL_CATEGORIES: Record<string, LibrarySkillCategory> = {}

/**
 * Return the icon category of a standalone library skill.
 *
 * @param slug Library skill slug.
 * @returns Its category, or null when it has none yet.
 */
export function librarySkillCategory(
  slug: string
): LibrarySkillCategory | null {
  return SKILL_CATEGORIES[slug] ?? null
}

export type LibraryMaintainer = "official" | "tracecat" | "community"

/**
 * Classify who maintains a library skill relative to its group.
 *
 * @param provider The source's maintainer; null when the group maintains it.
 * @returns Official, Tracecat, or community.
 */
export function libraryMaintainer(
  provider: string | null | undefined
): LibraryMaintainer {
  if (!provider) return "official"
  return provider === UNSOURCED_PROVIDER ? "tracecat" : "community"
}

/**
 * Return the URL segment a library skill's preview nests under.
 *
 * @param source The skill's library source, if any.
 * @returns The group slug, or the maintainer slug for a standalone skill.
 */
export function librarySkillRouteSlug(
  source: LibrarySkillRead["source"]
): string {
  return libraryProviderSlug(
    source?.group ?? source?.provider ?? UNSOURCED_PROVIDER
  )
}

/**
 * Return the URL of a library skill's read-only preview.
 *
 * @param workspaceId Workspace identifier.
 * @param skill The library skill.
 * @returns The preview path.
 */
export function librarySkillPath(
  workspaceId: string,
  skill: Pick<LibrarySkillRead, "slug" | "source">
): string {
  return `/workspaces/${workspaceId}/skills/library/${librarySkillRouteSlug(skill.source)}/${skill.slug}`
}

/**
 * Return where a library entry links: its only skill, or its group page.
 *
 * @param workspaceId Workspace identifier.
 * @param provider The library entry.
 * @returns The skill preview path for one-skill entries, else the group page.
 */
export function libraryEntryPath(
  workspaceId: string,
  provider: LibraryProvider
): string {
  const [only, ...rest] = provider.skills
  if (only && rest.length === 0) return librarySkillPath(workspaceId, only)
  return `/workspaces/${workspaceId}/skills/library/${provider.slug}`
}

/**
 * Group library skills by the platform they work with; standalone skills
 * become entries of their own.
 *
 * @param skills Library skills with install state.
 * @returns Entries sorted by name, each with skills sorted by slug.
 */
export function groupLibraryProviders(
  skills: LibrarySkillRead[]
): LibraryProvider[] {
  const groups = new Map<string, LibrarySkillRead[]>()
  const entries: LibraryProvider[] = []
  for (const skill of skills) {
    if (skill.source && !skill.source.group) {
      entries.push(libraryEntry(skill.slug, [skill], true))
      continue
    }
    const name = skill.source?.group ?? UNSOURCED_PROVIDER
    groups.set(name, [...(groups.get(name) ?? []), skill])
  }
  for (const [name, members] of groups) {
    entries.push(libraryEntry(name, members, false))
  }
  return entries.sort((a, b) => a.name.localeCompare(b.name))
}

function libraryEntry(
  name: string,
  members: LibrarySkillRead[],
  standalone: boolean
): LibraryProvider {
  const sorted = [...members].sort((a, b) => a.slug.localeCompare(b.slug))
  return {
    name,
    slug: libraryProviderSlug(name),
    standalone,
    description: sorted[0]?.source?.group_description ?? null,
    summary: standalone
      ? (sorted[0]?.source?.summary ?? null)
      : (sorted[0]?.source?.group_summary ?? null),
    repos: [
      ...new Set(
        sorted.flatMap((skill) =>
          skill.source?.repo ? [skill.source.repo] : []
        )
      ),
    ].sort(),
    skills: sorted,
    installedCount: sorted.filter((skill) => skill.installed).length,
  }
}

/**
 * Check whether a provider or any of its skills matches a search query.
 *
 * @param provider Provider to test.
 * @param query Lowercased, trimmed search text.
 * @returns True when the query is empty or matches.
 */
export function libraryProviderMatches(
  provider: LibraryProvider,
  query: string
): boolean {
  if (!query) return true
  return (
    provider.name.toLowerCase().includes(query) ||
    (provider.summary?.toLowerCase().includes(query) ?? false) ||
    (provider.standalone &&
      librarySkillTitle(provider.name).toLowerCase().includes(query)) ||
    provider.skills.some(
      (skill) =>
        skill.slug.includes(query) ||
        (skill.source?.summary?.toLowerCase().includes(query) ?? false) ||
        (skill.description?.toLowerCase().includes(query) ?? false)
    )
  )
}
