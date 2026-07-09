import type { AuditWebhookTestResult } from "@/client"

function formatErrorCategory(category: string | null | undefined): string {
  switch (category) {
    case "receiver_error":
      return "receiver error"
    case "timeout":
      return "timeout"
    case "request_error":
      return "request error"
    default:
      return "request failed"
  }
}

export function getAuditWebhookTestTitle(
  result: AuditWebhookTestResult
): string {
  return result.ok
    ? "Audit webhook test succeeded"
    : "Audit webhook test failed"
}

export function getAuditWebhookTestDescription(
  result: AuditWebhookTestResult
): string {
  if (result.receiver_status_code != null) {
    return `Receiver returned ${result.receiver_status_code}.`
  }
  return `Tracecat could not reach the receiver: ${formatErrorCategory(result.error_category)}.`
}
