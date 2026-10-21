/** Path of a case's detail page. */
export function caseHref(workspaceId: string, caseId: string): string {
  return `/workspaces/${workspaceId}/cases/${caseId}`
}
