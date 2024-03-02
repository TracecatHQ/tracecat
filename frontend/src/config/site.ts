import { SiteConfig } from "@/types"

import { env } from "@/env.mjs"

export const siteConfig: SiteConfig = {
  name: "Next Entree",
  author: "Tracecat",
  description:
    "The AI-native Tines alternative.",
  keywords: ["Next.js", "React", "Tailwind CSS", "Radix UI", "shadcn/ui"],
  url: {
    base: env.NEXT_PUBLIC_APP_URL,
    author: "Tracecat",
  },
  links: {
    github: "https://github.com/TracecatHQ/tracecat",
  },
  ogImage: `${env.NEXT_PUBLIC_APP_URL}/og.jpg`,
}
