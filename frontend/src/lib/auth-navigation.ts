type DocumentNavigator = Pick<Location, "reload">

/**
 * Reload the current document so authentication transitions start with a new
 * query client and no state from the previous session.
 */
export function reloadCurrentDocument(
  location: DocumentNavigator = window.location
): void {
  location.reload()
}

/**
 * Navigate to a URL through a full document load.
 */
export function navigateToDocument(
  url: string,
  location: Pick<Location, "assign"> = window.location
): void {
  location.assign(url)
}
