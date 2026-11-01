/**
 * Retrieves or initializes a unique caller uuid stored in localStorage.
 * Wrapped in try/catch to safely handle private browsing modes where storage access throws.
 */
export function getOrCreateCallerId(): string | null {
  if (typeof window === 'undefined') return null;

  try {
    let id = localStorage.getItem('mcp_caller_id');
    if (!id) {
      id = crypto.randomUUID();
      localStorage.setItem('mcp_caller_id', id);
    }
    return id;
  } catch {
    // In private windows or when cookies/storage are blocked, return null silently
    return null;
  }
}
