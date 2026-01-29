let installed = false;

export function installGlobalErrorHandlers(): void {
  if (installed || typeof window === 'undefined') return;
  installed = true;

  window.addEventListener('error', (event) => {
    console.error('[playground-ui] Unhandled error:', event.error ?? event.message);
  });

  window.addEventListener('unhandledrejection', (event) => {
    console.error('[playground-ui] Unhandled rejection:', event.reason);
  });
}
