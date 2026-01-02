import { describe, it, expect, beforeEach } from 'vitest';
import { createAgentStore } from '$lib/stores';
import type { MetaResponse } from '$lib/types';

const agentStore = createAgentStore();

describe('agentStore', () => {
  beforeEach(() => {
    agentStore.reset();
  });

  describe('initial state', () => {
    it('has default meta', () => {
      expect(agentStore.meta.name).toBe('loading_agent');
      expect(agentStore.meta.tools).toBe(0);
      expect(agentStore.meta.flows).toBe(0);
    });

    it('has empty config and services', () => {
      expect(agentStore.plannerConfig).toEqual([]);
      expect(agentStore.services).toEqual([]);
      expect(agentStore.catalog).toEqual([]);
    });
  });

  describe('setFromResponse', () => {
    it('sets agent meta', () => {
      const response: MetaResponse = {
        agent: {
          name: 'test-agent',
          description: 'A test agent',
          template: 'react',
          version: '1.0.0',
          flags: ['streaming', 'hitl']
        },
        tools: [
          { name: 'search', description: 'Search tool' },
          { name: 'answer', description: 'Answer tool' }
        ],
        flows: [{ name: 'main' }]
      };

      agentStore.setFromResponse(response);

      expect(agentStore.meta.name).toBe('test-agent');
      expect(agentStore.meta.description).toBe('A test agent');
      expect(agentStore.meta.template).toBe('react');
      expect(agentStore.meta.version).toBe('1.0.0');
      expect(agentStore.meta.flags).toEqual(['streaming', 'hitl']);
      expect(agentStore.meta.tools).toBe(2);
      expect(agentStore.meta.flows).toBe(1);
    });

    it('sets planner config', () => {
      const response: MetaResponse = {
        agent: { name: 'agent' },
        planner: {
          max_hops: 10,
          model: 'gpt-4',
          temperature: 0.7
        }
      };

      agentStore.setFromResponse(response);

      expect(agentStore.plannerConfig).toHaveLength(3);
      const config = agentStore.plannerConfig as Array<{ label: string; value: unknown }>;
      expect(config.find((c) => c.label === 'max_hops')?.value).toBe(10);
      expect(config.find((c) => c.label === 'model')?.value).toBe('gpt-4');
    });

    it('sets services', () => {
      const response: MetaResponse = {
        agent: { name: 'agent' },
        services: [
          { name: 'db', enabled: true, url: 'localhost:5432' },
          { name: 'cache', enabled: false }
        ]
      };

      agentStore.setFromResponse(response);

      expect(agentStore.services).toHaveLength(2);
      expect(agentStore.services[0]).toEqual({
        name: 'db',
        status: 'enabled',
        url: 'localhost:5432'
      });
      expect(agentStore.services[1]).toEqual({
        name: 'cache',
        status: 'disabled',
        url: null
      });
    });

    it('sets tool catalog', () => {
      const response: MetaResponse = {
        agent: { name: 'agent' },
        tools: [
          { name: 'search', description: 'Web search', tags: ['retrieval'] },
          { name: 'calc', description: 'Calculator' }
        ]
      };

      agentStore.setFromResponse(response);

      expect(agentStore.catalog).toHaveLength(2);
      expect(agentStore.catalog[0]).toEqual({
        name: 'search',
        desc: 'Web search',
        tags: ['retrieval']
      });
      const second = agentStore.catalog[1]!;
      expect(second.tags).toEqual([]);
    });

    it('handles empty response', () => {
      agentStore.setFromResponse({} as MetaResponse);

      expect(agentStore.meta.name).toBe('agent');
      expect(agentStore.plannerConfig).toEqual([]);
      expect(agentStore.services).toEqual([]);
      expect(agentStore.catalog).toEqual([]);
    });
  });

  describe('reset', () => {
    it('resets to default state', () => {
      agentStore.setFromResponse({
        agent: { name: 'custom' },
        tools: [{ name: 'tool', description: 'desc' }]
      });

      agentStore.reset();

      expect(agentStore.meta.name).toBe('loading_agent');
      expect(agentStore.meta.tools).toBe(0);
      expect(agentStore.catalog).toEqual([]);
    });
  });
});
