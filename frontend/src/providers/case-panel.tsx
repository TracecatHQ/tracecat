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

import { type Case } from "@/types/schemas"
import { cn } from "@/lib/utils"
import { CasePanelContent } from "@/components/cases/panel-content"
import { SlidingPanel } from "@/components/sliding-panel"

interface CasePanelContextProps {
  panelCase: Case | null
  setPanelCase: Dispatch<SetStateAction<Case | null>>
  isOpen: boolean
  setIsOpen: Dispatch<SetStateAction<boolean>>
}

export const useCasePanelContext = () =>
  useContext<CasePanelContextProps>(CasePanelContext)
const CasePanelContext = createContext<CasePanelContextProps>({
  panelCase: null as Case | null,
  setPanelCase: () => {},
  isOpen: false,
  setIsOpen: () => {},
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
        className={cn("py-0 sm:w-4/5 md:w-4/5 lg:w-4/5", className)}
        isOpen={isOpen}
        setIsOpen={setIsOpen}
      >
        {selectedCase && isOpen && (
          <CasePanelContent caseId={selectedCase.id} />
        )}
      </SlidingPanel>
    </CasePanelContext.Provider>
  )
}
