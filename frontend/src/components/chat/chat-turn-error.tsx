import {
  AlertCircleIcon,
  ArrowRight,
  ChevronRightIcon,
  RefreshCcwIcon,
  WifiOffIcon,
} from "lucide-react"
import Link from "next/link"
import type { ModelErrorDetail } from "@/client"
import { Action, Actions } from "@/components/ai-elements/actions"
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
import { describeModelError, modelErrorFacts } from "@/lib/model-errors"

/** The Retry action under a turn. */
export function RetryAction({ onRetry }: { onRetry: () => void }) {
  return (
    <Actions className="mt-4">
      <Action size="sm" onClick={onRetry} label="Retry" tooltip="Retry">
        <RefreshCcwIcon className="size-3" />
      </Action>
    </Actions>
  )
}

/**
 * A failed model request: what went wrong, how to fix it, and a collapsed
 * row of technical detail for support.
 */
function ModelErrorMessage({
  detail,
  error,
}: {
  detail: ModelErrorDetail
  error: string
}) {
  const copy = describeModelError(detail)
  return (
    <div role="alert" className="flex items-start gap-2 text-sm">
      <AlertCircleIcon className="mt-0.5 size-3.5 shrink-0 text-destructive" />
      <div className="min-w-0 space-y-1">
        <p className="font-medium text-destructive">{copy.title}</p>
        <p className="text-muted-foreground">
          {copy.action}
          {copy.settingsHref && (
            <Link
              href={copy.settingsHref}
              className="ml-1.5 inline-flex items-center gap-0.5 font-medium text-foreground underline-offset-4 hover:underline"
            >
              Agent settings
              <ArrowRight className="size-3" />
            </Link>
          )}
        </p>
        <Collapsible>
          <CollapsibleTrigger className="group inline-flex items-center gap-0.5 text-xs text-muted-foreground hover:text-foreground">
            <ChevronRightIcon className="size-3 transition-transform group-data-[state=open]:rotate-90" />
            Details
          </CollapsibleTrigger>
          <CollapsibleContent>
            <p className="mt-1 break-words font-mono text-xs text-muted-foreground">
              {[...modelErrorFacts(detail), error].join(" · ")}
            </p>
          </CollapsibleContent>
        </Collapsible>
      </div>
    </div>
  )
}

/**
 * A failed turn's error, appended after its transcript. A dropped connection
 * reads as neutral: the server-side turn may still be running. A failed model
 * request with structured detail reads as an actionable message.
 */
export function TurnErrorLine({
  error,
  modelError,
  dropped,
  onRetry,
}: {
  error: string
  /** Structured detail when a model request failed. */
  modelError?: ModelErrorDetail | null
  dropped: boolean
  /** Omit to hide Retry. */
  onRetry?: () => void
}) {
  let body: React.ReactNode
  if (dropped) {
    body = (
      <p role="alert" className="flex items-center gap-2 text-sm">
        <WifiOffIcon className="size-3.5 shrink-0 text-muted-foreground" />
        <span className="font-medium">Connection dropped</span>
        <span className="text-muted-foreground">
          The reply stopped loading.
        </span>
      </p>
    )
  } else if (modelError) {
    body = <ModelErrorMessage detail={modelError} error={error} />
  } else {
    body = (
      <p
        role="alert"
        className="flex items-start gap-2 text-sm text-destructive"
      >
        <AlertCircleIcon className="mt-0.5 size-3.5 shrink-0" />
        <span className="min-w-0 whitespace-pre-wrap break-words">{error}</span>
      </p>
    )
  }
  return (
    <div className="mt-4">
      {body}
      {onRetry && <RetryAction onRetry={onRetry} />}
    </div>
  )
}
