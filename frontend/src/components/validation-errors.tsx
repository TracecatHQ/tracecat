import React, { PropsWithChildren } from "react"
import { ValidationDetail, ValidationResult } from "@/client"
import { CornerDownRightIcon } from "lucide-react"

import { cn } from "@/lib/utils"
import { Separator } from "@/components/ui/separator"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"

export const VALIDATION_ERROR_TYPES = [
  "pydantic.missing",
  "pydantic.invalid_type",
  "pydantic.extra_forbidden",
] as const
export type ValidationErrorType = (typeof VALIDATION_ERROR_TYPES)[number]

export const ERROR_TYPE_TO_MESSAGE: Record<
  ValidationErrorType | "default",
  React.ComponentType<{ detail: ValidationDetail }>
> = {
  "pydantic.missing": ({ detail }) => (
    <div className="flex items-center">
      <CornerDownRightIcon className="mr-2 size-3" />
      <span>Missing required field:</span>
      <strong className="ml-2 text-xs">{detail.loc?.join(".")}</strong>
    </div>
  ),
  "pydantic.invalid_type": ({ detail }) => (
    <div>
      Invalid type: {detail.msg} ({detail.loc})
    </div>
  ),
  "pydantic.extra_forbidden": ({ detail }) => (
    <div className="flex items-center">
      <CornerDownRightIcon className="mr-2 size-3" />
      <span>Unrecognized field:</span>
      <strong className="ml-2 text-xs">{detail.loc?.join(".")}</strong>
    </div>
  ),
  default: ({ detail }) => (
    <div className="flex items-start">
      <div className="flex flex-col items-start justify-start">
        <CornerDownRightIcon className="mr-2 mt-px size-3" />
      </div>
      <div className="flex flex-col">
        {detail.loc && (
          <div className="flex items-center">
            <span>In {detail.loc?.join(" → ")}</span>
          </div>
        )}
        <span>
          {detail.msg} ({detail.type})
        </span>
      </div>
    </div>
  ),
}

function ValidationDetails({ error }: { error: ValidationResult }) {
  if (Array.isArray(error.detail)) {
    return error.detail?.map((d, index) => {
      const type = d.type as ValidationErrorType
      const MessageComponent =
        ERROR_TYPE_TO_MESSAGE[type] || ERROR_TYPE_TO_MESSAGE["default"]
      return <MessageComponent key={index} detail={d} />
    })
  }
  return null
}

export function ValidationErrorMessage({
  error,
  className,
}: {
  error: ValidationResult
  className?: string
}) {
  // Replace newline characters with <br /> tags
  const formattedMessage = error.msg?.split("\n").map((line, index) => (
    <React.Fragment key={index}>
      {line}
      <br />
    </React.Fragment>
  ))

  return (
    <pre
      className={cn("overflow-auto whitespace-pre-wrap text-wrap", className)}
    >
      <div className="flex flex-col space-y-2">
        {error.type === "secret" && (
          <React.Fragment>
            <span>{formattedMessage}</span>
          </React.Fragment>
        )}
        {error.type === "expression" && (
          <React.Fragment>
            {error.ref && (
              <span>
                In action → <strong>{error.ref}</strong>
              </span>
            )}
            {error.expression && <span>Expression → {error.expression}</span>}
            <span>{formattedMessage}</span>
            <ValidationDetails error={error} />
          </React.Fragment>
        )}
        {error.type === "action" && (
          <React.Fragment>
            {error.ref && (
              <span>
                In action → <strong>{error.ref}</strong>
              </span>
            )}
            <span>{formattedMessage}</span>
            <ValidationDetails error={error} />
          </React.Fragment>
        )}

        {error.type === "dsl" && (
          <React.Fragment>
            {error.ref ? (
              <span>
                In action → <strong>{error.ref}</strong>
              </span>
            ) : (
              <span>In the workflow definition</span>
            )}
            <span>{formattedMessage}</span>
            <ValidationDetails error={error} />
          </React.Fragment>
        )}
        {error.type === "action_template" && (
          <React.Fragment>
            {error.ref && (
              <span>
                In action → <strong>{error.ref}</strong>
              </span>
            )}
            <span>{formattedMessage}</span>
            <ValidationDetails error={error} />
          </React.Fragment>
        )}
      </div>
    </pre>
  )
}

interface ValidationErrorViewProps
  extends PropsWithChildren<React.HTMLAttributes<HTMLDivElement>> {
  validationErrors: ValidationResult[]
  noErrorTooltip?: React.ReactNode
  side?: "top" | "bottom" | "left" | "right"
}

export function ValidationErrorView({
  validationErrors,
  noErrorTooltip,
  children,
  side = "bottom",
  className,
  ...props
}: ValidationErrorViewProps) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>{children}</TooltipTrigger>
      <TooltipContent
        side={side}
        className={cn("w-fit p-0 text-xs shadow-lg", className)}
        {...props}
      >
        {validationErrors && validationErrors.length > 0 ? (
          <div className="rounded-md border border-rose-300 bg-rose-100 font-mono tracking-tighter text-rose-500">
            <div className="m-2">
              <span className="text-xs font-bold">
                Found {validationErrors.length} validation errors
              </span>
            </div>
            {validationErrors
              .sort((a, b) => {
                if (a.ref === b.ref) return 0
                if (!a.ref) return 1
                if (!b.ref) return -1
                return a.ref.localeCompare(b.ref)
              })
              .map((error, index) => (
                <div className="space-y-2" key={index}>
                  <Separator className="bg-rose-300" />
                  <div className="m-2 pb-2">
                    <ValidationErrorMessage key={index} error={error} />
                  </div>
                </div>
              ))}
          </div>
        ) : (
          <div className="p-2">{noErrorTooltip}</div>
        )}
      </TooltipContent>
    </Tooltip>
  )
}
