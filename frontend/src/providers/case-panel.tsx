"use client"

import React, {
  createContext,
  Dispatch,
  PropsWithChildren,
  SetStateAction,
  useContext,
  useEffect,
  useState,
} from "react"
import { CaseReadMinimal } from "@/client"

import { cn } from "@/lib/utils"
import { CasePanelView } from "@/components/cases/case-panel-view"
import { SlidingPanel } from "@/components/sliding-panel"

interface CasePanelContextType {
  panelCase: CaseReadMinimal | null
  setPanelCase: Dispatch<SetStateAction<CaseReadMinimal | null>>
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
  const [selectedCase, setSelectedCase] = useState<CaseReadMinimal | null>(null)
  const [isOpen, setIsOpen] = useState(false)

  useEffect(() => {
    setIsOpen(selectedCase !== null)
  }, [selectedCase])

  return (
    <CasePanelContext.Provider
      value={{
        panelCase: selectedCase,
        setPanelCase: setSelectedCase,
        isOpen,
        setIsOpen,
      }}
    >
      {children}
      <SlidingPanel
        className={cn("py-0 sm:w-4/5 md:w-4/5 lg:w-4/5", className)}
        isOpen={isOpen}
        setIsOpen={setIsOpen}
      >
        {selectedCase && isOpen && <CasePanelView caseId={selectedCase.id} />}
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
