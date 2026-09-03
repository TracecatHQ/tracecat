/**
 * Build the default landing path for a workspace.
 */
export function getWorkspaceLandingPath(workspaceId: string): string {
  return `/workspaces/${workspaceId}/chat`
}
