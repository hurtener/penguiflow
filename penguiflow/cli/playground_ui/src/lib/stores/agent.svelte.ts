import type { AgentMeta, ConfigItem, ServiceInfo, ToolInfo, MetaResponse } from '$lib/types';

const DEFAULT_META: AgentMeta = {
  name: 'loading_agent',
  description: '',
  template: '',
  version: '',
  flags: [],
  tools: 0,
  flows: 0
};

function createAgentStore() {
  let meta = $state<AgentMeta>({ ...DEFAULT_META });
  let plannerConfig = $state<ConfigItem[]>([]);
  let services = $state<ServiceInfo[]>([]);
  let catalog = $state<ToolInfo[]>([]);

  return {
    get meta() { return meta; },
    get plannerConfig() { return plannerConfig; },
    get services() { return services; },
    get catalog() { return catalog; },

    setFromResponse(data: MetaResponse) {
      const agent = data.agent ?? {};
      meta = {
        name: agent.name ?? 'agent',
        description: agent.description ?? '',
        template: agent.template ?? '',
        version: agent.version ?? '',
        flags: agent.flags ?? [],
        tools: (data.tools ?? []).length,
        flows: (data.flows ?? []).length
      };

      plannerConfig = data.planner && Object.keys(data.planner).length
        ? Object.entries(data.planner).map(([label, value]) => ({
            label,
            value: value as string | number | boolean | null
          }))
        : [];

      services = data.services?.map(svc => ({
        name: svc.name,
        status: svc.enabled ? 'enabled' : 'disabled',
        url: svc.url ?? null
      })) ?? [];

      catalog = data.tools?.map(tool => ({
        name: tool.name,
        desc: tool.description,
        tags: tool.tags ?? []
      })) ?? [];
    },

    reset() {
      meta = { ...DEFAULT_META };
      plannerConfig = [];
      services = [];
      catalog = [];
    }
  };
}

export const agentStore = createAgentStore();
