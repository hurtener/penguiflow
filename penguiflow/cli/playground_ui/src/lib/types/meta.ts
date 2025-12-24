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
