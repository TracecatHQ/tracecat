"use client"

import { useParams } from "next/navigation"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { CenteredSpinner } from "@/components/loading/spinner"
import { SkillLibraryProviderView } from "@/components/skills/skill-library-provider-view"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"

export default function SkillLibraryProviderPage() {
  const params = useParams<{ provider: string }>()
  const canRead = useScopeCheck("agent:read")

  if (canRead === undefined) return <CenteredSpinner />

  if (canRead === false) {
    return (
      <div className="flex h-full items-center justify-center p-6">
        <Alert className="max-w-md">
          <AlertTitle>Access denied</AlertTitle>
          <AlertDescription>
            You do not have permission to view the skill library.
          </AlertDescription>
        </Alert>
      </div>
    )
  }

  return <SkillLibraryProviderView providerSlug={params.provider} />
}
