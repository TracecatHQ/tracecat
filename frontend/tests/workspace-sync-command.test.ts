import type { SyncOperationRead } from "@/client"
import { syncOperationCommand } from "@/lib/workspace-sync-command"

const operation: SyncOperationRead = {
  id: "synthetic-operation",
  direction: "push",
  status: "ready",
  stage: "awaiting_confirmation",
  preview_revision: 1,
  state_revision: 3,
  created_at: "2026-01-01T00:00:00Z",
  expires_at: "2099-01-01T00:00:00Z",
  inputs: { id: "synthetic-operation", direction: "push" },
}

it("preserves the exact command after a lost response and a module reload", () => {
  const workspace = crypto.randomUUID()
  const first = syncOperationCommand(workspace, operation, "apply")
  expect(syncOperationCommand(workspace, { ...operation }, "apply")).toEqual(
    first
  )
  jest.isolateModules(() => {
    const { syncOperationCommand: reloaded } = jest.requireActual(
      "@/lib/workspace-sync-command"
    )
    expect(reloaded(workspace, operation, "apply")).toEqual(first)
  })
})

it("binds retry to one failure revision and separates different commands", () => {
  const workspace = crypto.randomUUID()
  const failed = { ...operation, status: "failed" as const, state_revision: 5 }
  const first = syncOperationCommand(workspace, failed, "retry")
  expect(syncOperationCommand(workspace, failed, "retry")).toEqual(first)
  expect(
    syncOperationCommand(workspace, { ...failed, state_revision: 7 }, "retry")
      .command_id
  ).not.toEqual(first.command_id)
  expect(
    syncOperationCommand(workspace, failed, "apply").command_id
  ).not.toEqual(first.command_id)
  expect(
    syncOperationCommand("other-workspace", failed, "retry").command_id
  ).not.toEqual(first.command_id)
})
