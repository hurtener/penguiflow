import { describe, expect, it } from 'vitest';
import { mergeMcpAppLlmContext } from '$lib/types';

describe('mergeMcpAppLlmContext', () => {
  it('nests MCP app model context under llm_context.mcp_app', () => {
    const merged = mergeMcpAppLlmContext(
      { tenant_summary: 'Q2 planning session' },
      {
        text: 'Revise slide 3',
        namespace: 'pengui_slides',
        modelContext: {
          structuredContent: {
            revision_request: {
              slide_id: 'slide-3',
              tone: 'executive',
            },
          },
        },
      }
    );

    expect(merged).toEqual({
      tenant_summary: 'Q2 planning session',
      mcp_app: {
        namespace: 'pengui_slides',
        model_context: {
          structuredContent: {
            revision_request: {
              slide_id: 'slide-3',
              tone: 'executive',
            },
          },
        },
      },
    });
  });

  it('deep-merges existing MCP app context', () => {
    const merged = mergeMcpAppLlmContext(
      {
        mcp_app: {
          namespace: 'pengui_slides',
          model_context: {
            structuredContent: {
              revision_request: {
                deck_id: 'deck-1',
              },
            },
          },
        },
      },
      {
        text: 'Revise slide 5',
        modelContext: {
          structuredContent: {
            revision_request: {
              slide_id: 'slide-5',
            },
          },
        },
      }
    );

    expect(merged).toEqual({
      mcp_app: {
        namespace: 'pengui_slides',
        model_context: {
          structuredContent: {
            revision_request: {
              deck_id: 'deck-1',
              slide_id: 'slide-5',
            },
          },
        },
      },
    });
  });
});
