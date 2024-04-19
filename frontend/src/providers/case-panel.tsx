"use client"

import React, {
  createContext,
  PropsWithChildren,
  useContext,
  useEffect,
  useState,
} from "react"

import { type Case } from "@/types/schemas"
import { cn } from "@/lib/utils"
import { CasePanelContent } from "@/components/cases/panel-content"
import { SlidingPanel } from "@/components/sliding-panel"

export const useCasePanelContext = () => useContext(CasePanelContext)

const CasePanelContext = createContext({
  panelCase: null as Case | null,
  setPanelCase: (rule: Case | null) => {},
  isOpen: false,
  setIsOpen: (isOpen: boolean) => {},
})

export default function CasePanelProvider({
  children,
  className,
}: PropsWithChildren<React.HTMLAttributes<HTMLDivElement>>) {
  const [selectedCase, setSelectedCase] = useState<Case | null>(null)
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
        className={cn("sm:w-4/5 md:w-4/5 lg:w-4/5", className)}
        isOpen={isOpen}
        setIsOpen={setIsOpen}
      >
        {selectedCase && isOpen && (
          <CasePanelContent currentCase={selectedCase} />
        )}
      </SlidingPanel>
    </CasePanelContext.Provider>
  )
}
