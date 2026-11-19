/** Saves `blob` as a file named `filename` through a temporary link. */
export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const link = document.createElement("a")
  try {
    link.href = url
    link.download = filename
    document.body.appendChild(link) // Required for Firefox
    link.click()
  } finally {
    link.remove()
    // Revoking during the click cancels the download in Safari and Firefox.
    setTimeout(() => URL.revokeObjectURL(url), 0)
  }
}
