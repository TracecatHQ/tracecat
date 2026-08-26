import { useCallback, useEffect, useRef, useState } from "react"

type LocalStorageUpdater<T> = T | ((value: T) => T)

/**
 * Persists a value in `localStorage` and keeps every subscriber of the same key
 * in sync, both across tabs (`storage` events) and within the current tab (a
 * `local-storage` custom event). Returns the current value and a setter that
 * accepts either a value or an updater function, like `useState`.
 */
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

  // Mirror the value in a ref so updaters resolve outside of React state
  // updaters: React treats those as pure and may run them during render or more
  // than once, which would replay the persist + dispatch side effects below.
  const storedValueRef = useRef(storedValue)

  const setValue = useCallback(
    (value: LocalStorageUpdater<T>) => {
      const valueToStore =
        typeof value === "function"
          ? (value as (current: T) => T)(storedValueRef.current)
          : value

      storedValueRef.current = valueToStore
      setStoredValue(valueToStore)

      try {
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
      } catch (error) {
        console.error(error)
      }
    },
    [prefixedKey]
  )

  initialValueRef.current = initialValue
  storedValueRef.current = storedValue

  useEffect(() => {
    if (typeof window === "undefined") {
      return
    }

    const applyValue = (next: T) => {
      storedValueRef.current = next
      setStoredValue(next)
    }

    const readValue = () => {
      try {
        const item = window.localStorage.getItem(prefixedKey)
        applyValue(item ? JSON.parse(item) : initialValueRef.current)
      } catch (error) {
        console.error(error)
      }
    }

    const handleStorageChange = (e: StorageEvent) => {
      if (e.key !== prefixedKey) {
        return
      }

      try {
        applyValue(
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

      // The dispatcher already holds this value; skip its own echo.
      if (Object.is(detail.value, storedValueRef.current)) {
        return
      }

      applyValue(detail.value)
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
