import type { UIMessage } from "ai"
import { cva, type VariantProps } from "class-variance-authority"
import type { ComponentProps, HTMLAttributes, ReactNode } from "react"
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar"
import { cn } from "@/lib/utils"

export type MessageProps = HTMLAttributes<HTMLDivElement> & {
  from: UIMessage["role"]
}

export const Message = ({ className, from, ...props }: MessageProps) => (
  <div
    className={cn(
      "group flex w-full items-end justify-end gap-2 py-2",
      from === "user" ? "is-user" : "is-assistant flex-row-reverse justify-end",
      className
    )}
    {...props}
  />
)

const messageContentVariants = cva(
  "is-user:dark flex flex-col gap-2 overflow-hidden rounded-lg text-sm",
  {
    variants: {
      variant: {
        contained: [
          "max-w-[80%] px-4 py-3",
          "group-[.is-user]:bg-primary group-[.is-user]:text-primary-foreground",
          "group-[.is-assistant]:bg-secondary group-[.is-assistant]:text-foreground",
        ],
        flat: [
          "group-[.is-user]:max-w-[80%] group-[.is-user]:bg-secondary group-[.is-user]:px-3 group-[.is-user]:py-2  group-[.is-user]:text-foreground group-[.is-user]:rounded-lg",
          "group-[.is-assistant]:text-foreground",
        ],
      },
    },
    defaultVariants: {
      variant: "contained",
    },
  }
)

export type MessageContentProps = HTMLAttributes<HTMLDivElement> &
  VariantProps<typeof messageContentVariants>

export const MessageContent = ({
  children,
  className,
  variant,
  ...props
}: MessageContentProps) => (
  <div
    className={cn(messageContentVariants({ variant, className }))}
    {...props}
  >
    {children}
  </div>
)

export type MessageAvatarProps = ComponentProps<typeof Avatar> & {
  src?: string
  name?: string
  /** Drawn instead of the initials, such as an agent's icon. */
  icon?: ReactNode
  /** A small mark on the avatar's corner, such as where the message came from. */
  badge?: ReactNode
}

/** Up to two initials of a display name. */
function initials(name: string | undefined): string {
  const words = (name ?? "").trim().split(/\s+/).filter(Boolean)
  if (words.length === 0) return "?"
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase()
  return `${words[0][0]}${words[1][0]}`.toUpperCase()
}

export const MessageAvatar = ({
  src,
  name,
  icon,
  badge,
  className,
  ...props
}: MessageAvatarProps) => (
  <div className="relative shrink-0">
    <Avatar
      role="img"
      className={cn("size-8 ring-1 ring-border", className)}
      {...props}
    >
      {src ? <AvatarImage alt="" className="mt-0 mb-0" src={src} /> : null}
      <AvatarFallback className="text-xs font-medium">
        {icon ?? initials(name)}
      </AvatarFallback>
    </Avatar>
    {badge ? (
      <span className="absolute -right-1 -bottom-1 flex size-4 items-center justify-center rounded-[5px] bg-background">
        {badge}
      </span>
    ) : null}
  </div>
)
