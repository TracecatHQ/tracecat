import { cn } from "@/lib/utils"
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar"

interface UserAvatarProps extends React.HTMLAttributes<HTMLElement> {
  src?: string
  alt?: string
}
export default function UserAvatar({ src, alt, className }: UserAvatarProps) {
  return (
    <Avatar className={cn("size-8", className)}>
      <AvatarImage src={src} alt={alt} />
      <AvatarFallback>TC</AvatarFallback>
    </Avatar>
  )
}
