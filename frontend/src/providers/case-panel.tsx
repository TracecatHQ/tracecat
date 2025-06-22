"use client"

import { useRouter, useSearchParams } from "next/navigation"
import type React from "react"
import {
  createContext,
  type Dispatch,
  type PropsWithChildren,
  type SetStateAction,
  useContext,
  useEffect,
  useState,
} from "react"
import { CasePanelView } from "@/components/cases/case-panel-view"
import { SlidingPanel } from "@/components/sliding-panel"
import { cn } from "@/lib/utils"

interface CasePanelContextType {
  caseId?: string
  setCaseId: (caseId?: string) => void
  isOpen: boolean
  setIsOpen: Dispatch<SetStateAction<boolean>>
}

const CasePanelContext = createContext<CasePanelContextType | undefined>(
  undefined
)

export default function CasePanelProvider({
  children,
  className,
}: PropsWithChildren<React.HTMLAttributes<HTMLDivElement>>) {
  const searchParams = useSearchParams()
  const router = useRouter()
  const caseId = searchParams.get("caseId") || undefined
  const [isOpen, setIsOpen] = useState(false)

  useEffect(() => {
    setIsOpen(!!caseId)
  }, [caseId])

  const setCaseId = (caseId?: string) => {
    const searchParams = new URLSearchParams(window.location.search)
    if (caseId) {
      searchParams.set("caseId", caseId)
    } else {
      searchParams.delete("caseId")
    }
    router.push(`?${searchParams.toString()}`)
  }

  return (
    <CasePanelContext.Provider
      value={{
        caseId,
        setCaseId,
        isOpen,
        setIsOpen,
      }}
    >
      {children}
      <SlidingPanel
        className={cn("py-0 sm:w-4/5 md:w-4/5 lg:w-4/5", className)}
        isOpen={!!caseId}
        setIsOpen={(isOpen) => {
          if (!isOpen) {
            const searchParams = new URLSearchParams(window.location.search)
            searchParams.delete("caseId")
            router.push(`?${searchParams.toString()}`)
          }
          setIsOpen(isOpen)
        }}
      >
        {caseId && <CasePanelView caseId={caseId} />}
      </SlidingPanel>
    </CasePanelContext.Provider>
  )
}

export const useCasePanelContext = (): CasePanelContextType => {
  const context = useContext(CasePanelContext)
  if (context === undefined) {
    throw new Error(
      "useCasePanelContext must be used within a CasePanelProvider"
    )
  }
  return context
}
