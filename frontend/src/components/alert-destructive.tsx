import { ExclamationTriangleIcon } from "@radix-ui/react-icons"

import { cn } from "@/lib/utils"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"

interface AlertDestructiveProps extends React.HTMLAttributes<HTMLDivElement> {
  message: string
  reset: () => void
}

export function AlertDestructive({
  message,
  reset,
  className,
}: AlertDestructiveProps) {
  return (
    <Alert
      variant="destructive"
      onClick={reset}
      className={cn("border-2 font-medium hover:cursor-pointer", className)}
    >
      <ExclamationTriangleIcon className="h-4 w-4" />
      <AlertTitle>Error</AlertTitle>
      <AlertDescription>{message}</AlertDescription>
    </Alert>
  )
}
