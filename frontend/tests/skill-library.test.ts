import type { LibrarySkillRead } from "@/client"
import {
  groupLibraryProviders,
  libraryEntryPath,
  libraryMaintainer,
  libraryProviderMatches,
  libraryProviderSlug,
  librarySkillCategory,
  librarySkillPath,
  librarySkillTitle,
} from "@/lib/skill-library"

function skill(
  slug: string,
  group: string | null,
  installed = false
): LibrarySkillRead {
  return {
    slug,
    description: `${slug} description`,
    installed,
    source: group
      ? {
          group,
          provider: null,
          kind: "upstream",
          repo: `${group.toLowerCase()}/skills`,
          commit: "a".repeat(40),
          license: "MIT",
          url: `https://github.com/${group.toLowerCase()}/skills/tree/${"a".repeat(40)}/skills/${slug}`,
        }
      : null,
  }
}

describe("groupLibraryProviders", () => {
  it("groups skills by provider, sorted, with install counts", () => {
    const providers = groupLibraryProviders([
      skill("triage", "Elastic", true),
      skill("audit", "Cloudflare"),
      skill("cases", "Elastic"),
    ])

    expect(providers.map((provider) => provider.name)).toEqual([
      "Cloudflare",
      "Elastic",
    ])
    const elastic = providers[1]
    expect(elastic.slug).toBe("elastic")
    expect(elastic.skills.map((entry) => entry.slug)).toEqual([
      "cases",
      "triage",
    ])
    expect(elastic.installedCount).toBe(1)
    expect(elastic.repos).toEqual(["elastic/skills"])
  })

  it("links a single-skill provider to its repository", () => {
    const [cloudflare] = groupLibraryProviders([skill("audit", "Cloudflare")])

    expect(cloudflare.repos).toEqual(["cloudflare/skills"])
  })

  it("puts skills without a source under Tracecat", () => {
    const [provider] = groupLibraryProviders([skill("local", null)])

    expect(provider.name).toBe("Tracecat")
    expect(provider.repos).toEqual([])
  })

  it("keeps every maintainer of a group on one page, listing upstream repos only", () => {
    const iam = skill("aws-iam", "AWS", true)
    const local: LibrarySkillRead = {
      ...skill("aws-incident-response", "AWS"),
      source: {
        group: "AWS",
        provider: "Tracecat",
        kind: "local",
        repo: null,
        commit: null,
        url: null,
        license: "AGPL-3.0-only",
      },
    }
    const [group] = groupLibraryProviders([local, iam])
    expect(group.name).toBe("AWS")
    expect(group.slug).toBe("aws")
    expect(group.skills.map((entry) => entry.slug)).toEqual([
      "aws-iam",
      "aws-incident-response",
    ])
    expect(group.installedCount).toBe(1)
    expect(group.repos).toEqual(["aws/skills"])
  })
})

describe("libraryMaintainer", () => {
  it.each([
    [undefined, "official"],
    [null, "official"],
    ["Tracecat", "tracecat"],
    ["Someone", "community"],
  ] as const)("classifies %j as %s", (provider, expected) => {
    expect(libraryMaintainer(provider)).toBe(expected)
  })
})

describe("libraryProviderMatches", () => {
  const [provider] = groupLibraryProviders([skill("alert-triage", "Elastic")])

  it.each([
    ["", true],
    ["elastic", true],
    ["triage", true],
    ["description", true],
    ["splunk", false],
  ])("matches %j: %s", (query, expected) => {
    expect(libraryProviderMatches(provider, query)).toBe(expected)
  })
})

describe("libraryProviderSlug", () => {
  it("lowercases and hyphenates names", () => {
    expect(libraryProviderSlug("Palo Alto Networks")).toBe("palo-alto-networks")
  })
})

function standalone(slug: string): LibrarySkillRead {
  return {
    slug,
    description: `${slug} description`,
    installed: false,
    source: {
      group: null,
      provider: "Tracecat",
      kind: "local",
      repo: null,
      commit: null,
      url: null,
      license: "AGPL-3.0-only",
    },
  }
}

describe("standalone library skills", () => {
  it("lists each groupless skill as its own entry", () => {
    const entries = groupLibraryProviders([
      standalone("malware-triage"),
      standalone("alert-triage"),
      skill("triage", "Elastic"),
      skill("cases", "Elastic"),
    ])

    expect(entries.map((entry) => [entry.name, entry.standalone])).toEqual([
      ["alert-triage", true],
      ["Elastic", false],
      ["malware-triage", true],
    ])
  })

  it("carries the group description onto its entry", () => {
    const aws = skill("aws-iam", "AWS")
    if (!aws.source) throw new Error("Missing fixture source")
    aws.source.group_description = "Amazon Web Services."
    const [entry] = groupLibraryProviders([aws, standalone("alert-triage")])

    expect(entry.description).toBeNull()
    expect(groupLibraryProviders([aws])[0].description).toBe(
      "Amazon Web Services."
    )
  })

  it("nests a standalone preview under its maintainer", () => {
    expect(librarySkillPath("ws", standalone("malware-triage"))).toBe(
      "/workspaces/ws/skills/library/tracecat/malware-triage"
    )
  })
})

describe("library entry summaries", () => {
  it("uses the group summary for groups and the skill summary for standalone skills", () => {
    const aws = skill("aws-iam", "AWS")
    const triage = standalone("alert-triage")
    if (!aws.source || !triage.source) throw new Error("Missing fixture source")
    aws.source.group_summary = "IAM review and incident response for AWS"
    triage.source.summary = "Decide whether an alert is real"
    const entries = groupLibraryProviders([aws, triage])

    expect(entries.map((entry) => entry.summary)).toEqual([
      "Decide whether an alert is real",
      "IAM review and incident response for AWS",
    ])
    expect(libraryProviderMatches(entries[0], "alert is real")).toBe(true)
  })
})

describe("libraryEntryPath", () => {
  it("links a one-skill entry straight to its preview", () => {
    const [cloudflare] = groupLibraryProviders([skill("audit", "Cloudflare")])

    expect(libraryEntryPath("ws", cloudflare)).toBe(
      "/workspaces/ws/skills/library/cloudflare/audit"
    )
  })

  it("links a multi-skill group to its overview", () => {
    const [elastic] = groupLibraryProviders([
      skill("triage", "Elastic"),
      skill("cases", "Elastic"),
    ])

    expect(libraryEntryPath("ws", elastic)).toBe(
      "/workspaces/ws/skills/library/elastic"
    )
  })

  it("falls back to the Tracecat segment for unsourced skills", () => {
    expect(librarySkillPath("ws", { slug: "triage", source: null })).toBe(
      "/workspaces/ws/skills/library/tracecat/triage"
    )
  })
})

describe("librarySkillTitle", () => {
  it.each([
    ["alert-triage", "Alert Triage"],
    ["adversary-behavior-mapping", "Adversary Behavior Mapping"],
    ["soc2-report-review", "SOC 2 Report Review"],
  ])("titles %s as %s", (slug, title) => {
    expect(librarySkillTitle(slug)).toBe(title)
  })

  it("matches a standalone entry by its title", () => {
    const [entry] = groupLibraryProviders([standalone("alert-triage")])

    expect(libraryProviderMatches(entry, "alert triage")).toBe(true)
  })
})

describe("librarySkillCategory", () => {
  it("leaves an unmapped skill without a category", () => {
    expect(librarySkillCategory("security-audit")).toBeNull()
  })
})
