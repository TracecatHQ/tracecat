type Listener = () => void

class EntityEvents {
  private addFieldListeners: Set<Listener> = new Set()

  onAddField(listener: Listener) {
    this.addFieldListeners.add(listener)
    return () => {
      this.addFieldListeners.delete(listener)
    }
  }

  emitAddField() {
    this.addFieldListeners.forEach((cb) => cb())
  }
}

export const entityEvents = new EntityEvents()
