import { Buffer } from "node:buffer"
import { randomUUID } from "node:crypto"
import {
  type APIRequestContext,
  type APIResponse,
  expect,
  type Page,
  test,
} from "@playwright/test"

import { getWorkspaceId } from "./utils/auth"

const ACTION = "core.transform.flatten_json"
const DEFAULT_WORKFLOW_MODEL_SETTINGS = { max_tokens: 256 }
const LIVE_AGENT_TEST_TIMEOUT_MS = 300_000
const OPENAI_MODEL_NAME = "gpt-5.4-mini"
const WORKFLOW_ID_PREFIX = "wf_"
const LEGACY_WORKFLOW_ID_PREFIX = "wf-"
const BASE62_ALPHABET =
  "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"

test.skip(
  process.env.RUN_AGENT_UX_CANARY !== "1",
  "Set RUN_AGENT_UX_CANARY=1 to run the live agent UX canary"
)

test.setTimeout(LIVE_AGENT_TEST_TIMEOUT_MS)
test.describe.configure({ mode: "serial" })

type JsonObject = Record<string, unknown>
type RequestPostOptions = NonNullable<Parameters<APIRequestContext["post"]>[1]>
type MultipartPayload = NonNullable<RequestPostOptions["multipart"]>

type ProviderSpec = {
  name: string
  modelProvider: string
  modelName: string
  catalogId: string
}

type CatalogList = {
  items?: Array<{
    id: string
    model_provider?: string
    model_name?: string
    custom_provider_id?: string | null
  }>
}

type AgentPresetRead = {
  id: string
  name: string
}

type WorkflowRead = {
  id: string
}

type WorkflowExecutionCreate = {
  wf_exec_id: string
}

type AgentSessionRead = {
  messages?: Array<{
    kind?: string
    approval?: {
      tool_call_id?: string
    } | null
  }>
}

type AgentSessionListItem = {
  id: string
}

type VercelSessionRead = {
  messages?: Array<{
    role?: string
    parts?: Array<{
      type?: string
      text?: string
    }>
  }>
}

test.describe("agent UX canary", () => {
  test("agent chat renders streaming tool use and persisted reload state", async ({
    page,
    request,
  }) => {
    await page.goto("/workspaces")
    const workspaceId = await getWorkspaceId(page)
    const provider = await ensurePrimaryProvider(request)
    const sentinel = newSentinel()
    const preset = await createAgentPreset(request, workspaceId, provider, {
      name: `Agent UX smoke ${sentinel}`,
      actions: [ACTION],
    })

    await page.goto(`/workspaces/${workspaceId}/agents/${preset.id}`)
    await page.getByRole("tab", { name: "Chat" }).click()

    const input = page.getByPlaceholder(`Talk to ${preset.name}...`)
    await expect(input).toBeVisible({ timeout: 30_000 })
    await input.fill(
      `Use the ${ACTION} tool exactly once with JSON {"ui":{"sentinel":"${sentinel}"}}. After the tool completes, reply exactly with ${sentinel}.`
    )
    await clickFirstEnabledButton(page, "Submit")

    await expectExactTextVisible(page, sentinel, 180_000)
    await waitForPersistedAgentPresetReply(request, workspaceId, {
      presetId: preset.id,
      text: sentinel,
    })

    await page.reload()
    await expectExactTextVisible(page, sentinel, 60_000)
  })

  test("workflow inbox renders agent approval and persisted reload state", async ({
    page,
    request,
  }) => {
    await page.goto("/workspaces")
    const workspaceId = await getWorkspaceId(page)
    const provider = await ensurePrimaryProvider(request)
    const sentinel = newSentinel()
    const sessionId = randomUUID()
    const sessionTitle = `Agent workflow UX smoke ${sentinel}`
    const workflowTitle = `Agent UX smoke ${sentinel}`

    await createWorkflowAgentRun(request, workspaceId, provider, {
      sessionId,
      sessionTitle,
      workflowTitle,
      prompt: `Use the ${ACTION} tool exactly once with JSON {"workflow":{"sentinel":"${sentinel}"}}. The tool requires approval. After approval, reply exactly with ${sentinel}.`,
    })
    await waitForApprovalRequest(request, workspaceId, sessionId)

    await page.goto(`/workspaces/${workspaceId}/inbox`)
    await openInboxSession(page, workflowTitle)
    await approveVisibleToolCall(page)
    await expectExactTextVisible(page, sentinel, 180_000)
    await waitForPersistedSessionReply(
      request,
      workspaceId,
      sessionId,
      sentinel
    )

    await page.reload()
    await openInboxSession(page, workflowTitle)
    await expectExactTextVisible(page, sentinel, 60_000)
  })
})

