"use client"

import {
  createContext,
  type PropsWithChildren,
  useContext,
  useMemo,
  useState,
} from "react"
import { createPortal } from "react-dom"

type CaseViewHeaderSlotContextValue = {
  slot: HTMLElement | null
  setSlot: (element: HTMLElement | null) => void
}

const CaseViewHeaderSlotContext = createContext<CaseViewHeaderSlotContextValue>(
  { slot: null, setSlot: () => {} }
)

/**
 * Connects the case view menu, which the cases page owns, to a spot in the
 * page's top header, which a layout renders.
 */
export function CaseViewHeaderSlotProvider({ children }: PropsWithChildren) {
  const [slot, setSlot] = useState<HTMLElement | null>(null)
  const value = useMemo(() => ({ slot, setSlot }), [slot])
  return (
    <CaseViewHeaderSlotContext.Provider value={value}>
      {children}
    </CaseViewHeaderSlotContext.Provider>
  )
}

/** Marks where in the top header the case view menu appears. */
export function CaseViewHeaderSlot() {
  const { setSlot } = useContext(CaseViewHeaderSlotContext)
  // `contents` makes the portalled children direct flex items of the header.
  return <div ref={setSlot} className="contents" />
}

/** Renders its children into the header slot, once the slot is mounted. */
export function CaseViewHeaderPortal({ children }: PropsWithChildren) {
  const { slot } = useContext(CaseViewHeaderSlotContext)
  return slot ? createPortal(children, slot) : null
}
