"use client"

import { ArrowRight, RefreshCcwIcon } from "lucide-react"
import Link from "next/link"
import type { ModelErrorDetail, ModelStatusRead } from "@/client"
import { Button } from "@/components/ui/button"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import { useModelStatus } from "@/hooks/use-model-status"
import type { ModelInfo } from "@/lib/chat"
import {
  AGENT_SETTINGS_HREF,
  describeModelError,
  modelErrorFacts,
  providerDisplayName,
  sentence,
} from "@/lib/model-errors"
import { cn } from "@/lib/utils"

/** The dot's look and one-line label for a check result. */
type DotTone = "checking" | "ready" | "unverified" | "warning" | "problem"

const DOT_CLASS: Record<DotTone, string> = {
  checking: "bg-muted-foreground/40 animate-pulse",
  ready: "bg-emerald-500",
  unverified: "bg-muted-foreground/60",
  warning: "bg-amber-500",
  problem: "bg-destructive",
}

/** Problems that pass on their own rather than needing a settings change. */
const TRANSIENT_CODES: ReadonlySet<ModelErrorDetail["code"]> = new Set([
  "rate_limited",
  "provider_unreachable",
])

function toneFor(
  status: ModelStatusRead | undefined,
  isChecking: boolean,
  failed: boolean
): DotTone {
  if (isChecking) return "checking"
  if (failed || !status) return "unverified"
  if (status.state === "ready") return "ready"
  if (status.state === "unverified") return "unverified"
  if (status.problem && TRANSIENT_CODES.has(status.problem.code)) {
    return "warning"
  }
  return "problem"
}

function headline(
  tone: DotTone,
  status: ModelStatusRead | undefined,
  modelInfo: ModelInfo,
  failed: boolean
): string {
  // A failed check says nothing about the result cached before it.
  if (failed) return "Couldn't check the model's status."
  const provider = providerDisplayName(modelInfo.provider)
  switch (tone) {
    case "checking":
      return `Checking ${provider}…`
    case "ready":
      return sentence(
        `${provider} accepted the credentials for ${modelInfo.name}.`
      )
    case "unverified":
      if (!status) return "Couldn't check the model's status."
      return sentence(
        `${provider} credentials are set. Connectivity isn't checked for this provider.`
      )
    default:
      return status?.problem
        ? describeModelError(status.problem).title
        : "The model has a problem."
  }
}

/**
 * A status dot for the chat's selected model. Click it for what's wrong and
 * where to fix it, or to check again.
 */
export function ModelStatusDot({
  workspaceId,
  modelInfo,
}: {
  workspaceId: string
  modelInfo: ModelInfo
}) {
  const { status, isChecking, error, recheck } = useModelStatus(
    workspaceId,
    modelInfo
  )
  const tone = toneFor(status, isChecking, Boolean(error))
  const label = headline(tone, status, modelInfo, Boolean(error))
  const problem = error ? null : (status?.problem ?? null)
  const copy = problem ? describeModelError(problem) : null

  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          aria-label={`Model status: ${label}`}
          title={label}
          className="inline-flex size-4 shrink-0 items-center justify-center rounded-full"
          data-tone={tone}
        >
          <span className={cn("size-2 rounded-full", DOT_CLASS[tone])} />
        </button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-80 space-y-2 p-3 text-xs">
        <p className="font-medium text-foreground">{label}</p>
        {copy && <p className="text-muted-foreground">{copy.action}</p>}
        {problem && (
          <p className="font-mono text-[11px] text-muted-foreground">
            {modelErrorFacts(problem).join(" · ")}
          </p>
        )}
        <div className="flex items-center justify-between gap-2 pt-1">
          {/* A transient problem needs no settings change, so no link. */}
          {problem && !copy?.settingsHref ? (
            <span />
          ) : (
            <Link
              href={copy?.settingsHref ?? AGENT_SETTINGS_HREF}
              className="inline-flex items-center gap-0.5 font-medium text-foreground underline-offset-4 hover:underline"
            >
              Agent settings
              <ArrowRight className="size-3" />
            </Link>
          )}
          <Button
            size="sm"
            variant="ghost"
            className="h-7 gap-1 px-2 text-xs"
            disabled={isChecking}
            onClick={recheck}
          >
            <RefreshCcwIcon className="size-3" />
            Check again
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  )
}