async function ensurePrimaryProvider(
  request: APIRequestContext
): Promise<ProviderSpec> {
  return await ensureBuiltInProvider(request, {
    providerName: "openai",
    keyEnv: "OPENAI_API_KEY",
    credentialKey: "OPENAI_API_KEY",
    modelName: OPENAI_MODEL_NAME,
  })
}

async function ensureBuiltInProvider(
  request: APIRequestContext,
  options: {
    providerName: string
    keyEnv: string
    credentialKey: string
    modelName: string
  }
): Promise<ProviderSpec> {
  const apiKey = requireEnv(options.keyEnv)
  const modelName = options.modelName
  await upsertProviderCredentials(request, {
    providerName: options.providerName,
    credentials: { [options.credentialKey]: apiKey },
  })
  const catalogId = await findCatalogId(request, {
    provider: options.providerName,
    modelName,
  })
  await setDefaultModel(request, catalogId)
  return {
    name: options.providerName,
    modelProvider: options.providerName,
    modelName,
    catalogId,
  }
}

async function upsertProviderCredentials(
  request: APIRequestContext,
  options: {
    providerName: string
    credentials: JsonObject
  }
): Promise<void> {
  const response = await request.post("/api/agent/credentials", {
    data: {
      provider: options.providerName,
      credentials: options.credentials,
    },
  })
  if (response.status() === 201) {
    return
  }

  const body = await response.text()
  if (
    response.status() === 400 &&
    isDuplicateProviderCredentialsResponse(body, options.providerName)
  ) {
    await putJson<JsonObject>(
      request,
      `/api/agent/credentials/${encodeURIComponent(options.providerName)}`,
      { credentials: options.credentials },
      [200]
    )
    return
  }

  throw new Error(
    `Failed to upsert ${options.providerName} credentials: HTTP ${response.status()}${body ? ` ${body}` : ""}`
  )
}

function isDuplicateProviderCredentialsResponse(
  body: string,
  providerName: string
): boolean {
  return (
    body.includes("duplicate key value violates unique constraint") &&
    body.includes(`agent-${providerName}-credentials`)
  )
}

async function findCatalogId(
  request: APIRequestContext,
  options: {
    provider: string
    modelName: string
    customProviderId?: string
  }
): Promise<string> {
  const catalog = await getJson<CatalogList>(
    request,
    `/api/organization/agent-catalog?provider=${encodeURIComponent(options.provider)}&model_name=${encodeURIComponent(options.modelName)}&limit=100`
  )
  const item = catalog.items?.find((entry) => {
    if (entry.model_provider !== options.provider) return false
    if (entry.model_name !== options.modelName) return false
    if (
      options.customProviderId &&
      entry.custom_provider_id !== options.customProviderId
    ) {
      return false
    }
    return true
  })
  if (!item) {
    throw new Error(
      `Catalog entry not found for ${options.provider}/${options.modelName}`
    )
  }
  return item.id
}

async function setDefaultModel(
  request: APIRequestContext,
  catalogId: string
): Promise<void> {
  await putJson<JsonObject>(
    request,
    "/api/agent/default-model-selection",
    { catalog_id: catalogId },
    [200]
  )
}

async function createAgentPreset(
  request: APIRequestContext,
  workspaceId: string,
  provider: ProviderSpec,
  options: {
    name: string
    actions?: string[]
    toolApprovals?: Record<string, boolean>
  }
): Promise<AgentPresetRead> {
  const slug = options.name.toLowerCase().replaceAll("_", "-")
  return await postJson<AgentPresetRead>(
    request,
    `/api/workspaces/${workspaceId}/agent/presets`,
    {
      name: options.name,
      slug,
      description: "Automated live agent UX smoke preset",
      instructions:
        "You are running a Tracecat browser smoke test. Follow the user instruction exactly and include requested sentinel strings verbatim.",
      model_name: provider.modelName,
      model_provider: provider.modelProvider,
      catalog_id: provider.catalogId,
      actions: options.actions ?? null,
      tool_approvals: options.toolApprovals ?? null,
      retries: 0,
      enable_thinking: false,
      enable_internet_access: false,
    },
    [201]
  )
}

async function createWorkflowAgentRun(
  request: APIRequestContext,
  workspaceId: string,
  provider: ProviderSpec,
  options: {
    sessionId: string
    sessionTitle: string
    workflowTitle: string
    prompt: string
  }
): Promise<WorkflowExecutionCreate> {
  const workflow = await createWorkflowDefinition(
    request,
    workspaceId,
    provider,
    {
      prompt: options.prompt,
      sessionId: options.sessionId,
      title: options.workflowTitle,
    }
  )
  const workflowEntityId = workflowIdToUuid(workflow.id)
  await postJson<JsonObject>(
    request,
    `/api/workspaces/${workspaceId}/agent/sessions`,
    {
      id: options.sessionId,
      title: options.sessionTitle,
      entity_type: "workflow",
      entity_id: workflowEntityId,
      tools: [ACTION],
    },
    [200]
  )
  return await postJson<WorkflowExecutionCreate>(
    request,
    `/api/workspaces/${workspaceId}/workflow-executions`,
    { workflow_id: workflow.id, inputs: null },
    [200]
  )
}

