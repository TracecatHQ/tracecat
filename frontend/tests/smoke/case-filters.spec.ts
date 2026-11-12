import { randomUUID } from "node:crypto"
import { type APIResponse, test as base, expect } from "@playwright/test"
import type {
  CaseFilterMetadata,
  CaseRead,
  CaseSearchAggregateRead,
  CasesQueryCasesResponse,
  Filter,
  WorkspacesListWorkspacesResponse,
} from "@/client"

async function body<T>(response: Promise<APIResponse>): Promise<T> {
  const result = await response
  await expect(result).toBeOK()
  return result.json()
}

const test = base.extend<{
  seed: { workspace: string; prefix: string; score: string; cases: CaseRead[] }
}>({
  seed: async ({ request }, use) => {
    const [workspace] = await body<WorkspacesListWorkspacesResponse>(
      request.get("/api/workspaces")
    )
    const token = randomUUID().replaceAll("-", "")
    const seed = {
      workspace: workspace.id,
      prefix: `filters-${token}`,
      score: `score_${token}`,
      cases: [] as CaseRead[],
    }
    const api = `/api/workspaces/${workspace.id}`
    const product = {
      metadata: { product: { vendor_name: "Microsoft", name: "Defender" } },
    }
    let fieldCreated = false
    try {
      await body(
        request.post(`${api}/case-fields`, {
          data: { name: seed.score, display_name: seed.score, type: "INTEGER" },
        })
      )
      fieldCreated = true
      for (const [suffix, score, priority, payload] of [
        ["high", 90, "high", { ...product, value: 80 }],
        ["low", 20, "low", { ...product, value: "80" }],
        ["unset", null, "critical", { ...product, value: true }],
        [
          "other",
          95,
          "critical",
          {
            metadata: { product: { vendor_name: "Microsoft", name: "Other" } },
            value: null,
          },
        ],
        ["missing", null, "low", {}],
      ] as const) {
        seed.cases.push(
          await body<CaseRead>(
            request.post(`${api}/cases`, {
              data: {
                summary: `${seed.prefix} ${suffix}`,
                description: "Synthetic filter smoke test",
                status: "new",
                priority,
                severity: "low",
                payload,
                fields: score === null ? {} : { [seed.score]: score },
              },
            })
          )
        )
      }
      await use(seed)
    } finally {
      for (const item of seed.cases)
        await expect(await request.delete(`${api}/cases/${item.id}`)).toBeOK()
      if (fieldCreated)
        await expect(
          await request.delete(`${api}/case-fields/${seed.score}`)
        ).toBeOK()
    }
  },
})

test.use({ timezoneId: "America/New_York" })
test.describe.configure({ mode: "serial", timeout: 90_000 })

test("real case queries share grouped matching, counts, pagination and typed payload semantics", async ({
  request,
  seed,
}) => {
  const api = `/api/workspaces/${seed.workspace}`
  const query = { search_term: seed.prefix, search_mode: "literal" }
  async function search(filters: Filter, cursor?: string, limit = 100) {
    return body<CasesQueryCasesResponse>(
      request.post(`${api}/cases/search`, {
        data: { ...query, filters, cursor, limit },
      })
    )
  }
  const filters: Filter = {
    and: [
      {
        field: "payload.metadata.product.vendor_name",
        op: "eq",
        value: "Microsoft",
      },
      { field: "payload.metadata.product.name", op: "eq", value: "Defender" },
      {
        or: [
          { field: `fields.${seed.score}`, op: "gte", value: 80 },
          { field: `fields.${seed.score}`, op: "is_null" },
        ],
      },
    ],
  }
  const first = await search(filters, undefined, 1)
  expect(first.items).toHaveLength(1)
  expect(first.has_more).toBe(true)
  expect(first.total_estimate).toBe(2)
  const second = await search(filters, first.next_cursor!, 1)
  expect(second.has_more).toBe(false)
  expect(
    [...first.items, ...second.items].map((item) => item.id).sort()
  ).toEqual([seed.cases[0].id, seed.cases[2].id].sort())
  const counts = await body<CaseSearchAggregateRead>(
    request.post(`${api}/cases/search/aggregate`, {
      data: { ...query, filters },
    })
  )
  expect(counts.total).toBe(2)
  expect(counts.status_groups.new).toBe(2)
  // PostgreSQL keeps strings, numbers, booleans, missing paths and JSON null distinct.
  for (const [value, index] of [
    [80, 0],
    ["80", 1],
    [true, 2],
  ] as const) {
    expect(
      (await search({ field: "payload.value", op: "eq", value })).items.map(
        (item) => item.id
      )
    ).toEqual([seed.cases[index].id])
  }
  expect(
    (await search({ field: "payload.value", op: "is_null" })).items.map(
      (item) => item.id
    )
  ).toEqual([seed.cases[4].id])
  expect(
    (await search({ field: "payload.value", op: "ne", value: 80 })).items
      .map((item) => item.id)
      .sort()
  ).toEqual(
    seed.cases
      .slice(1, 4)
      .map((item) => item.id)
      .sort()
  )
  const metadata = await body<CaseFilterMetadata>(
    request.get(`${api}/cases/search/fields`)
  )
  expect(
    metadata.fields.find((field) => field.field === "priority")?.range_choices
  ).toEqual(["low", "medium", "high", "critical"])
  expect(
    metadata.fields.find((field) => field.field === "severity")?.range_choices
  ).toEqual(["informational", "low", "medium", "high", "critical", "fatal"])
  expect(
    (
      await request.post(`${api}/cases/search`, {
        data: {
          filters: {
            field: "summary",
            op: "in",
            value: Array(metadata.max_values! + 1).fill("synthetic"),
          },
        },
      })
    ).status()
  ).toBe(422)
})
