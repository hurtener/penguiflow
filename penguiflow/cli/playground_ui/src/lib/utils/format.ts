/**
 * Format timestamp to HH:MM display
 */
export const formatTime = (ts: number): string =>
  new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

/**
 * Generate a random UUID
 */
export const randomId = (): string => crypto.randomUUID();