async function createWorkflowDefinition(
  request: APIRequestContext,
  workspaceId: string,
  provider: ProviderSpec,
  options: {
    prompt: string
    sessionId: string
    title: string
  }
): Promise<WorkflowRead> {
  const dsl = {
    title: options.title,
    description: "Automated live agent UX smoke workflow",
    entrypoint: { ref: "agent" },
    actions: [
      {
        ref: "agent",
        action: "ai.agent",
        args: {
          user_prompt: options.prompt,
          session_id: options.sessionId,
          model: {
            model_name: provider.modelName,
            model_provider: provider.modelProvider,
            catalog_id: provider.catalogId,
          },
          actions: [ACTION],
          tool_approvals: { [ACTION]: true },
          max_requests: 8,
          max_tool_calls: 3,
          retries: 0,
          enable_thinking: false,
          model_settings: workflowModelSettings(),
        },
      },
    ],
    returns: "${{ ACTIONS.agent.result }}",
  }

  const workflow = await postMultipart<WorkflowRead>(
    request,
    `/api/workspaces/${workspaceId}/workflows`,
    {
      file: {
        name: "agent-ux-smoke.json",
        mimeType: "application/json",
        buffer: Buffer.from(JSON.stringify({ definition: dsl })),
      },
      use_workflow_id: "false",
    },
    [201]
  )
  const commit = await postJson<{ status?: string }>(
    request,
    `/api/workspaces/${workspaceId}/workflows/${workflow.id}/commit`,
    {},
    [200]
  )
  expect(commit.status, JSON.stringify(commit)).toBe("success")
  return workflow
}

async function waitForApprovalRequest(
  request: APIRequestContext,
  workspaceId: string,
  sessionId: string
): Promise<void> {
  const deadline = Date.now() + 180_000
  while (Date.now() < deadline) {
    const session = await getJson<AgentSessionRead>(
      request,
      `/api/workspaces/${workspaceId}/agent/sessions/${sessionId}`
    )
    const hasApproval = session.messages?.some(
      (message) =>
        message.kind === "approval-request" && message.approval?.tool_call_id
    )
    if (hasApproval) return
    await delay(3_000)
  }
  throw new Error(`Timed out waiting for approval request in ${sessionId}`)
}

async function waitForPersistedAgentPresetReply(
  request: APIRequestContext,
  workspaceId: string,
  options: {
    presetId: string
    text: string
  }
): Promise<void> {
  const deadline = Date.now() + 60_000
  let lastSessionIds: string[] = []
  while (Date.now() < deadline) {
    const sessions = await getJson<AgentSessionListItem[]>(
      request,
      `/api/workspaces/${workspaceId}/agent/sessions?entity_type=agent_preset&entity_id=${encodeURIComponent(options.presetId)}&limit=10`
    )
    lastSessionIds = sessions.map((session) => session.id)
    for (const session of sessions) {
      if (
        await sessionHasPersistedAssistantText(
          request,
          workspaceId,
          session.id,
          options.text
        )
      ) {
        return
      }
    }
    await delay(1_000)
  }
  throw new Error(
    `Timed out waiting for persisted assistant reply for preset ${options.presetId}. Sessions: ${lastSessionIds.join(", ")}`
  )
}

async function waitForPersistedSessionReply(
  request: APIRequestContext,
  workspaceId: string,
  sessionId: string,
  text: string
): Promise<void> {
  const deadline = Date.now() + 60_000
  while (Date.now() < deadline) {
    if (
      await sessionHasPersistedAssistantText(
        request,
        workspaceId,
        sessionId,
        text
      )
    ) {
      return
    }
    await delay(1_000)
  }
  throw new Error(
    `Timed out waiting for persisted assistant reply in session ${sessionId}`
  )
}

async function sessionHasPersistedAssistantText(
  request: APIRequestContext,
  workspaceId: string,
  sessionId: string,
  text: string
): Promise<boolean> {
  const session = await getJson<VercelSessionRead>(
    request,
    `/api/workspaces/${workspaceId}/agent/sessions/${sessionId}/vercel`
  )
  return (
    session.messages?.some(
      (message) =>
        message.role === "assistant" &&
        message.parts?.some(
          (part) => part.type === "text" && part.text === text
        )
    ) ?? false
  )
}

