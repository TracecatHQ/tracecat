import { CheckCheckIcon, CopyIcon } from "lucide-react"
import React from "react"
import { Button } from "@/components/ui/button"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { copyToClipboard } from "@/lib/utils"

export function CopyButton({
  value,
  toastMessage,
  tooltipMessage,
}: {
  value: string
  toastMessage: string
  tooltipMessage?: string
}) {
  const [copied, setCopied] = React.useState(false)
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          type="button"
          variant="ghost"
          className="group m-0 size-4 p-0"
          onClick={(e) => {
            e.stopPropagation()
            copyToClipboard({
              value,
              message: toastMessage,
            })
            setCopied(true)
            setTimeout(() => setCopied(false), 2000)
          }}
        >
          {copied ? (
            <CheckCheckIcon className="size-3 text-muted-foreground" />
          ) : (
            <CopyIcon className="size-3 text-muted-foreground" />
          )}
        </Button>
      </TooltipTrigger>
      <TooltipContent>{tooltipMessage || "Copy"}</TooltipContent>
    </Tooltip>
  )
}
