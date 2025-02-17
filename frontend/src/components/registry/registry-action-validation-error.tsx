import { AlertCircle, XCircle } from "lucide-react"

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { ScrollArea } from "@/components/ui/scroll-area"

interface ErrorDisplayProps {
  title?: string
  error: Error | string | Record<string, string[]> | null
  variant?: "default" | "destructive"
  className?: string
}

export function ErrorDisplay({
  title = "Error",
  error,
  variant = "destructive",
  className,
}: ErrorDisplayProps) {
  if (!error) return null

  const getErrorMessage = () => {
    if (error instanceof Error) {
      return error.message
    }
    if (typeof error === "string") {
      return error
    }
    if (typeof error === "object") {
      return (
        <ScrollArea className="h-full max-h-[120px]">
          {Object.entries(error).map(([field, messages]) => (
            <div key={field} className="mb-2">
              <span className="font-medium">{field}:</span>
              <ul className="list-disc pl-4">
                {messages.map((message, index) => (
                  <li key={index}>{message}</li>
                ))}
              </ul>
            </div>
          ))}
        </ScrollArea>
      )
    }
    return "An unknown error occurred"
  }

  const Icon = variant === "destructive" ? XCircle : AlertCircle

  return (
    <Alert variant={variant} className={className}>
      <Icon className="size-4" />
      <AlertTitle>{title}</AlertTitle>
      <AlertDescription>{getErrorMessage()}</AlertDescription>
    </Alert>
  )
}
