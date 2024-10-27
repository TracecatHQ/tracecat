"use client"

import {
  BackToWorkspaceNavButton,
  OrganizationNavButton,
  RegistryNavButton,
} from "@/components/nav/nav-buttons"

export function OrganizationNav() {
  return (
    <nav className="flex space-x-4 lg:space-x-6">
      <BackToWorkspaceNavButton />
      <RegistryNavButton />
      <OrganizationNavButton />
    </nav>
  )
}
