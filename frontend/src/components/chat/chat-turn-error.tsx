import { AlertCircleIcon, RefreshCcwIcon, WifiOffIcon } from "lucide-react"
import { Action, Actions } from "@/components/ai-elements/actions"

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
 * A failed turn's error, appended after its transcript. A dropped connection
 * reads as neutral: the server-side turn may still be running.
 */
export function TurnErrorLine({
  error,
  dropped,
  onRetry,
}: {
  error: string
  dropped: boolean
  /** Omit to hide Retry. */
  onRetry?: () => void
}) {
  return (
    <div className="mt-4">
      {dropped ? (
        <p role="alert" className="flex items-center gap-2 text-sm">
          <WifiOffIcon className="size-3.5 shrink-0 text-muted-foreground" />
          <span className="font-medium">Connection dropped</span>
          <span className="text-muted-foreground">
            The reply stopped loading.
          </span>
        </p>
      ) : (
        <p
          role="alert"
          className="flex items-start gap-2 text-sm text-destructive"
        >
          <AlertCircleIcon className="mt-0.5 size-3.5 shrink-0" />
          <span className="min-w-0 whitespace-pre-wrap break-words">
            {error}
          </span>
        </p>
      )}
      {onRetry && <RetryAction onRetry={onRetry} />}
    </div>
  )
}
