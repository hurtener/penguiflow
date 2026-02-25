export type AgentMeta = {
  name: string;
  description: string;
  template: string;
  version: string;
  flags: string[];
  tools: number;
  flows: number;
};

export type ServiceInfo = {
  name: string;
  status: string;
  url: string | null;
};

export type ToolInfo = {
  name: string;
  desc: string;
  tags: string[];
};

export type ConfigItem = {
  label: string;
  value: string | number | boolean | null;
};

export interface MetaResponse {
  agent?: {
    name?: string;
    description?: string;
    template?: string;
    version?: string;
    flags?: string[];
  };
  planner?: Record<string, string | number | boolean | null>;
  services?: Array<{ name: string; enabled: boolean; url?: string }>;
  tools?: Array<{ name: string; description: string; tags?: string[] }>;
  flows?: unknown[];
}

export interface PlaygroundSetupState {
  fixed_session_id: string | null;
  rewrite_agui: boolean;
  fixed_session_source: 'runtime' | 'env' | 'none';
  rewrite_agui_source: 'runtime' | 'env';
  runtime_fixed_session_id: string | null;
  runtime_rewrite_agui: boolean | null;
  env_fixed_session_id: string | null;
  env_rewrite_agui: boolean;
}
