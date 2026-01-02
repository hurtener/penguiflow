import { render, screen, waitFor } from '@testing-library/svelte';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import ComponentRendererHost from './ComponentRendererHost.svelte';
import { createComponentRegistryStore } from '$lib/stores';

const componentRegistryStore = createComponentRegistryStore();

const basePayload = {
  version: '1.0.0',
  enabled: true,
  allowlist: [],
  components: {}
};

describe('ComponentRenderer', () => {
  beforeEach(() => {
    componentRegistryStore.reset();
  });

  describe('rendering', () => {
    it('renders markdown component', async () => {
      componentRegistryStore.setFromPayload({
        ...basePayload,
        components: {
          markdown: { name: 'markdown', interactive: false, description: '', propsSchema: {}, category: 'display' }
        }
      });

      render(ComponentRendererHost, {
        props: {
          componentRegistryStore,
          component: 'markdown',
          props: { content: '# Hello World' }
        }
      });

      expect(await screen.findByText('Hello World')).toBeInTheDocument();
    });

    it('renders json component with data', async () => {
      componentRegistryStore.setFromPayload({
        ...basePayload,
        components: {
          json: { name: 'json', interactive: false, description: '', propsSchema: {}, category: 'display' }
        }
      });

      render(ComponentRendererHost, {
        props: {
          componentRegistryStore,
          component: 'json',
          props: { data: { key: 'value' } }
        }
      });

      expect(await screen.findByText('key')).toBeInTheDocument();
    });

    it('renders metric component', async () => {
      componentRegistryStore.setFromPayload({
        ...basePayload,
        components: {
          metric: { name: 'metric', interactive: false, description: '', propsSchema: {}, category: 'display' }
        }
      });

      render(ComponentRendererHost, {
        props: {
          componentRegistryStore,
          component: 'metric',
          props: { value: 42, label: 'Users' }
        }
      });

      expect(await screen.findByText('42')).toBeInTheDocument();
      expect(await screen.findByText('Users')).toBeInTheDocument();
    });

    it('renders code component with syntax', async () => {
      componentRegistryStore.setFromPayload({
        ...basePayload,
        components: {
          code: { name: 'code', interactive: false, description: '', propsSchema: {}, category: 'display' }
        }
      });

      render(ComponentRendererHost, {
        props: {
          componentRegistryStore,
          component: 'code',
          props: {
            code: 'const x = 1;',
            language: 'javascript',
            filename: 'test.js'
          }
        }
      });

      expect(await screen.findByText('const x = 1;')).toBeInTheDocument();
      expect(await screen.findByText('test.js')).toBeInTheDocument();
    });

    it('renders callout component', async () => {
      componentRegistryStore.setFromPayload({
        ...basePayload,
        components: {
          callout: { name: 'callout', interactive: false, description: '', propsSchema: {}, category: 'display' }
        }
      });

      render(ComponentRendererHost, {
        props: {
          componentRegistryStore,
          component: 'callout',
          props: {
            type: 'warning',
            title: 'Warning',
            content: 'This is important'
          }
        }
      });

      expect(await screen.findByText('Warning')).toBeInTheDocument();
    });
  });

  describe('unknown components', () => {
    it('shows error for unknown component', async () => {
      render(ComponentRendererHost, {
        props: {
          componentRegistryStore,
          component: 'nonexistent',
          props: {}
        }
      });

      expect(await screen.findByText(/Failed to render/)).toBeInTheDocument();
      expect(await screen.findByText(/Renderer not found: nonexistent/)).toBeInTheDocument();
    });
  });

  describe('interactive components', () => {
    it('marks interactive components with special styling', async () => {
      componentRegistryStore.setFromPayload({
        ...basePayload,
        components: {
          form: { name: 'form', interactive: true, description: '', propsSchema: {}, category: 'input' }
        }
      });

      const { container } = render(ComponentRendererHost, {
        props: {
          componentRegistryStore,
          component: 'form',
          props: { fields: [] }
        }
      });

      await waitFor(() => {
        expect(container.querySelector('.interactive')).toBeInTheDocument();
      });
    });

    it('calls onResult when interactive component submits', async () => {
      componentRegistryStore.setFromPayload({
        ...basePayload,
        components: {
          confirm: { name: 'confirm', interactive: true, description: '', propsSchema: {}, category: 'input' }
        }
      });

      const onResult = vi.fn();

      render(ComponentRendererHost, {
        props: {
          componentRegistryStore,
          component: 'confirm',
          props: {
            message: 'Are you sure?',
            confirmLabel: 'Yes',
            cancelLabel: 'No'
          },
          onResult
        }
      });

      await screen.findByText('Yes');
      const confirmBtn = screen.getByText('Yes');
      await confirmBtn.click();

      expect(onResult).toHaveBeenCalledWith(
        expect.objectContaining({ confirmed: true })
      );
    });
  });

  describe('data-attributes', () => {
    it('sets data-component attribute', async () => {
      componentRegistryStore.setFromPayload({
        ...basePayload,
        components: {
          markdown: { name: 'markdown', interactive: false, description: '', propsSchema: {}, category: 'display' }
        }
      });

      const { container } = render(ComponentRendererHost, {
        props: {
          componentRegistryStore,
          component: 'markdown',
          props: { content: 'test' }
        }
      });

      await waitFor(() => {
        expect(container.querySelector('[data-component="markdown"]')).toBeInTheDocument();
      });
    });
  });
});
