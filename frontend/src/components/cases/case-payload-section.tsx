import { Braces } from "lucide-react"
import type { CaseRead } from "@/client"
import { JsonViewWithControls } from "@/components/json-viewer"

// Payload can be a dict or list (type will be updated after regenerating client types)
type PayloadType = Record<string, unknown> | unknown[] | null

function hasPayloadContent(payload: PayloadType): boolean {
  if (payload === null || payload === undefined) {
    return false
  }
  if (Array.isArray(payload)) {
    return payload.length > 0
  }
  return Object.keys(payload).length > 0
}

export function CasePayloadSection({ caseData }: { caseData: CaseRead }) {
  // Cast to PayloadType to handle both dict and list (forward-compatible with updated types)
  const payload = caseData.payload as PayloadType
  return (
    <div className="space-y-4">
      {hasPayloadContent(payload) ? (
        <JsonViewWithControls
          src={payload}
          defaultTab="nested"
          defaultExpanded={true}
          showControls={true}
        />
      ) : (
        <NoPaylod />
      )}
    </div>
  )
}

function NoPaylod() {
  return (
    <div className="flex flex-col items-center justify-center py-4">
      <div className="p-2 rounded-full bg-muted/50 mb-3">
        <Braces className="h-6 w-6 text-muted-foreground" />
      </div>
      <h3 className="text-sm font-medium text-muted-foreground mb-1">
        No payload available
      </h3>
      <p className="text-xs text-muted-foreground/75 text-center max-w-[250px]">
        Payload data will appear here when added to the case
      </p>
    </div>
  )
}
