import { fireEvent, render, waitFor } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';
import McpApp from '$lib/renderers/McpApp.svelte';

describe('McpApp', () => {
  it('allows same-origin sandboxing and data URL images for embedded apps', async () => {
    render(McpApp, {
      props: {
        html: '<!doctype html><html><head></head><body><div id="app"></div></body></html>',
      }
    });

    const iframe = await waitFor(() => {
      const frame = document.querySelector('.mcp-app-frame') as HTMLIFrameElement | null;
      expect(frame).toBeTruthy();
      return frame as HTMLIFrameElement;
    });

    expect(iframe.getAttribute('sandbox')).toContain('allow-same-origin');
    expect(iframe.getAttribute('srcdoc')).toContain("img-src data: blob:");
  });

  it('normalizes proxied tool payloads before posting them into the iframe', async () => {
    const proxiedToolData = new Proxy(
      {
        content: [{ type: 'text', text: 'Opening editor' }],
        structuredContent: {
          editor_state: {
            deck: { id: 'deck-1' },
          },
        },
        isError: false,
      },
      {}
    );

    render(McpApp, {
      props: {
        html: '<!doctype html><html><body><div id="app"></div></body></html>',
        tool_data: proxiedToolData,
      }
    });

    const iframe = await waitFor(() => {
      const frame = document.querySelector('.mcp-app-frame') as HTMLIFrameElement | null;
      expect(frame).toBeTruthy();
      return frame as HTMLIFrameElement;
    });

    const postMessage = vi.fn();
    Object.defineProperty(iframe, 'contentWindow', {
      configurable: true,
      value: { postMessage },
    });

    await fireEvent.load(iframe);

    expect(postMessage).toHaveBeenCalled();
    const [message] = postMessage.mock.calls[0];
    expect(message).toEqual({
      jsonrpc: '2.0',
      method: 'tools/result',
      params: {
        content: [{ type: 'text', text: 'Opening editor' }],
        structuredContent: {
          editor_state: {
            deck: { id: 'deck-1' },
          },
        },
        isError: false,
      },
    });
    expect(message.params).not.toBe(proxiedToolData);
  });

  it('forwards ui/message with the latest ui/update-model-context payload', async () => {
    const onSendMessage = vi.fn(async () => {});

    render(McpApp, {
      props: {
        html: '<!doctype html><html><body><div id="app"></div></body></html>',
        namespace: 'pengui_slides',
        onSendMessage,
      }
    });

    const iframe = await waitFor(() => {
      const frame = document.querySelector('.mcp-app-frame') as HTMLIFrameElement | null;
      expect(frame).toBeTruthy();
      return frame as HTMLIFrameElement;
    });

    const postMessage = vi.fn();
    const contentWindow = { postMessage };
    Object.defineProperty(iframe, 'contentWindow', {
      configurable: true,
      value: contentWindow,
    });

    window.dispatchEvent(new MessageEvent('message', {
      data: {
        jsonrpc: '2.0',
        id: 10,
        method: 'ui/update-model-context',
        params: {
          structuredContent: {
            revision_request: {
              slide_id: 'slide-1',
              instruction: 'Make it more executive'
            }
          }
        }
      },
      source: contentWindow as unknown as MessageEventSource,
    }));

    window.dispatchEvent(new MessageEvent('message', {
      data: {
        jsonrpc: '2.0',
        id: 11,
        method: 'ui/message',
        params: {
          role: 'user',
          content: [{ type: 'text', text: 'Please revise this slide.' }],
        }
      },
      source: contentWindow as unknown as MessageEventSource,
    }));

    await waitFor(() => {
      expect(onSendMessage).toHaveBeenCalledWith({
        text: 'Please revise this slide.',
        namespace: 'pengui_slides',
        modelContext: {
          structuredContent: {
            revision_request: {
              slide_id: 'slide-1',
              instruction: 'Make it more executive'
            }
          }
        }
      });
    });
  });
});
