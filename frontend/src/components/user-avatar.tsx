import { UserRead } from "@/client"
import { UserIcon } from "lucide-react"

import { cn } from "@/lib/utils"
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar"

interface UserAvatarProps extends React.HTMLAttributes<HTMLElement> {
  src?: string
  alt?: string
  user?: UserRead | null
}
export default function UserAvatar({
  src,
  alt,
  user,
  className,
}: UserAvatarProps) {
  const initials = user?.first_name
    ? `${user.first_name[0]}`.toUpperCase()
    : user?.email[0].toUpperCase()
  return (
    <Avatar className={cn("size-8", className)}>
      <AvatarImage src={src} alt={alt} />
      <AvatarFallback>{initials}</AvatarFallback>
    </Avatar>
  )
}
