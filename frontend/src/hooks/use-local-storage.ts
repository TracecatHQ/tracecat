import { useEffect, useRef, useState } from "react"

type LocalStorageUpdater<T> = T | ((value: T) => T)

export function useLocalStorage<T>(
  key: string,
  initialValue: T,
  prefix = ""
): [T, (value: LocalStorageUpdater<T>) => void] {
  const prefixedKey = prefix ? `${prefix}_${key}` : key

  const initialValueRef = useRef(initialValue)

  const [storedValue, setStoredValue] = useState<T>(() => {
    if (typeof window === "undefined") {
      return initialValue
    }
    try {
      const item = window.localStorage.getItem(prefixedKey)
      return item ? JSON.parse(item) : initialValue
    } catch (error) {
      console.error(error)
      return initialValue
    }
  })

  const setValue = (value: LocalStorageUpdater<T>) => {
    try {
      setStoredValue((current) => {
        const valueToStore =
          typeof value === "function"
            ? (value as (current: T) => T)(current)
            : value

        if (typeof window !== "undefined") {
          const serialized = JSON.stringify(valueToStore)
          window.localStorage.setItem(prefixedKey, serialized)
          // Broadcast updates so other subscribers in the same tab stay in sync.
          window.dispatchEvent(
            new CustomEvent("local-storage", {
              detail: { key: prefixedKey, value: valueToStore },
            })
          )
        }

        return valueToStore
      })
    } catch (error) {
      console.error(error)
    }
  }

  initialValueRef.current = initialValue

  useEffect(() => {
    if (typeof window === "undefined") {
      return
    }

    const readValue = () => {
      try {
        const item = window.localStorage.getItem(prefixedKey)
        setStoredValue(item ? JSON.parse(item) : initialValueRef.current)
      } catch (error) {
        console.error(error)
      }
    }

    const handleStorageChange = (e: StorageEvent) => {
      if (e.key !== prefixedKey) {
        return
      }

      try {
        setStoredValue(
          e.newValue ? JSON.parse(e.newValue) : initialValueRef.current
        )
      } catch (error) {
        console.error(error)
      }
    }

    const handleCustomEvent = (event: Event) => {
      const { detail } = event as CustomEvent<
        { key: string; value: T } | undefined
      >

      if (!detail || detail.key !== prefixedKey) {
        return
      }

      setStoredValue(detail.value)
    }

    readValue()
    window.addEventListener("storage", handleStorageChange)
    window.addEventListener("local-storage", handleCustomEvent)
    return () => {
      window.removeEventListener("storage", handleStorageChange)
      window.removeEventListener("local-storage", handleCustomEvent)
    }
  }, [prefixedKey])

  return [storedValue, setValue]
}
