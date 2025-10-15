import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar"
import type { User } from "@/lib/auth"
import { cn } from "@/lib/utils"

interface UserAvatarProps extends React.HTMLAttributes<HTMLElement> {
  src?: string
  alt?: string
  user?: User | null
  fallbackClassName?: string
}
export default function UserAvatar({
  src,
  alt,
  user,
  className,
  fallbackClassName,
}: UserAvatarProps) {
  const initials = user?.firstName
    ? `${user.firstName[0]}`.toUpperCase()
    : user?.email[0].toUpperCase()
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
