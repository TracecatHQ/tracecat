import Link from "next/link"

import { siteConfig } from "@/config/site"
import { Icons } from "@/components/icons"

export function CommunityNav() {
  return (
    <div className="flex items-center justify-center space-x-4">
      <Link href={siteConfig.links.discord} target="_blank">
        <Icons.discord className="size-5" />
      </Link>
      <Link href={siteConfig.links.github} target="_blank">
        <Icons.gitHub className="size-5" />
      </Link>
    </div>
  )
}
