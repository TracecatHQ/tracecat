import { CheckCheckIcon, CopyIcon } from "lucide-react"
import React from "react"
import { Button } from "@/components/ui/button"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { cn, copyToClipboard } from "@/lib/utils"

export function CopyButton({
  value,
  toastMessage,
  tooltipMessage,
  className,
  iconClassName,
}: {
  value: string
  toastMessage: string
  tooltipMessage?: string
  className?: string
  iconClassName?: string
}) {
  const [copied, setCopied] = React.useState(false)
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          type="button"
          variant="ghost"
          className={cn("group m-0 size-4 p-0", className)}
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
            <CheckCheckIcon
              className={cn("size-3 text-muted-foreground", iconClassName)}
            />
          ) : (
            <CopyIcon
              className={cn("size-3 text-muted-foreground", iconClassName)}
            />
          )}
        </Button>
      </TooltipTrigger>
      <TooltipContent>{tooltipMessage || "Copy"}</TooltipContent>
    </Tooltip>
  )
}
