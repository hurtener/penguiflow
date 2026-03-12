<script lang="ts">
  import { onMount } from 'svelte';

  const MCP_APP_PROTOCOL_VERSION = '2026-01-26';

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

  interface JsonRpcRequest {
    jsonrpc: '2.0';
    id?: string | number | null;
    method: string;
    params?: Record<string, unknown>;
  }

  interface JsonRpcResponse {
    jsonrpc: '2.0';
    id?: string | number | null;
    result?: unknown;
    error?: { code: number; message: string };
    method?: string;
    params?: Record<string, unknown>;
  }

  interface Props {
    artifact_url?: string;
    html?: string;
    csp?: AppCSP;
    permissions?: AppPermissions;
    tool_data?: unknown;
    tool_input?: Record<string, unknown>;
    namespace?: string;
    session_id?: string;
    tenant_id?: string;
    user_id?: string;
    height?: string;
    prefers_border?: boolean;
    sandbox?: string;
    onToolCall?: (name: string, args: Record<string, unknown>) => Promise<unknown>;
    onSendMessage?: (payload: {
      text: string;
      namespace?: string;
      modelContext?: Record<string, unknown>;
    }) => Promise<void> | void;
  }

  let {
    artifact_url = undefined,
    html = undefined,
    csp = {},
    permissions = {},
    tool_data = undefined,
    tool_input = {},
    namespace = undefined,
    session_id = undefined,
    tenant_id = undefined,
    user_id = undefined,
    height = '500px',
    prefers_border = false,
    sandbox = undefined,
    onToolCall = undefined,
    onSendMessage = undefined,
  }: Props = $props();

  let iframeRef: HTMLIFrameElement | undefined = $state(undefined);
  let iframeHeight = $state('500px');
  let htmlContent = $state('');
  let loading = $state(false);
  let error = $state('');
  let initialized = $state(false);
  let pendingModelContext = $state<Record<string, unknown>>({});

  const computedSandbox = $derived(() => {
    const parts = new Set(
      (sandbox ?? 'allow-scripts allow-forms')
        .split(/\s+/)
        .map((part) => part.trim())
        .filter(Boolean)
    );
    parts.add('allow-same-origin');
    return Array.from(parts).join(' ');
  });

  function buildCSPMeta(): string {
    const scriptSources = ["'unsafe-inline'"];
    const styleSources = ["'unsafe-inline'"];
    const imageSources = ['data:', 'blob:'];
    const fontSources = ['data:'];
    const directives: string[] = ["default-src 'none'"];

    if (csp?.connect_domains?.length) {
      directives.push(`connect-src ${csp.connect_domains.join(' ')}`);
    }
    if (csp?.resource_domains?.length) {
      const domains = csp.resource_domains.join(' ');
      scriptSources.push(domains);
      styleSources.push(domains);
      imageSources.push(domains);
      fontSources.push(domains);
    }
    if (csp?.frame_domains?.length) {
      directives.push(`frame-src ${csp.frame_domains.join(' ')}`);
    }
    directives.push(`script-src ${scriptSources.join(' ')}`);
    directives.push(`style-src ${styleSources.join(' ')}`);
    directives.push(`img-src ${imageSources.join(' ')}`);
    directives.push(`font-src ${fontSources.join(' ')}`);

    return `<meta http-equiv="Content-Security-Policy" content="${directives.join('; ')}">`;
  }

  function injectBridge(rawHtml: string): string {
    const cspMeta = buildCSPMeta();
    const bridge = `
<script>
(function() {
  const MCP_ORIGIN = '*';
  let _jsonrpcId = 0;
  const _pending = new Map();

  window.addEventListener('message', function(event) {
    try {
      const msg = typeof event.data === 'string' ? JSON.parse(event.data) : event.data;
      if (!msg || !msg.jsonrpc) return;

      if (msg.id !== undefined && _pending.has(msg.id)) {
        const resolve = _pending.get(msg.id);
        _pending.delete(msg.id);
        resolve(msg.result ?? msg.error);
        return;
      }

      if (msg.method === 'tools/result' || msg.method === 'mcp/tool-result') {
        if (typeof window.onMcpToolResult === 'function') {
          window.onMcpToolResult(msg.params);
        }
        window.dispatchEvent(new CustomEvent('mcp:tool-result', { detail: msg.params }));
      }
      else if (msg.method === 'ui/theme' || msg.method === 'mcp/theme') {
        window.dispatchEvent(new CustomEvent('mcp:theme', { detail: msg.params }));
      }
      else if (msg.method === 'tools/response' || msg.method === 'mcp/tool-response') {
        window.dispatchEvent(new CustomEvent('mcp:tool-response', { detail: msg.params }));
      }
    } catch(e) { /* ignore malformed messages */ }
  });

  window.mcpRequest = function(method, params) {
    return new Promise(function(resolve) {
      const id = ++_jsonrpcId;
      _pending.set(id, resolve);
      parent.postMessage({
        jsonrpc: '2.0',
        id: id,
        method: method,
        params: params
      }, MCP_ORIGIN);
    });
  };

  window.mcpCallTool = function(name, args) {
    return window.mcpRequest('tools/call', { name: name, arguments: args || {} });
  };

  window.mcpSendMessage = function(text) {
    return window.mcpRequest('ui/message', { role: 'user', content: [{ type: 'text', text: text }] });
  };

  window.mcpResize = function(h) {
    return window.mcpRequest('ui/notifications/size-changed', { height: h });
  };
})();
<\/script>`;

    if (rawHtml.includes('</head>')) {
      return rawHtml.replace('</head>', cspMeta + bridge + '</head>');
    }
    if (rawHtml.includes('<body>')) {
      return rawHtml.replace('<body>', '<body>' + cspMeta + bridge);
    }
    return cspMeta + bridge + rawHtml;
  }

  function buildQueryString(): string {
    const params = new URLSearchParams();
    if (session_id) params.set('session_id', session_id);
    if (tenant_id) params.set('tenant_id', tenant_id);
    if (user_id) params.set('user_id', user_id);
    const query = params.toString();
    return query ? `?${query}` : '';
  }

  async function fetchAppApi(path: string, init?: RequestInit): Promise<unknown> {
    if (!namespace) {
      throw new Error('MCP App namespace is missing');
    }

    const response = await fetch(`/apps/${encodeURIComponent(namespace)}${path}${buildQueryString()}`, init);
    if (!response.ok) {
      const detail = await response.text();
      throw new Error(`Request failed (${response.status}): ${detail}`);
    }
    return await response.json();
  }

  async function callToolThroughApi(name: string, args: Record<string, unknown>): Promise<unknown> {
    const payload = await fetchAppApi('/call-tool', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, arguments: args ?? {} }),
    });

    if (payload && typeof payload === 'object' && 'result' in payload) {
      return (payload as Record<string, unknown>).result;
    }
    return payload;
  }

  async function listToolsThroughApi(): Promise<unknown> {
    return await fetchAppApi('/tools');
  }

  async function listResourcesThroughApi(): Promise<unknown> {
    return await fetchAppApi('/resources');
  }

  async function readResourceThroughApi(uri: string): Promise<unknown> {
    return await fetchAppApi('/read-resource', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ uri }),
    });
  }

  function normalizeRecord(value: unknown): Record<string, unknown> {
    return value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
  }

  function mergeModelContext(
    base: Record<string, unknown>,
    patch: Record<string, unknown>,
  ): Record<string, unknown> {
    const merged: Record<string, unknown> = { ...base };
    for (const [key, value] of Object.entries(patch)) {
      const current = merged[key];
      if (
        value &&
        typeof value === 'object' &&
        !Array.isArray(value) &&
        current &&
        typeof current === 'object' &&
        !Array.isArray(current)
      ) {
        merged[key] = mergeModelContext(current as Record<string, unknown>, value as Record<string, unknown>);
        continue;
      }
      merged[key] = value;
    }
    return merged;
  }

  function getTheme(): 'light' | 'dark' {
    return document.documentElement.classList.contains('dark') ? 'dark' : 'light';
  }

  function getContainerDimensions(): Record<string, number> {
    const width = iframeRef?.clientWidth || window.innerWidth || 0;
    return {
      width,
      maxHeight: Number.parseInt(iframeHeight, 10) || 500,
    };
  }

  function buildHostCapabilities(): Record<string, unknown> {
    const sandboxPermissions: Record<string, Record<string, never>> = {};
    if (permissions?.camera) sandboxPermissions.camera = {};
    if (permissions?.microphone) sandboxPermissions.microphone = {};
    if (permissions?.geolocation) sandboxPermissions.geolocation = {};
    if (permissions?.clipboard_write) sandboxPermissions.clipboardWrite = {};

    const sandboxCsp: Record<string, string[]> = {};
    if (csp?.connect_domains?.length) sandboxCsp.connectDomains = csp.connect_domains;
    if (csp?.resource_domains?.length) sandboxCsp.resourceDomains = csp.resource_domains;
    if (csp?.frame_domains?.length) sandboxCsp.frameDomains = csp.frame_domains;
    if (csp?.base_uri_domains?.length) sandboxCsp.baseUriDomains = csp.base_uri_domains;

    return {
      openLinks: {},
      serverTools: { listChanged: true },
      serverResources: { listChanged: true },
      logging: {},
      sandbox: {
        permissions: sandboxPermissions,
        csp: sandboxCsp,
      },
      updateModelContext: {
        text: {},
        image: {},
        resource: {},
        resourceLink: {},
        structuredContent: {},
      },
      message: {
        text: {},
        resource: {},
        resourceLink: {},
        structuredContent: {},
      },
    };
  }

  function buildHostContext(): Record<string, unknown> {
    const resolvedTimeZone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    const touchCapable = navigator.maxTouchPoints > 0;
    return {
      theme: getTheme(),
      displayMode: 'inline',
      availableDisplayModes: ['inline'],
      containerDimensions: getContainerDimensions(),
      locale: navigator.language,
      timeZone: resolvedTimeZone,
      userAgent: navigator.userAgent,
      platform: 'web',
      deviceCapabilities: {
        touch: touchCapable,
        hover: window.matchMedia('(hover: hover)').matches,
      },
    };
  }

  function wrapToolResultPayload(result: unknown): Record<string, unknown> {
    if (result && typeof result === 'object' && !Array.isArray(result)) {
      const payload = result as Record<string, unknown>;
      if ('content' in payload || 'structuredContent' in payload || 'isError' in payload || '_meta' in payload) {
        return payload;
      }
    }
    return {
      content: [],
      structuredContent: result,
      isError: false,
    };
  }

  function extractUserText(params: Record<string, unknown> | undefined): string {
    const directText = params?.text;
    if (typeof directText === 'string' && directText.trim()) return directText;

    const content = params?.content;
    if (!Array.isArray(content)) return '';
    return content
      .map((block) => {
        if (!block || typeof block !== 'object') return '';
        const text = (block as Record<string, unknown>).text;
        return typeof text === 'string' ? text : '';
      })
      .filter(Boolean)
      .join('\n');
  }

  function parseJsonRpcMessage(data: unknown): JsonRpcRequest | null {
    if (!data) return null;
    if (typeof data === 'string') {
      try {
        return JSON.parse(data) as JsonRpcRequest;
      } catch {
        return null;
      }
    }
    if (typeof data === 'object' && (data as Record<string, unknown>).jsonrpc === '2.0') {
      return data as JsonRpcRequest;
    }
    return null;
  }

  function toCloneable(value: unknown): unknown {
    if (
      value === null ||
      value === undefined ||
      typeof value === 'string' ||
      typeof value === 'number' ||
      typeof value === 'boolean'
    ) {
      return value;
    }
    if (Array.isArray(value)) {
      return value.map((item) => toCloneable(item));
    }
    if (typeof value === 'object') {
      const plain: Record<string, unknown> = {};
      for (const [key, nested] of Object.entries(value as Record<string, unknown>)) {
        if (typeof nested === 'function' || typeof nested === 'symbol') continue;
        const normalized = toCloneable(nested);
        if (normalized !== undefined) {
          plain[key] = normalized;
        }
      }
      return plain;
    }
    return String(value);
  }

  function sendToApp(msg: JsonRpcResponse): void {
    if (iframeRef?.contentWindow) {
      iframeRef.contentWindow.postMessage(toCloneable(msg), '*');
    }
  }

  function sendResponse(id: string | number | null | undefined, result: unknown): void {
    if (id === undefined) return;
    sendToApp({ jsonrpc: '2.0', id, result });
  }

  function sendError(id: string | number | null | undefined, code: number, message: string): void {
    if (id === undefined) return;
    sendToApp({ jsonrpc: '2.0', id, error: { code, message } });
  }

  function sendNotification(method: string, params: Record<string, unknown> = {}): void {
    sendToApp({ jsonrpc: '2.0', method, params });
  }

  function pushLegacyToolData(): void {
    if (tool_data !== undefined) {
      sendNotification('tools/result', wrapToolResultPayload(tool_data));
    }
  }

  function pushProtocolToolState(): void {
    sendNotification('ui/notifications/tool-input', { arguments: normalizeRecord(tool_input) });
    if (tool_data !== undefined) {
      sendNotification('ui/notifications/tool-result', wrapToolResultPayload(tool_data));
    }
  }

  async function handleMessage(event: MessageEvent): Promise<void> {
    if (!iframeRef || event.source !== iframeRef.contentWindow) return;

    const msg = parseJsonRpcMessage(event.data);
    if (!msg) return;

    try {
      switch (msg.method) {
        case 'ui/initialize': {
          initialized = true;
          sendResponse(msg.id, {
            protocolVersion: MCP_APP_PROTOCOL_VERSION,
            hostInfo: {
              name: 'PenguiFlow Playground',
              version: '3.1.1',
            },
            hostCapabilities: buildHostCapabilities(),
            hostContext: buildHostContext(),
          });
          queueMicrotask(() => {
            sendNotification('ui/notifications/initialized');
            pushProtocolToolState();
          });
          return;
        }
        case 'tools/call':
        case 'mcp/call-tool': {
          const { name, arguments: args } = normalizeRecord(msg.params);
          if (typeof name !== 'string' || !name) {
            sendError(msg.id, -32602, 'Invalid tool name');
            return;
          }
          const normalizedArgs = normalizeRecord(args);
          const handler = onToolCall ?? callToolThroughApi;
          sendResponse(msg.id, await handler(name, normalizedArgs));
          return;
        }
        case 'tools/list': {
          sendResponse(msg.id, await listToolsThroughApi());
          return;
        }
        case 'resources/list': {
          const payload = normalizeRecord(await listResourcesThroughApi());
          sendResponse(msg.id, { resources: Array.isArray(payload.resources) ? payload.resources : [] });
          return;
        }
        case 'resources/templates/list': {
          const payload = normalizeRecord(await listResourcesThroughApi());
          sendResponse(msg.id, {
            resourceTemplates: Array.isArray(payload.resourceTemplates) ? payload.resourceTemplates : [],
          });
          return;
        }
        case 'resources/read': {
          const { uri } = normalizeRecord(msg.params);
          if (typeof uri !== 'string' || !uri) {
            sendError(msg.id, -32602, 'Invalid resource uri');
            return;
          }
          sendResponse(msg.id, await readResourceThroughApi(uri));
          return;
        }
        case 'ui/message':
        case 'mcp/send-message': {
          const text = extractUserText(normalizeRecord(msg.params));
          if (text && onSendMessage) {
            await onSendMessage({
              text,
              ...(namespace ? { namespace } : {}),
              ...(Object.keys(pendingModelContext).length ? { modelContext: pendingModelContext } : {}),
            });
          }
          sendResponse(msg.id, {});
          return;
        }
        case 'ui/open-link': {
          const { url } = normalizeRecord(msg.params);
          if (typeof url !== 'string' || !url) {
            sendError(msg.id, -32602, 'Invalid URL');
            return;
          }
          window.open(url, '_blank', 'noopener,noreferrer');
          sendResponse(msg.id, {});
          return;
        }
        case 'ui/request-display-mode': {
          sendResponse(msg.id, { mode: 'inline' });
          return;
        }
        case 'ui/update-model-context': {
          pendingModelContext = mergeModelContext(pendingModelContext, normalizeRecord(msg.params));
          sendResponse(msg.id, {});
          return;
        }
        case 'ui/notifications/size-changed':
        case 'mcp/resize': {
          const { height: nextHeight } = normalizeRecord(msg.params);
          if (typeof nextHeight === 'number') iframeHeight = `${nextHeight}px`;
          else if (typeof nextHeight === 'string' && nextHeight) iframeHeight = nextHeight;
          if (msg.id !== undefined) sendResponse(msg.id, {});
          return;
        }
        case 'notifications/message':
        case 'ping':
        case 'ui/notifications/initialized': {
          if (msg.id !== undefined) sendResponse(msg.id, {});
          return;
        }
        default: {
          sendError(msg.id, -32601, `Method not found: ${msg.method}`);
        }
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      sendError(msg.id, -32000, message);
    }
  }

  function handleIframeLoad(): void {
    if (!initialized) {
      pushLegacyToolData();
    }
  }

  $effect(() => {
    iframeHeight = typeof height === 'string' && height.trim() ? height : '500px';
  });

  $effect(() => {
    initialized = false;
    error = '';
    pendingModelContext = {};

    if (typeof html === 'string' && html) {
      htmlContent = html;
      loading = false;
      return;
    }

    htmlContent = '';

    if (!artifact_url) {
      loading = false;
      return;
    }

    loading = true;
    let cancelled = false;

    void (async () => {
      try {
        const resp = await fetch(artifact_url);
        if (cancelled) return;
        if (resp.ok) {
          htmlContent = await resp.text();
        } else {
          error = `Failed to load app: ${resp.status}`;
        }
      } catch (e) {
        if (cancelled) return;
        error = `Failed to load app: ${e}`;
      } finally {
        if (!cancelled) {
          loading = false;
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  });

  onMount(() => {
    window.addEventListener('message', handleMessage);
    return () => {
      if (typeof window !== 'undefined') {
        window.removeEventListener('message', handleMessage);
      }
    };
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
    background: var(--color-card-bg, #fcfaf7);
    border-radius: calc(var(--radius-lg, 12px) - 2px);
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
