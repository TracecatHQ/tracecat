import {
  matchesSkillAgentFilter,
  parseSkillAgentFilter,
  skillIdsForAgents,
  writeSkillAgentFilter,
} from "@/lib/skill-catalog-filters"

const PRESETS = [
  { id: "triage", skill_ids: ["s1", "s2"] },
  { id: "enrich", skill_ids: ["s2", "s3"] },
  { id: "empty", skill_ids: [] },
]

it("round-trips the agent filter through URL params, keeping other params", () => {
  const params = writeSkillAgentFilter(
    new URLSearchParams("view=list&agent=stale"),
    ["triage", "enrich"]
  )

  expect(params.get("view")).toBe("list")
  expect(parseSkillAgentFilter(params)).toEqual(["triage", "enrich"])
  expect(writeSkillAgentFilter(params, []).toString()).toBe("view=list")
  expect(parseSkillAgentFilter(null)).toEqual([])
})

it("matches every skill when no agent is selected", () => {
  expect(skillIdsForAgents(PRESETS, [])).toBeNull()
})

it("unions the skills of the selected agents", () => {
  expect(skillIdsForAgents(PRESETS, ["triage", "enrich"])).toEqual(
    new Set(["s1", "s2", "s3"])
  )
  expect(skillIdsForAgents(PRESETS, ["empty"])).toEqual(new Set())
  expect(skillIdsForAgents(PRESETS, ["deleted"])).toEqual(new Set())
})

it("matches only selected skills, never folders, under an active filter", () => {
  const skill = { type: "skill", id: "s1" } as const
  const folder = { type: "folder", id: "s1" } as const

  expect(matchesSkillAgentFilter(skill, null)).toBe(true)
  expect(matchesSkillAgentFilter(folder, null)).toBe(true)
  expect(matchesSkillAgentFilter(skill, new Set(["s1"]))).toBe(true)
  expect(matchesSkillAgentFilter(skill, new Set(["s2"]))).toBe(false)
  expect(matchesSkillAgentFilter(folder, new Set(["s1"]))).toBe(false)
})
