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

test("analyst builds, persists and clears grouped conditions through the real case screen", async ({
  page,
  seed,
}) => {
  await page.goto(`/workspaces/${seed.workspace}/cases`)
  await page.getByRole("textbox", { name: "Search cases" }).fill(seed.prefix)
  async function showRows(count: number) {
    const group = page.getByRole("button", {
      name: `New ${count}`,
      exact: true,
    })
    await expect(group).toBeVisible()
    if ((await group.getAttribute("aria-expanded")) === "false")
      await group.click()
  }
  await showRows(5)
  await expect(
    page.getByText(`${seed.prefix} low`, { exact: true })
  ).toBeVisible()
  await page.getByRole("button", { name: "Conditions", exact: true }).click()
  const dialog = page.getByRole("dialog", { name: "Case conditions" })
  async function condition(
    index: number,
    field: string,
    op: string,
    value: string,
    path?: string
  ) {
    await dialog
      .getByLabel("Condition field", { exact: true })
      .nth(index)
      .selectOption(field)
    if (path) await dialog.getByLabel("Payload path").last().fill(path)
    await dialog.getByLabel("Condition operator").nth(index).selectOption(op)
    const input = dialog
      .getByLabel("Condition value", { exact: true })
      .nth(index)
    if (await input.evaluate((element) => element.tagName === "SELECT"))
      await input.selectOption(value)
    else await input.fill(value)
  }
  await condition(
    0,
    "payload.<path>",
    "eq",
    "Microsoft",
    "metadata.product.vendor_name"
  )
  await dialog
    .getByRole("button", { name: "Add condition", exact: true })
    .click()
  await condition(
    1,
    "payload.<path>",
    "eq",
    "Defender",
    "metadata.product.name"
  )
  await dialog.getByRole("button", { name: "Add group", exact: true }).click()
  await dialog.getByLabel("Group matching").nth(1).selectOption("any")
  await condition(2, `fields.${seed.score}`, "gte", "80")
  await dialog.getByLabel("Include unset").nth(2).check()
  await dialog
    .getByRole("button", { name: "Add condition", exact: true })
    .first()
    .click()
  await condition(3, "priority", "gte", "critical")
  await expect(
    dialog
      .getByLabel("Condition value")
      .nth(3)
      .getByRole("option", { name: "unknown", exact: true })
  ).toHaveCount(0)
  await dialog.getByRole("button", { name: "Apply conditions" }).click()
  await showRows(2)
  await expect(
    page.getByText(`${seed.prefix} high`, { exact: true })
  ).toBeVisible()
  await expect(
    page.getByText(`${seed.prefix} low`, { exact: true })
  ).toHaveCount(0)
  await page.reload()
  await expect(
    page.getByRole("button", { name: "Conditions (5)" })
  ).toBeVisible()
  await showRows(2)
  await expect(
    page.getByText(`${seed.prefix} unset`, { exact: true })
  ).toBeVisible()
  await page.getByRole("button", { name: "Conditions (5)" }).click()
  await dialog
    .getByRole("button", { name: "Add condition", exact: true })
    .last()
    .click()
  await condition(4, "created_at", "gte", "2000-01-01T00:00")
  const dated = page.waitForRequest(
    (request) =>
      new URL(request.url()).pathname.endsWith("/cases/search") &&
      request.method() === "POST"
  )
  await dialog.getByRole("button", { name: "Apply conditions" }).click()
  expect((await dated).postDataJSON().filters.and.at(-1).value).toBe(
    "2000-01-01T05:00:00.000Z"
  )
  await page
    .getByRole("button", { name: "Clear conditions", exact: true })
    .click()
  await showRows(5)
  await expect(
    page.getByText(`${seed.prefix} low`, { exact: true })
  ).toBeVisible()
  await page.getByRole("button", { name: "Conditions", exact: true }).click()
  await condition(0, "summary", "in", Array(1001).fill("synthetic").join(","))
  await expect(dialog.getByRole("alert")).toContainText("at most 1000 values")
  await expect(
    dialog.getByRole("button", { name: "Apply conditions" })
  ).toBeDisabled()
  await dialog
    .getByLabel("Condition value", { exact: true })
    .fill(Array(1000).fill(`${seed.prefix} high`).join(","))
  await dialog.getByRole("button", { name: "Apply conditions" }).click()
  await showRows(1)
  await expect(
    page.getByText(`${seed.prefix} high`, { exact: true })
  ).toBeVisible()
  await expect(
    page.getByText(`${seed.prefix} low`, { exact: true })
  ).toHaveCount(0)
  const remove = page.getByRole("button", { name: /^Remove Summary is one of/ })
  await expect(remove.getByText("×", { exact: true })).toBeVisible()
  await remove.click()
  await showRows(5)
})
