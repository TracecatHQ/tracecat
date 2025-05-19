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
  default: ({ detail }) => (
    <div className="flex items-center">
      <CornerDownRightIcon className="mr-2 size-3" />
      <div className="flex flex-col">
        <div className="flex items-center">
          <span>At field:</span>
          <strong className="ml-2 text-xs">{detail.loc?.join(".")}</strong>
        </div>
        <span>
          {detail.msg} ({detail.type})
        </span>
      </div>
    </div>
  ),
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

  console.log("ValidationErrorMessage", {
    error,
    formattedMessage,
  })

  return (
    <pre
      className={cn("overflow-auto whitespace-pre-wrap text-wrap", className)}
    >
      {formattedMessage}
      {error.type === "secret" && (
        <span>
          Please go to Workspace &gt; Credentials and add the secret &quot;
          {error.detail?.secret_name}&quot; under the &quot;
          {error.detail?.environment}&quot; environment.
        </span>
      )}
      {error.type === "expression" && (
        <React.Fragment>
          <br />
          <span>Expression Type: {error.expression_type}</span>
          <br />
          <span>Expression: {error.msg}</span>
        </React.Fragment>
      )}
      {["generic", "registry", "action_template"].includes(error.type ?? "") &&
        Array.isArray(error.detail) && (
          <React.Fragment>
            <br />
            {error.detail?.map((d, index) => {
              const type = d.type as ValidationErrorType
              const MessageComponent =
                ERROR_TYPE_TO_MESSAGE[type] || ERROR_TYPE_TO_MESSAGE.default
              return (
                <React.Fragment key={index}>
                  <MessageComponent detail={d} />
                  <br />
                </React.Fragment>
              )
            })}
          </React.Fragment>
        )}
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
  ...props
}: ValidationErrorViewProps) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>{children}</TooltipTrigger>
      <TooltipContent
        side={side}
        className="w-fit p-0 text-xs shadow-lg"
        {...props}
      >
        {validationErrors && validationErrors.length > 0 ? (
          <div className="space-y-2 rounded-md border border-rose-300 bg-rose-100 p-2 font-mono tracking-tighter">
            <span className="text-xs font-bold text-rose-500">
              Validation Errors
            </span>
            <div className="mt-1 space-y-1">
              {validationErrors.map((error, index) => (
                <div className="space-y-2" key={index}>
                  <Separator className="bg-rose-400" />
                  <ValidationErrorMessage
                    key={index}
                    error={error}
                    className="text-rose-500"
                  />
                </div>
              ))}
            </div>
          </div>
        ) : (
          <div className="p-2">{noErrorTooltip}</div>
        )}
      </TooltipContent>
    </Tooltip>
  )
}
