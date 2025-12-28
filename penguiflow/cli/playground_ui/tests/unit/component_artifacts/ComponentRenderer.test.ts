import { render, screen } from '@testing-library/svelte';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import ComponentRenderer from '$lib/component_artifacts/ComponentRenderer.svelte';
import { componentRegistryStore } from '$lib/stores/component_registry.svelte';

// Mock the registry store
vi.mock('$lib/stores/component_registry.svelte', () => ({
  componentRegistryStore: {
    getComponent: vi.fn(),
    enabled: true,
    components: {}
  }
}));

describe('ComponentRenderer', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('rendering', () => {
    it('renders markdown component', () => {
      vi.mocked(componentRegistryStore.getComponent).mockReturnValue({
        name: 'markdown',
        interactive: false
      });

      render(ComponentRenderer, {
        props: {
          component: 'markdown',
          props: { content: '# Hello World' }
        }
      });

      expect(screen.getByText('Hello World')).toBeInTheDocument();
    });

    it('renders json component with data', () => {
      vi.mocked(componentRegistryStore.getComponent).mockReturnValue({
        name: 'json',
        interactive: false
      });

      render(ComponentRenderer, {
        props: {
          component: 'json',
          props: { data: { key: 'value' } }
        }
      });

      expect(screen.getByText('key')).toBeInTheDocument();
    });

    it('renders metric component', () => {
      vi.mocked(componentRegistryStore.getComponent).mockReturnValue({
        name: 'metric',
        interactive: false
      });

      render(ComponentRenderer, {
        props: {
          component: 'metric',
          props: { value: 42, label: 'Users' }
        }
      });

      expect(screen.getByText('42')).toBeInTheDocument();
      expect(screen.getByText('Users')).toBeInTheDocument();
    });

    it('renders code component with syntax', () => {
      vi.mocked(componentRegistryStore.getComponent).mockReturnValue({
        name: 'code',
        interactive: false
      });

      render(ComponentRenderer, {
        props: {
          component: 'code',
          props: {
            code: 'const x = 1;',
            language: 'javascript',
            filename: 'test.js'
          }
        }
      });

      expect(screen.getByText('const x = 1;')).toBeInTheDocument();
      expect(screen.getByText('test.js')).toBeInTheDocument();
    });

    it('renders callout component', () => {
      vi.mocked(componentRegistryStore.getComponent).mockReturnValue({
        name: 'callout',
        interactive: false
      });

      render(ComponentRenderer, {
        props: {
          component: 'callout',
          props: {
            type: 'warning',
            title: 'Warning',
            content: 'This is important'
          }
        }
      });

      expect(screen.getByText('Warning')).toBeInTheDocument();
    });
  });

  describe('unknown components', () => {
    it('shows error for unknown component', () => {
      vi.mocked(componentRegistryStore.getComponent).mockReturnValue(undefined);

      render(ComponentRenderer, {
        props: {
          component: 'nonexistent',
          props: {}
        }
      });

      expect(screen.getByText(/Unknown component/)).toBeInTheDocument();
      expect(screen.getByText('nonexistent')).toBeInTheDocument();
    });
  });

  describe('interactive components', () => {
    it('marks interactive components with special styling', () => {
      vi.mocked(componentRegistryStore.getComponent).mockReturnValue({
        name: 'form',
        interactive: true
      });

      const { container } = render(ComponentRenderer, {
        props: {
          component: 'form',
          props: { fields: [] }
        }
      });

      expect(container.querySelector('.interactive')).toBeInTheDocument();
    });

    it('calls onResult when interactive component submits', async () => {
      vi.mocked(componentRegistryStore.getComponent).mockReturnValue({
        name: 'confirm',
        interactive: true
      });

      const onResult = vi.fn();

      render(ComponentRenderer, {
        props: {
          component: 'confirm',
          props: {
            message: 'Are you sure?',
            confirmLabel: 'Yes',
            cancelLabel: 'No'
          },
          onResult
        }
      });

      // Find and click confirm button
      const confirmBtn = screen.getByText('Yes');
      await confirmBtn.click();

      expect(onResult).toHaveBeenCalledWith(
        expect.objectContaining({ confirmed: true })
      );
    });
  });

  describe('data-attributes', () => {
    it('sets data-component attribute', () => {
      vi.mocked(componentRegistryStore.getComponent).mockReturnValue({
        name: 'markdown',
        interactive: false
      });

      const { container } = render(ComponentRenderer, {
        props: {
          component: 'markdown',
          props: { content: 'test' }
        }
      });

      expect(container.querySelector('[data-component="markdown"]')).toBeInTheDocument();
    });
  });
});
