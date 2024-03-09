import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar"

export default function UserAvatar() {
  return (
    <Avatar>
      <AvatarImage
        src="https://media.licdn.com/dms/image/C5103AQEXlYZeTKuwyQ/profile-displayphoto-shrink_200_200/0/1582770649112?e=1715212800&v=beta&t=wqVZfVV4YwedybQFzKazeWmlQslMQ11t_NGMCqwpN-k"
        alt="@daryllimyt"
      />
      <AvatarFallback>CN</AvatarFallback>
    </Avatar>
  )
}
