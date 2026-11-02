import {
  type SyncOperationCommand,
  type SyncOperationRead,
  workflowsApplySyncOperation,
  workflowsRetrySyncOperation,
} from "@/client"

type CommandKind = "apply" | "retry"
const commands = new Map<string, SyncOperationCommand>()

/** Reuse one confirmation identity across lost responses and page reloads. */
export function syncOperationCommand(
  workspaceId: string,
  operation: SyncOperationRead,
  kind: CommandKind
): SyncOperationCommand {
  const key = `git-sync-command:${workspaceId}:${operation.id}:${kind}`
  let saved: unknown = commands.get(key)
  try {
    const stored = sessionStorage.getItem(key)
    if (stored) saved = JSON.parse(stored)
  } catch {
    // Restricted browser storage must not prevent confirmation in this tab.
  }
  const previewRevision = operation.preview_revision ?? 0
  const stateRevision = operation.state_revision ?? 0
  if (
    saved &&
    typeof saved === "object" &&
    "command_id" in saved &&
    typeof saved.command_id === "string" &&
    "preview_revision" in saved &&
    saved.preview_revision === previewRevision &&
    "state_revision" in saved &&
    saved.state_revision === stateRevision
  ) {
    return {
      command_id: saved.command_id,
      preview_revision: previewRevision,
      state_revision: stateRevision,
    }
  }
  const command: SyncOperationCommand = {
    command_id: crypto.randomUUID(),
    preview_revision: previewRevision,
    state_revision: stateRevision,
  }
  commands.set(key, command)
  if (commands.size > 128) {
    const oldest = commands.keys().next().value
    if (oldest) commands.delete(oldest)
  }
  try {
    sessionStorage.setItem(key, JSON.stringify(command))
  } catch {
    // The bounded in-memory copy still protects retransmissions in this tab.
  }
  return command
}

/** Send Apply or Retry to the existing workflow with the exact reviewed revisions. */
export function sendSyncOperationCommand(
  workspaceId: string,
  operation: SyncOperationRead,
  kind: CommandKind
) {
  const request = {
    workspaceId,
    operationId: operation.id,
    requestBody: syncOperationCommand(workspaceId, operation, kind),
  }
  return kind === "apply"
    ? workflowsApplySyncOperation(request)
    : workflowsRetrySyncOperation(request)
}
