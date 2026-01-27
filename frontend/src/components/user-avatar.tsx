import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar"
import { cn } from "@/lib/utils"

export interface UserAvatarProps extends React.HTMLAttributes<HTMLElement> {
  src?: string
  alt?: string
  email: string
  firstName?: string | null
  fallbackClassName?: string
}

export default function UserAvatar({
  src,
  alt,
  email,
  firstName,
  className,
  fallbackClassName,
}: UserAvatarProps) {
  const initials = firstName
    ? `${firstName[0]}`.toUpperCase()
    : email[0].toUpperCase()
  return (
    <Avatar className={cn("size-8", className)}>
      <AvatarImage src={src} alt={alt} />
      <AvatarFallback
        className={cn("text-sm font-medium uppercase", fallbackClassName)}
      >
        {initials}
      </AvatarFallback>
    </Avatar>
  )
}
