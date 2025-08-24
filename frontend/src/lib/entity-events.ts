class EntityEventEmitter {
  private static instance: EntityEventEmitter
  private listeners: Map<string, Set<() => void>> = new Map()

  private constructor() {}

  static getInstance(): EntityEventEmitter {
    if (!EntityEventEmitter.instance) {
      EntityEventEmitter.instance = new EntityEventEmitter()
    }
    return EntityEventEmitter.instance
  }

  emitAddField() {
    const callbacks = this.listeners.get("add-field")
    if (callbacks) {
      // Copy the Set to avoid issues if callbacks modify the Set during iteration
      Array.from(callbacks).forEach((callback) => callback())
    }
  }

  onAddField(callback: () => void) {
    if (!this.listeners.has("add-field")) {
      this.listeners.set("add-field", new Set())
    }
    this.listeners.get("add-field")!.add(callback)

    return () => {
      const callbacks = this.listeners.get("add-field")
      if (callbacks) {
        callbacks.delete(callback)
      }
    }
  }
}

export const entityEvents = EntityEventEmitter.getInstance()
