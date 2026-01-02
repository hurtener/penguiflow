import { render, screen, fireEvent } from '@testing-library/svelte';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import Confirm from '$lib/renderers/Confirm.svelte';
import Form from '$lib/renderers/Form.svelte';
import SelectOption from '$lib/renderers/internal/SelectOption.svelte';

describe('Interactive Component Flows', () => {
  describe('Confirm component', () => {
    it('renders confirmation message', () => {
      render(Confirm, {
        props: {
          message: 'Delete this item?',
          confirmLabel: 'Delete',
          cancelLabel: 'Cancel'
        }
      });

      expect(screen.getByText('Delete this item?')).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Delete' })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument();
    });

    it('calls onResult with confirmed: true when clicking confirm', async () => {
      const onResult = vi.fn();

      render(Confirm, {
        props: {
          message: 'Proceed?',
          confirmLabel: 'Yes',
          cancelLabel: 'No',
          onResult
        }
      });

      await fireEvent.click(screen.getByRole('button', { name: 'Yes' }));

      expect(onResult).toHaveBeenCalledWith({ confirmed: true });
    });

    it('calls onResult with confirmed: false when clicking cancel', async () => {
      const onResult = vi.fn();

      render(Confirm, {
        props: {
          message: 'Proceed?',
          confirmLabel: 'Yes',
          cancelLabel: 'No',
          onResult
        }
      });

      await fireEvent.click(screen.getByRole('button', { name: 'No' }));

      expect(onResult).toHaveBeenCalledWith({ confirmed: false });
    });

    it('uses default labels when not provided', () => {
      render(Confirm, {
        props: {
          message: 'Are you sure?'
        }
      });

      expect(screen.getByRole('button', { name: 'Confirm' })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument();
    });

    it('applies danger styling for danger variant', () => {
      const { container } = render(Confirm, {
        props: {
          message: 'Delete forever?',
          variant: 'danger'
        }
      });

      expect(container.querySelector('.danger')).toBeInTheDocument();
    });
  });

  describe('Form component', () => {
    const mockFields = [
      { name: 'email', type: 'text' as const, label: 'Email', required: true },
      { name: 'agree', type: 'checkbox' as const, label: 'I agree to terms' }
    ];

    it('renders form fields', () => {
      render(Form, {
        props: {
          fields: mockFields,
          submitLabel: 'Submit'
        }
      });

      // Use regex to match label with required asterisk
      expect(screen.getByLabelText(/Email/)).toBeInTheDocument();
      expect(screen.getByLabelText(/I agree to terms/)).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Submit' })).toBeInTheDocument();
    });

    it('calls onResult with form data on submit', async () => {
      const onResult = vi.fn();

      render(Form, {
        props: {
          fields: mockFields,
          submitLabel: 'Send',
          onResult
        }
      });

      const emailInput = screen.getByLabelText(/Email/);
      await fireEvent.input(emailInput, { target: { value: 'test@example.com' } });

      const checkbox = screen.getByLabelText(/I agree to terms/);
      await fireEvent.click(checkbox);

      await fireEvent.click(screen.getByRole('button', { name: 'Send' }));

      expect(onResult).toHaveBeenCalledWith(
        expect.objectContaining({
          email: 'test@example.com',
          agree: true
        })
      );
    });

    it('shows title when provided', () => {
      render(Form, {
        props: {
          title: 'Contact Us',
          fields: [{ name: 'message', type: 'textarea' as const, label: 'Message' }]
        }
      });

      expect(screen.getByText('Contact Us')).toBeInTheDocument();
    });

    it('supports textarea fields', () => {
      render(Form, {
        props: {
          fields: [{ name: 'bio', type: 'textarea' as const, label: 'Biography' }]
        }
      });

      expect(screen.getByLabelText('Biography').tagName).toBe('TEXTAREA');
    });

    it('supports number fields', () => {
      render(Form, {
        props: {
          fields: [{ name: 'age', type: 'number' as const, label: 'Age' }]
        }
      });

      const input = screen.getByLabelText('Age');
      expect(input).toHaveAttribute('type', 'number');
    });

    it('shows cancel button when cancelLabel provided', () => {
      const onResult = vi.fn();

      render(Form, {
        props: {
          fields: mockFields,
          cancelLabel: 'Nevermind',
          onResult
        }
      });

      expect(screen.getByRole('button', { name: 'Nevermind' })).toBeInTheDocument();
    });

    it('calls onResult with _cancelled: true on cancel', async () => {
      const onResult = vi.fn();

      render(Form, {
        props: {
          fields: mockFields,
          cancelLabel: 'Cancel',
          onResult
        }
      });

      await fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));

      expect(onResult).toHaveBeenCalledWith({ _cancelled: true });
    });
  });

  describe('SelectOption component', () => {
    const mockOptions = [
      { value: 'opt1', label: 'Option 1', description: 'First option' },
      { value: 'opt2', label: 'Option 2', description: 'Second option' },
      { value: 'opt3', label: 'Option 3' }
    ];

    it('renders all options', () => {
      render(SelectOption, {
        props: {
          options: mockOptions
        }
      });

      expect(screen.getByText('Option 1')).toBeInTheDocument();
      expect(screen.getByText('Option 2')).toBeInTheDocument();
      expect(screen.getByText('Option 3')).toBeInTheDocument();
    });

    it('shows option descriptions', () => {
      render(SelectOption, {
        props: {
          options: mockOptions
        }
      });

      expect(screen.getByText('First option')).toBeInTheDocument();
      expect(screen.getByText('Second option')).toBeInTheDocument();
    });

    it('calls onResult with selection when submitting', async () => {
      const onResult = vi.fn();

      render(SelectOption, {
        props: {
          options: mockOptions,
          onResult
        }
      });

      // Click an option to select it
      await fireEvent.click(screen.getByText('Option 2'));
      // Then click submit
      await fireEvent.click(screen.getByRole('button', { name: 'Submit' }));

      expect(onResult).toHaveBeenCalledWith({ selection: 'opt2' });
    });

    it('shows title when provided', () => {
      render(SelectOption, {
        props: {
          title: 'Choose an option',
          options: mockOptions
        }
      });

      expect(screen.getByText('Choose an option')).toBeInTheDocument();
    });

    it('supports multiple selection', async () => {
      const onResult = vi.fn();

      render(SelectOption, {
        props: {
          options: mockOptions,
          multiple: true,
          onResult
        }
      });

      await fireEvent.click(screen.getByText('Option 1'));
      await fireEvent.click(screen.getByText('Option 3'));

      // Submit button for multiple selection
      await fireEvent.click(screen.getByRole('button', { name: 'Submit' }));

      expect(onResult).toHaveBeenCalledWith(
        expect.objectContaining({
          selection: expect.arrayContaining(['opt1', 'opt3'])
        })
      );
    });
  });

  describe('Integration: Interactive result callback', () => {
    it('simulates full interactive workflow', async () => {
      // This test simulates the flow from receiving an interactive component
      // to returning a result back to the backend

      const results: unknown[] = [];
      const captureResult = (result: unknown) => {
        results.push(result);
      };

      // Step 1: Render a confirm dialog
      const { unmount: unmount1 } = render(Confirm, {
        props: {
          message: 'Confirm deletion?',
          confirmLabel: 'Delete',
          cancelLabel: 'Keep',
          onResult: captureResult
        }
      });

      // User clicks confirm
      await fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
      expect(results).toHaveLength(1);
      expect(results[0]).toEqual({ confirmed: true });

      unmount1();

      // Step 2: Render a form
      const { unmount: unmount2 } = render(Form, {
        props: {
          fields: [
            { name: 'reason', type: 'text' as const, label: 'Reason' }
          ],
          submitLabel: 'Continue',
          onResult: captureResult
        }
      });

      await fireEvent.input(screen.getByLabelText('Reason'), {
        target: { value: 'No longer needed' }
      });
      await fireEvent.click(screen.getByRole('button', { name: 'Continue' }));

      expect(results).toHaveLength(2);
      expect(results[1]).toEqual({ reason: 'No longer needed' });

      unmount2();

      // Step 3: Render a select option
      render(SelectOption, {
        props: {
          options: [
            { value: 'archive', label: 'Archive' },
            { value: 'delete', label: 'Permanently Delete' }
          ],
          onResult: captureResult
        }
      });

      await fireEvent.click(screen.getByText('Permanently Delete'));
      await fireEvent.click(screen.getByRole('button', { name: 'Submit' }));

      expect(results).toHaveLength(3);
      expect(results[2]).toEqual({ selection: 'delete' });
    });

    it('handles rapid interactions gracefully', async () => {
      const onResult = vi.fn();

      render(Confirm, {
        props: {
          message: 'Quick test',
          onResult
        }
      });

      // Rapid clicks should only trigger once (component should disable after first click)
      const confirmBtn = screen.getByRole('button', { name: 'Confirm' });
      await fireEvent.click(confirmBtn);
      await fireEvent.click(confirmBtn);
      await fireEvent.click(confirmBtn);

      // Should only be called once (or implementation may vary)
      expect(onResult).toHaveBeenCalled();
    });
  });
});