async function expectExactTextVisible(
  page: Page,
  text: string,
  timeout: number
): Promise<void> {
  await expect(page.getByText(text, { exact: true }).first()).toBeVisible({
    timeout,
  })
}

async function approveVisibleToolCall(page: Page): Promise<void> {
  const approveButton = page.getByRole("button", { name: /^Approve$/ }).first()
  await expect(approveButton).toBeVisible({
    timeout: 180_000,
  })
  await approveButton.click()
  await clickFirstEnabledButton(page, /^Submit$/)
}

async function clickFirstEnabledButton(
  page: Page,
  name: string | RegExp,
  timeoutMs = 10_000
): Promise<void> {
  const deadline = Date.now() + timeoutMs
  const buttons = page.getByRole("button", { name })
  while (Date.now() < deadline) {
    const count = await buttons.count()
    for (let index = 0; index < count; index += 1) {
      const button = buttons.nth(index)
      const isVisible = await button.isVisible().catch(() => false)
      const isEnabled = await button.isEnabled().catch(() => false)
      if (isVisible && isEnabled) {
        await button.click()
        return
      }
    }
    await delay(250)
  }
  throw new Error(`No enabled ${name} button found`)
}

async function openInboxSession(
  page: Page,
  sessionTitle: string
): Promise<void> {
  const item = page.getByRole("button").filter({ hasText: sessionTitle })
  await expect(item).toBeVisible({ timeout: 60_000 })
  await item.click()
}

async function getJson<T>(
  request: APIRequestContext,
  url: string,
  expectedStatuses = [200]
): Promise<T> {
  return await readJson<T>(await request.get(url), expectedStatuses)
}

async function postJson<T>(
  request: APIRequestContext,
  url: string,
  data: JsonObject,
  expectedStatuses = [200]
): Promise<T> {
  return await readJson<T>(await request.post(url, { data }), expectedStatuses)
}

async function putJson<T>(
  request: APIRequestContext,
  url: string,
  data: JsonObject,
  expectedStatuses = [200]
): Promise<T> {
  return await readJson<T>(await request.put(url, { data }), expectedStatuses)
}

async function postMultipart<T>(
  request: APIRequestContext,
  url: string,
  multipart: MultipartPayload,
  expectedStatuses = [200]
): Promise<T> {
  return await readJson<T>(
    await request.post(url, { multipart }),
    expectedStatuses
  )
}

async function readJson<T>(
  response: APIResponse,
  expectedStatuses: number[]
): Promise<T> {
  if (!expectedStatuses.includes(response.status())) {
    throw new Error(await response.text())
  }
  if ((await response.body()).length === 0) {
    return {} as T
  }
  return (await response.json()) as T
}

function newSentinel(): string {
  return `TC_SMOKE_UI_${randomUUID().replaceAll("-", "").slice(0, 12).toUpperCase()}`
}

function requireEnv(name: string): string {
  const value = process.env[name]
  if (!value) {
    throw new Error(`Set ${name} to run the agent UX canary`)
  }
  return value
}

function workflowModelSettings(): JsonObject {
  return DEFAULT_WORKFLOW_MODEL_SETTINGS
}

function workflowIdToUuid(workflowId: string): string {
  if (isUuid(workflowId)) {
    return workflowId
  }
  if (workflowId.startsWith(LEGACY_WORKFLOW_ID_PREFIX)) {
    return uuidFromHex(workflowId.slice(LEGACY_WORKFLOW_ID_PREFIX.length))
  }
  if (workflowId.startsWith(WORKFLOW_ID_PREFIX)) {
    return uuidFromTracecatShortId(workflowId, WORKFLOW_ID_PREFIX)
  }
  throw new Error(`Unsupported workflow ID format: ${workflowId}`)
}

function uuidFromTracecatShortId(id: string, prefix: string): string {
  let value = BigInt(0)
  for (const char of id.slice(prefix.length)) {
    const digit = BASE62_ALPHABET.indexOf(char)
    if (digit === -1) {
      throw new Error(`Invalid base62 character in workflow ID: ${char}`)
    }
    value = value * BigInt(62) + BigInt(digit)
  }
  return uuidFromHex(value.toString(16).padStart(32, "0"))
}

function uuidFromHex(hex: string): string {
  if (!/^[0-9a-fA-F]{32}$/.test(hex)) {
    throw new Error(`Invalid workflow UUID hex: ${hex}`)
  }
  const normalized = hex.toLowerCase()
  return `${normalized.slice(0, 8)}-${normalized.slice(8, 12)}-${normalized.slice(12, 16)}-${normalized.slice(16, 20)}-${normalized.slice(20)}`
}

function isUuid(value: string): boolean {
  return /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/.test(
    value
  )
}

async function delay(ms: number): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, ms))
}
