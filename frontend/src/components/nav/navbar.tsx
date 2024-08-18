"use client"

import React from "react"
import Link from "next/link"

import { TooltipProvider } from "@/components/ui/tooltip"
import { Icons } from "@/components/icons"
import UserNav from "@/components/nav/user-nav"

interface NavbarProps extends React.HTMLAttributes<HTMLDivElement> {
  children: React.ReactNode
}
export function Navbar({ children, ...props }: NavbarProps) {
  return (
    <TooltipProvider delayDuration={300}>
      <div className="w-full space-x-8 border-b" {...props}>
        <div className="flex h-12 w-full items-center space-x-5 px-5">
          <Link href="/workspaces">
            <Icons.logo className="size-5" />
          </Link>
          {children}
          <div className="flex flex-1 items-center justify-end space-x-6">
            <UserNav />
          </div>
        </div>
      </div>
    </TooltipProvider>
  )
}
