<script lang="ts">
  import { onMount, onDestroy } from 'svelte';

  interface AppCSP {
    connect_domains?: string[];
    resource_domains?: string[];
    frame_domains?: string[];
    base_uri_domains?: string[];
  }

  interface AppPermissions {
    camera?: boolean;
    microphone?: boolean;
    geolocation?: boolean;
    clipboard_write?: boolean;
  }

  interface Props {
    artifact_url?: string;
    html?: string;
    csp?: AppCSP;
    permissions?: AppPermissions;
    tool_data?: unknown;
    height?: string;
    prefers_border?: boolean;
    sandbox?: string;
    onToolCall?: (name: string, args: Record<string, unknown>) => Promise<unknown>;
    onSendMessage?: (text: string) => void;
  }

  let {
    artifact_url = undefined,
    html = undefined,
    csp = {},
    permissions = {},
    tool_data = undefined,
    height = '500px',
    prefers_border = false,
    sandbox = undefined,
    onToolCall = undefined,
    onSendMessage = undefined,
  }: Props = $props();

  let iframeRef: HTMLIFrameElement | undefined = $state(undefined);
  let iframeHeight = $state(height);
  let htmlContent = $state(html ?? '');
  let loading = $state(!!artifact_url);
  let error = $state('');

  // Build sandbox attribute from permissions
  const computedSandbox = $derived(() => {
    if (sandbox) return sandbox;
    const parts = ['allow-scripts', 'allow-forms'];
    if (permissions?.clipboard_write) parts.push('allow-same-origin');
    return parts.join(' ');
  });

  // Build CSP meta tag
  function buildCSPMeta(): string {
    const directives: string[] = ["default-src 'none'", "script-src 'unsafe-inline'", "style-src 'unsafe-inline'"];

    if (csp?.connect_domains?.length) {
      directives.push(`connect-src ${csp.connect_domains.join(' ')}`);
    }
    if (csp?.resource_domains?.length) {
      const domains = csp.resource_domains.join(' ');
      directives.push(`script-src 'unsafe-inline' ${domains}`);
      directives.push(`img-src ${domains}`);
      directives.push(`font-src ${domains}`);
      directives.push(`style-src 'unsafe-inline' ${domains}`);
    }
    if (csp?.frame_domains?.length) {
      directives.push(`frame-src ${csp.frame_domains.join(' ')}`);
    }

    return `<meta http-equiv="Content-Security-Policy" content="${directives.join('; ')}">`;
  }

  // Inject the postMessage bridge into the HTML
  function injectBridge(rawHtml: string): string {
    const bridge = `
<script>
(function() {
  const MCP_ORIGIN = '*';
  let _jsonrpcId = 0;
  const _pending = new Map();

  // Receive messages from host
  window.addEventListener('message', function(event) {
    try {
      const msg = typeof event.data === 'string' ? JSON.parse(event.data) : event.data;
      if (!msg || !msg.jsonrpc) return;

      // Handle responses to our requests
      if (msg.id !== undefined && _pending.has(msg.id)) {
        const resolve = _pending.get(msg.id);
        _pending.delete(msg.id);
        resolve(msg.result ?? msg.error);
        return;
      }

      // Handle host-initiated methods
      if (msg.method === 'mcp/tool-result') {
        if (typeof window.onMcpToolResult === 'function') {
          window.onMcpToolResult(msg.params);
        }
        // Also dispatch as custom event
        window.dispatchEvent(new CustomEvent('mcp:tool-result', { detail: msg.params }));
      }
      else if (msg.method === 'mcp/theme') {
        window.dispatchEvent(new CustomEvent('mcp:theme', { detail: msg.params }));
      }
      else if (msg.method === 'mcp/tool-response') {
        window.dispatchEvent(new CustomEvent('mcp:tool-response', { detail: msg.params }));
      }
    } catch(e) { /* ignore malformed messages */ }
  });

  // Send JSON-RPC request to host
  window.mcpRequest = function(method, params) {
    return new Promise(function(resolve) {
      const id = ++_jsonrpcId;
      _pending.set(id, resolve);
      parent.postMessage(JSON.stringify({
        jsonrpc: '2.0',
        id: id,
        method: method,
        params: params
      }), MCP_ORIGIN);
    });
  };

  // Convenience methods
  window.mcpCallTool = function(name, args) {
    return window.mcpRequest('mcp/call-tool', { name: name, arguments: args || {} });
  };

  window.mcpSendMessage = function(text) {
    return window.mcpRequest('mcp/send-message', { text: text });
  };

  window.mcpRequestTheme = function() {
    return window.mcpRequest('mcp/request-theme', {});
  };

  window.mcpResize = function(h) {
    return window.mcpRequest('mcp/resize', { height: h });
  };
})();
<\/script>`;

    // Insert bridge before closing </head> or at start of body
    if (rawHtml.includes('</head>')) {
      return rawHtml.replace('</head>', bridge + '</head>');
    }
    if (rawHtml.includes('<body>')) {
      return rawHtml.replace('<body>', '<body>' + bridge);
    }
    return bridge + rawHtml;
  }

  // Handle postMessage from iframe
  function handleMessage(event: MessageEvent) {
    if (!iframeRef || event.source !== iframeRef.contentWindow) return;

    try {
      const msg = typeof event.data === 'string' ? JSON.parse(event.data) : event.data;
      if (!msg || msg.jsonrpc !== '2.0') return;

      if (msg.method === 'mcp/call-tool' && onToolCall) {
        const { name, arguments: args } = msg.params ?? {};
        onToolCall(name, args ?? {}).then((result) => {
          sendToApp({ jsonrpc: '2.0', id: msg.id, result });
        }).catch((err: Error) => {
          sendToApp({ jsonrpc: '2.0', id: msg.id, error: { code: -1, message: err.message } });
        });
      }
      else if (msg.method === 'mcp/send-message' && onSendMessage) {
        const text = msg.params?.text;
        if (text) onSendMessage(text);
        sendToApp({ jsonrpc: '2.0', id: msg.id, result: { ok: true } });
      }
      else if (msg.method === 'mcp/request-theme') {
        sendToApp({
          jsonrpc: '2.0',
          id: msg.id,
          result: { theme: document.documentElement.classList.contains('dark') ? 'dark' : 'light' }
        });
      }
      else if (msg.method === 'mcp/resize') {
        const h = msg.params?.height;
        if (h) iframeHeight = typeof h === 'number' ? `${h}px` : h;
        sendToApp({ jsonrpc: '2.0', id: msg.id, result: { ok: true } });
      }
    } catch { /* ignore malformed messages */ }
  }

  function sendToApp(msg: unknown) {
    if (iframeRef?.contentWindow) {
      iframeRef.contentWindow.postMessage(JSON.stringify(msg), '*');
    }
  }

  function pushToolData() {
    if (tool_data !== undefined) {
      sendToApp({
        jsonrpc: '2.0',
        method: 'mcp/tool-result',
        params: tool_data,
      });
    }
  }

  function handleIframeLoad() {
    // Push tool data once the iframe loads
    pushToolData();
  }

  // Fetch HTML from artifact URL if needed
  onMount(async () => {
    window.addEventListener('message', handleMessage);

    if (artifact_url && !html) {
      try {
        const resp = await fetch(artifact_url);
        if (resp.ok) {
          htmlContent = await resp.text();
        } else {
          error = `Failed to load app: ${resp.status}`;
        }
      } catch (e) {
        error = `Failed to load app: ${e}`;
      } finally {
        loading = false;
      }
    }
  });

  onDestroy(() => {
    if (typeof window !== 'undefined') {
      window.removeEventListener('message', handleMessage);
    }
  });

  const srcdoc = $derived(htmlContent ? injectBridge(htmlContent) : '');
</script>

{#if loading}
  <div class="mcp-app-loading">Loading MCP App...</div>
{:else if error}
  <div class="mcp-app-error">{error}</div>
{:else}
  <iframe
    bind:this={iframeRef}
    class="mcp-app-frame"
    class:bordered={prefers_border}
    style={`height: ${iframeHeight};`}
    sandbox={computedSandbox()}
    {srcdoc}
    title="MCP App"
    onload={handleIframeLoad}
  ></iframe>
{/if}

<style>
  .mcp-app-frame {
    width: 100%;
    border: none;
    background: #ffffff;
    border-radius: 8px;
  }
  .mcp-app-frame.bordered {
    border: 1px solid var(--border-color, #e2e8f0);
  }
  .mcp-app-loading {
    display: flex;
    align-items: center;
    justify-content: center;
    height: 200px;
    color: var(--text-muted, #6b7280);
    font-size: 0.875rem;
  }
  .mcp-app-error {
    display: flex;
    align-items: center;
    justify-content: center;
    height: 200px;
    color: var(--text-error, #ef4444);
    font-size: 0.875rem;
  }
</style>
