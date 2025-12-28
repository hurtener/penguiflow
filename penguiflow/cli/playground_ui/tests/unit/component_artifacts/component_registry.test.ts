import { describe, it, expect, beforeEach } from 'vitest';
import { componentRegistryStore, type ComponentRegistryPayload } from '$lib/stores/component_registry.svelte';

describe('componentRegistryStore', () => {
  beforeEach(() => {
    componentRegistryStore.reset();
  });

  describe('initial state', () => {
    it('starts with null registry', () => {
      expect(componentRegistryStore.registry).toBeNull();
    });

    it('starts with no error', () => {
      expect(componentRegistryStore.error).toBeNull();
    });

    it('reports enabled as false when no registry', () => {
      expect(componentRegistryStore.enabled).toBe(false);
    });

    it('returns empty components when no registry', () => {
      expect(componentRegistryStore.components).toEqual({});
    });

    it('returns empty version when no registry', () => {
      expect(componentRegistryStore.version).toBe('');
    });

    it('returns empty allowlist when no registry', () => {
      expect(componentRegistryStore.allowlist).toEqual([]);
    });
  });

  describe('setFromPayload', () => {
    const mockPayload: ComponentRegistryPayload = {
      version: '1.0.0',
      enabled: true,
      allowlist: ['markdown', 'json', 'code'],
      components: {
        markdown: {
          name: 'markdown',
          description: 'Render markdown content',
          propsSchema: { content: { type: 'string' } },
          interactive: false,
          category: 'display'
        },
        json: {
          name: 'json',
          description: 'Render JSON data',
          propsSchema: { data: { type: 'object' } },
          interactive: false,
          category: 'display'
        }
      }
    };

    it('sets registry from payload', () => {
      componentRegistryStore.setFromPayload(mockPayload);

      expect(componentRegistryStore.registry).toEqual(mockPayload);
    });

    it('reports enabled correctly', () => {
      componentRegistryStore.setFromPayload(mockPayload);

      expect(componentRegistryStore.enabled).toBe(true);
    });

    it('reports version correctly', () => {
      componentRegistryStore.setFromPayload(mockPayload);

      expect(componentRegistryStore.version).toBe('1.0.0');
    });

    it('reports allowlist correctly', () => {
      componentRegistryStore.setFromPayload(mockPayload);

      expect(componentRegistryStore.allowlist).toEqual(['markdown', 'json', 'code']);
    });

    it('clears error when payload is set', () => {
      componentRegistryStore.setError('Previous error');
      componentRegistryStore.setFromPayload(mockPayload);

      expect(componentRegistryStore.error).toBeNull();
    });
  });

  describe('setError', () => {
    it('sets error message', () => {
      componentRegistryStore.setError('Failed to load components');

      expect(componentRegistryStore.error).toBe('Failed to load components');
    });
  });

  describe('getComponent', () => {
    const mockPayload: ComponentRegistryPayload = {
      version: '1.0.0',
      enabled: true,
      allowlist: ['markdown', 'confirm'],
      components: {
        markdown: {
          name: 'markdown',
          description: 'Render markdown',
          propsSchema: {},
          interactive: false,
          category: 'display'
        },
        confirm: {
          name: 'confirm',
          description: 'Confirmation dialog',
          propsSchema: {},
          interactive: true,
          category: 'input'
        }
      }
    };

    it('returns undefined when no registry', () => {
      expect(componentRegistryStore.getComponent('markdown')).toBeUndefined();
    });

    it('returns component definition by name', () => {
      componentRegistryStore.setFromPayload(mockPayload);

      const component = componentRegistryStore.getComponent('markdown');
      expect(component).toEqual({
        name: 'markdown',
        description: 'Render markdown',
        propsSchema: {},
        interactive: false,
        category: 'display'
      });
    });

    it('returns undefined for unknown component', () => {
      componentRegistryStore.setFromPayload(mockPayload);

      expect(componentRegistryStore.getComponent('nonexistent')).toBeUndefined();
    });

    it('can retrieve interactive components', () => {
      componentRegistryStore.setFromPayload(mockPayload);

      const component = componentRegistryStore.getComponent('confirm');
      expect(component?.interactive).toBe(true);
    });
  });

  describe('reset', () => {
    it('clears registry', () => {
      componentRegistryStore.setFromPayload({
        version: '1.0.0',
        enabled: true,
        allowlist: [],
        components: {}
      });
      componentRegistryStore.reset();

      expect(componentRegistryStore.registry).toBeNull();
    });

    it('clears error', () => {
      componentRegistryStore.setError('Some error');
      componentRegistryStore.reset();

      expect(componentRegistryStore.error).toBeNull();
    });

    it('reports enabled as false after reset', () => {
      componentRegistryStore.setFromPayload({
        version: '1.0.0',
        enabled: true,
        allowlist: [],
        components: {}
      });
      componentRegistryStore.reset();

      expect(componentRegistryStore.enabled).toBe(false);
    });
  });

  describe('components with examples', () => {
    it('stores component examples', () => {
      const payloadWithExample: ComponentRegistryPayload = {
        version: '1.0.0',
        enabled: true,
        allowlist: ['metric'],
        components: {
          metric: {
            name: 'metric',
            description: 'Display a metric',
            propsSchema: {
              value: { type: 'number' },
              label: { type: 'string' }
            },
            interactive: false,
            category: 'display',
            tags: ['data', 'visualization'],
            example: {
              description: 'Show user count',
              props: { value: 42, label: 'Users' }
            }
          }
        }
      };

      componentRegistryStore.setFromPayload(payloadWithExample);

      const metric = componentRegistryStore.getComponent('metric');
      expect(metric?.example).toEqual({
        description: 'Show user count',
        props: { value: 42, label: 'Users' }
      });
      expect(metric?.tags).toEqual(['data', 'visualization']);
    });
  });

  describe('disabled registry', () => {
    it('reports enabled as false when explicitly disabled', () => {
      componentRegistryStore.setFromPayload({
        version: '1.0.0',
        enabled: false,
        allowlist: [],
        components: {}
      });

      expect(componentRegistryStore.enabled).toBe(false);
    });

    it('still allows component lookup when disabled', () => {
      componentRegistryStore.setFromPayload({
        version: '1.0.0',
        enabled: false,
        allowlist: ['markdown'],
        components: {
          markdown: {
            name: 'markdown',
            description: 'Markdown renderer',
            propsSchema: {},
            interactive: false,
            category: 'display'
          }
        }
      });

      expect(componentRegistryStore.getComponent('markdown')).toBeDefined();
    });
  });
});
