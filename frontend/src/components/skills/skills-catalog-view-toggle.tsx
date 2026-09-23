"use client"

import { Pyramid, TagIcon } from "lucide-react"
import Link from "next/link"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"

export enum SkillsCatalogViewMode {
  Skills = "skills",
  Tags = "tags",
}

interface SkillsCatalogViewToggleProps {
  view: SkillsCatalogViewMode
  skillsHref: string
  tagsHref: string
  className?: string
}

/**
 * Toggle between the skills catalog and skill tags.
 *
 * @param props Toggle properties.
 * @returns Catalog navigation controls.
 */
export function SkillsCatalogViewToggle({
  view,
  skillsHref,
  tagsHref,
  className,
}: SkillsCatalogViewToggleProps) {
  const toggleItems = [
    {
      mode: SkillsCatalogViewMode.Skills,
      icon: Pyramid,
      tooltip: "Skills",
      href: skillsHref,
      ariaLabel: "Skills view",
    },
    {
      mode: SkillsCatalogViewMode.Tags,
      icon: TagIcon,
      tooltip: "Skill tags",
      href: tagsHref,
      ariaLabel: "Skill tags view",
    },
  ] as const

  return (
    <div
      className={cn(
        "inline-flex items-center rounded-md border bg-transparent",
        className
      )}
    >
      <TooltipProvider>
        {toggleItems.map((item, index) => {
          const Icon = item.icon
          const isActive = view === item.mode
          let roundedClass = "rounded-none"
          if (index === 0) {
            roundedClass = "rounded-l-sm"
          } else if (index === toggleItems.length - 1) {
            roundedClass = "rounded-r-sm"
          }
          return (
            <Tooltip key={item.mode}>
              <TooltipTrigger asChild>
                <Link
                  href={item.href}
                  className={cn(
                    "flex size-7 items-center justify-center transition-colors",
                    roundedClass,
                    isActive
                      ? "bg-background text-accent-foreground"
                      : "bg-accent text-muted-foreground hover:bg-muted/50"
                  )}
                  aria-current={isActive ? "page" : undefined}
                  aria-label={item.ariaLabel}
                >
                  <Icon className="size-3.5" />
                </Link>
              </TooltipTrigger>
              <TooltipContent>
                <p>{item.tooltip}</p>
              </TooltipContent>
            </Tooltip>
          )
        })}
      </TooltipProvider>
    </div>
  )
}
