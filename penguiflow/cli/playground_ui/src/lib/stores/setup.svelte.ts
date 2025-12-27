import { parseJsonObject } from '$lib/utils';

export interface SetupContext {
  toolContext: Record<string, unknown>;
  llmContext: Record<string, unknown>;
}

function createSetupStore() {
  let tenantId = $state('playground-tenant');
  let userId = $state('playground-user');
  let toolContextRaw = $state('{}');
  let llmContextRaw = $state('{}');
  let error = $state<string | null>(null);
  let useAgui = $state(false);

  return {
    get tenantId() { return tenantId; },
    set tenantId(v: string) { tenantId = v; },

    get userId() { return userId; },
    set userId(v: string) { userId = v; },

    get toolContextRaw() { return toolContextRaw; },
    set toolContextRaw(v: string) { toolContextRaw = v; },

    get llmContextRaw() { return llmContextRaw; },
    set llmContextRaw(v: string) { llmContextRaw = v; },

    get error() { return error; },
    set error(v: string | null) { error = v; },

    get useAgui() { return useAgui; },
    set useAgui(v: boolean) { useAgui = v; },

    /**
     * Parse and validate contexts
     * @returns Parsed contexts or null if error (error is set internally)
     */
    parseContexts(): SetupContext | null {
      try {
        const extraTool = parseJsonObject(toolContextRaw, { label: 'Tool context' });
        const toolContext = {
          tenant_id: tenantId,
          user_id: userId,
          ...extraTool
        };
        const llmContext = parseJsonObject(llmContextRaw, { label: 'LLM context' });
        error = null;
        return { toolContext, llmContext };
      } catch (e) {
        error = e instanceof Error ? e.message : 'Invalid setup configuration.';
        return null;
      }
    },

    clearError() {
      error = null;
    },

    reset() {
      tenantId = 'playground-tenant';
      userId = 'playground-user';
      toolContextRaw = '{}';
      llmContextRaw = '{}';
      error = null;
      useAgui = false;
    }
  };
}

export const setupStore = createSetupStore();
