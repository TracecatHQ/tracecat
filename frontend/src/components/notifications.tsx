import { ExclamationTriangleIcon } from "@radix-ui/react-icons"
import { cva, type VariantProps } from "class-variance-authority"
import { AlertCircleIcon, CheckCheckIcon } from "lucide-react"
import React, { type ComponentPropsWithoutRef } from "react"

import { cn } from "@/lib/utils"

export type AlertLevel = "error" | "info" | "warning" | "success"
interface AlertNotificationProps
  extends ComponentPropsWithoutRef<typeof Notification> {
  level?: AlertLevel
  message: React.ReactNode
  reset?: () => void
}

function getIcon(level: AlertLevel) {
  switch (level) {
    case "error":
      return ExclamationTriangleIcon
    case "info":
      return AlertCircleIcon
    case "warning":
      return ExclamationTriangleIcon
    case "success":
      return CheckCheckIcon
  }
}

export function AlertNotification({
  level = "info",
  message,
  reset,
  className,
}: AlertNotificationProps) {
  const Icon = getIcon(level)
  return (
    <Notification
      variant={level}
      onClick={reset}
      className={cn(
        "m-2 border-2 font-medium hover:cursor-pointer",
        className,
        !reset && "hover:cursor-default"
      )}
    >
      <div className="flex items-center justify-start space-x-4">
        <Icon className="size-4" />
        <NotificationDescription>{message}</NotificationDescription>
      </div>
    </Notification>
  )
}

const notificationVariants = cva(
  "relative w-full rounded-lg border px-4 py-3 text-sm [&>svg+div]:translate-y-[-3px] [&>svg]:absolute [&>svg]:left-4 [&>svg]:top-4 [&>svg]:text-foreground [&>svg~*]:pl-7",
  {
    variants: {
      variant: {
        success:
          "border-green-500/50 bg-green-500/15 text-green-700 dark:border-green-500/50 dark:bg-green-950/40 dark:text-green-300 [&>svg]:text-green-600 dark:[&>svg]:text-green-400",
        info: "border-cyan-500/50 bg-cyan-500/15 text-cyan-700 dark:border-cyan-500/50 dark:bg-cyan-950/40 dark:text-cyan-300 [&>svg]:text-cyan-600 dark:[&>svg]:text-cyan-400",
        warning:
          "border-yellow-500/50 bg-yellow-500/10 text-yellow-700 dark:border-yellow-500/50 dark:bg-yellow-950/40 dark:text-yellow-300 [&>svg]:text-yellow-600 dark:[&>svg]:text-yellow-400",
        error:
          "border-destructive/50 bg-destructive/10 text-destructive dark:border-red-900/60 dark:bg-red-950/40 dark:text-red-300 [&>svg]:text-destructive dark:[&>svg]:text-red-400",
      },
    },
    defaultVariants: {
      variant: "info",
    },
  }
)

const Notification = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement> &
    VariantProps<typeof notificationVariants>
>(({ className, variant, ...props }, ref) => (
  <div
    ref={ref}
    role="alert"
    className={cn(notificationVariants({ variant }), className)}
    {...props}
  />
))
Notification.displayName = "Notification"

const NotificationTitle = React.forwardRef<
  HTMLParagraphElement,
  React.HTMLAttributes<HTMLHeadingElement>
>(({ className, ...props }, ref) => (
  <h5
    ref={ref}
    className={cn("mb-1 font-medium leading-none tracking-tight", className)}
    {...props}
  />
))
NotificationTitle.displayName = "NotificationTitle"

const NotificationDescription = React.forwardRef<
  HTMLParagraphElement,
  React.HTMLAttributes<HTMLParagraphElement>
>(({ className, ...props }, ref) => (
  <div
    ref={ref}
    className={cn("text-sm [&_p]:leading-relaxed", className)}
    {...props}
  />
))
NotificationDescription.displayName = "NotificationDescription"

export { Notification, NotificationTitle, NotificationDescription }
