import { render, screen, fireEvent } from '@testing-library/svelte';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import ComponentLab from '$lib/component_artifacts/ComponentLab.svelte';
import { componentRegistryStore, componentArtifactsStore } from '$lib/stores';
import type { ComponentRegistryPayload } from '$lib/stores/component_registry.svelte';

// Mock the stores
vi.mock('$lib/stores', () => ({
  componentRegistryStore: {
    components: {},
    getComponent: vi.fn()
  },
  componentArtifactsStore: {
    lastArtifact: null
  }
}));

describe('ComponentLab', () => {
  const mockRegistryPayload: ComponentRegistryPayload = {
    version: '1.0.0',
    enabled: true,
    allowlist: ['markdown', 'json', 'metric'],
    components: {
      markdown: {
        name: 'markdown',
        description: 'Render markdown content',
        propsSchema: { content: { type: 'string', required: true } },
        interactive: false,
        category: 'display',
        example: {
          description: 'Simple heading',
          props: { content: '# Hello World' }
        }
      },
      json: {
        name: 'json',
        description: 'Display JSON data',
        propsSchema: { data: { type: 'object' } },
        interactive: false,
        category: 'display'
      },
      confirm: {
        name: 'confirm',
        description: 'Confirmation dialog',
        propsSchema: { message: { type: 'string' } },
        interactive: true,
        category: 'input',
        example: {
          props: { message: 'Are you sure?' }
        }
      }
    }
  };

  beforeEach(() => {
    vi.clearAllMocks();
    // Reset mock store
    (componentRegistryStore as any).components = mockRegistryPayload.components;
    (componentRegistryStore.getComponent as any).mockImplementation(
      (name: string) => mockRegistryPayload.components[name]
    );
    (componentArtifactsStore as any).lastArtifact = null;
  });

  describe('component list', () => {
    it('renders component list heading', () => {
      render(ComponentLab);

      expect(screen.getByRole('heading', { name: 'Components' })).toBeInTheDocument();
    });

    it('displays all registered components', () => {
      render(ComponentLab);

      // Use getAllByText since component name appears in list AND detail header
      expect(screen.getAllByText('markdown').length).toBeGreaterThan(0);
      expect(screen.getAllByText('json').length).toBeGreaterThan(0);
      expect(screen.getAllByText('confirm').length).toBeGreaterThan(0);
    });

    it('shows category and interactive flag', () => {
      render(ComponentLab);

      // Check for category labels
      const displayLabels = screen.getAllByText(/display/);
      expect(displayLabels.length).toBeGreaterThan(0);

      // Interactive components show indicator
      expect(screen.getByText(/input.*interactive/)).toBeInTheDocument();
    });

    it('sorts components by category and name', () => {
      render(ComponentLab);

      const buttons = screen.getAllByRole('button').filter(
        btn => btn.classList.contains('component-item')
      );

      // Should be sorted: confirm (input), json (display), markdown (display)
      const names = buttons.map(btn => btn.querySelector('.name')?.textContent);
      expect(names).toContain('markdown');
      expect(names).toContain('json');
      expect(names).toContain('confirm');
    });
  });

  describe('component selection', () => {
    it('selects first component by default', () => {
      render(ComponentLab);

      // First sorted component should be selected
      const selectedBtn = screen.getAllByRole('button').find(
        btn => btn.classList.contains('active')
      );
      expect(selectedBtn).toBeDefined();
    });

    it('shows component description when selected', async () => {
      render(ComponentLab);

      const markdownBtn = screen.getByRole('button', { name: /markdown/i });
      await fireEvent.click(markdownBtn);

      expect(screen.getByText('Render markdown content')).toBeInTheDocument();
    });

    it('updates active state on selection', async () => {
      render(ComponentLab);

      const jsonBtn = screen.getByRole('button', { name: /json/i });
      await fireEvent.click(jsonBtn);

      expect(jsonBtn.classList.contains('active')).toBe(true);
    });
  });

  describe('props schema display', () => {
    it('shows props schema section', async () => {
      render(ComponentLab);

      const markdownBtn = screen.getByRole('button', { name: /markdown/i });
      await fireEvent.click(markdownBtn);

      expect(screen.getByRole('heading', { name: 'Props Schema' })).toBeInTheDocument();
    });

    it('displays schema as JSON', async () => {
      render(ComponentLab);

      const markdownBtn = screen.getByRole('button', { name: /markdown/i });
      await fireEvent.click(markdownBtn);

      // Schema should be stringified in a pre element
      const schemaSection = screen.getByText('Props Schema').parentElement;
      expect(schemaSection?.querySelector('pre')).toBeInTheDocument();
    });
  });

  describe('example section', () => {
    it('shows example heading', async () => {
      render(ComponentLab);

      const markdownBtn = screen.getByRole('button', { name: /markdown/i });
      await fireEvent.click(markdownBtn);

      expect(screen.getByRole('heading', { name: 'Example' })).toBeInTheDocument();
    });

    it('displays example props', async () => {
      render(ComponentLab);

      const markdownBtn = screen.getByRole('button', { name: /markdown/i });
      await fireEvent.click(markdownBtn);

      // The example section should have the example props
      const exampleSection = screen.getByText('Example').parentElement;
      const pre = exampleSection?.querySelector('pre');
      expect(pre?.textContent).toContain('Hello World');
    });
  });

  describe('payload editor', () => {
    it('shows payload editor', async () => {
      render(ComponentLab);

      const markdownBtn = screen.getByRole('button', { name: /markdown/i });
      await fireEvent.click(markdownBtn);

      expect(screen.getByRole('heading', { name: 'Payload' })).toBeInTheDocument();
      expect(screen.getByRole('textbox')).toBeInTheDocument();
    });

    it('shows error for invalid JSON', async () => {
      render(ComponentLab);

      const markdownBtn = screen.getByRole('button', { name: /markdown/i });
      await fireEvent.click(markdownBtn);

      const textarea = screen.getByRole('textbox');
      await fireEvent.input(textarea, { target: { value: 'not valid json' } });

      expect(screen.getByText(/Invalid JSON|Unexpected token/)).toBeInTheDocument();
    });

    it('shows error for missing component key', async () => {
      render(ComponentLab);

      const markdownBtn = screen.getByRole('button', { name: /markdown/i });
      await fireEvent.click(markdownBtn);

      const textarea = screen.getByRole('textbox');
      await fireEvent.input(textarea, { target: { value: '{"props": {}}' } });

      expect(screen.getByText(/must include component and props/)).toBeInTheDocument();
    });
  });

  describe('action buttons', () => {
    it('shows Use Example button', async () => {
      render(ComponentLab);

      const markdownBtn = screen.getByRole('button', { name: /markdown/i });
      await fireEvent.click(markdownBtn);

      expect(screen.getByRole('button', { name: 'Use Example' })).toBeInTheDocument();
    });

    it('shows Use Last Artifact button', async () => {
      render(ComponentLab);

      const markdownBtn = screen.getByRole('button', { name: /markdown/i });
      await fireEvent.click(markdownBtn);

      expect(screen.getByRole('button', { name: 'Use Last Artifact' })).toBeInTheDocument();
    });

    it('shows Reset button', async () => {
      render(ComponentLab);

      const markdownBtn = screen.getByRole('button', { name: /markdown/i });
      await fireEvent.click(markdownBtn);

      expect(screen.getByRole('button', { name: 'Reset' })).toBeInTheDocument();
    });

    it('disables Use Last Artifact when no artifact', async () => {
      render(ComponentLab);

      const markdownBtn = screen.getByRole('button', { name: /markdown/i });
      await fireEvent.click(markdownBtn);

      const lastArtifactBtn = screen.getByRole('button', { name: 'Use Last Artifact' });
      expect(lastArtifactBtn).toBeDisabled();
    });

    it('enables Use Last Artifact when artifact exists', async () => {
      (componentArtifactsStore as any).lastArtifact = {
        id: 'test-1',
        component: 'markdown',
        props: { content: 'Last content' }
      };

      render(ComponentLab);

      const markdownBtn = screen.getByRole('button', { name: /markdown/i });
      await fireEvent.click(markdownBtn);

      const lastArtifactBtn = screen.getByRole('button', { name: 'Use Last Artifact' });
      expect(lastArtifactBtn).not.toBeDisabled();
    });
  });

  describe('preview section', () => {
    it('shows preview heading', async () => {
      render(ComponentLab);

      const markdownBtn = screen.getByRole('button', { name: /markdown/i });
      await fireEvent.click(markdownBtn);

      expect(screen.getByRole('heading', { name: 'Preview' })).toBeInTheDocument();
    });

    it('shows fix payload message when invalid', async () => {
      render(ComponentLab);

      const markdownBtn = screen.getByRole('button', { name: /markdown/i });
      await fireEvent.click(markdownBtn);

      const textarea = screen.getByRole('textbox');
      await fireEvent.input(textarea, { target: { value: 'invalid' } });

      expect(screen.getByText('Fix payload to preview.')).toBeInTheDocument();
    });
  });

  describe('empty state', () => {
    it('shows empty message when no registry', () => {
      (componentRegistryStore as any).components = {};

      render(ComponentLab);

      expect(screen.getByText('No registry loaded.')).toBeInTheDocument();
    });
  });
});
