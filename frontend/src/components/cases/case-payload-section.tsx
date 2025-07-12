import { Braces } from "lucide-react"
import type { CaseRead } from "@/client"
import { JsonViewWithControls } from "@/components/json-viewer"

export function CasePayloadSection({ caseData }: { caseData: CaseRead }) {
  return (
    <div className="space-y-4">
      {caseData.payload && Object.keys(caseData.payload).length > 0 ? (
        <JsonViewWithControls
          src={caseData.payload}
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
