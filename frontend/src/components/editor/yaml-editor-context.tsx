"use client"

import React, { createContext, useCallback, useContext, useRef } from "react"

export interface YamlEditorCommitFunction {
  (): void
}

interface YamlEditorContextType {
  registerEditor: (id: string, commitFn: YamlEditorCommitFunction) => void
  unregisterEditor: (id: string) => void
  commitAllEditors: () => void
}

const YamlEditorContext = createContext<YamlEditorContextType | null>(null)

export function useYamlEditorContext() {
  const context = useContext(YamlEditorContext)
  if (!context) {
    throw new Error(
      "useYamlEditorContext must be used within a YamlEditorProvider"
    )
  }
  return context
}

export function YamlEditorProvider({
  children,
}: {
  children: React.ReactNode
}) {
  const editorsRef = useRef<Map<string, YamlEditorCommitFunction>>(new Map())

  const registerEditor = useCallback(
    (id: string, commitFn: YamlEditorCommitFunction) => {
      editorsRef.current.set(id, commitFn)
    },
    []
  )

  const unregisterEditor = useCallback((id: string) => {
    editorsRef.current.delete(id)
  }, [])

  const commitAllEditors = useCallback(() => {
    editorsRef.current.forEach((commitFn) => {
      try {
        commitFn()
      } catch (error) {
        console.warn("Failed to commit YAML editor:", error)
      }
    })
  }, [])

  const value = {
    registerEditor,
    unregisterEditor,
    commitAllEditors,
  }

  return (
    <YamlEditorContext.Provider value={value}>
      {children}
    </YamlEditorContext.Provider>
  )
}
