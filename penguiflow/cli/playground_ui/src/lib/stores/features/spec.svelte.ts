import { getContext, setContext } from 'svelte';
import type { SpecError, ValidationStatus, SpecData, ValidationResult } from '$lib/types';

const SPEC_STORE_KEY = Symbol('spec-store');

export interface SpecStore {
  content: string;
  status: ValidationStatus;
  readonly errors: SpecError[];
  readonly isValid: boolean;
  readonly hasErrors: boolean;
  setFromSpecData(data: SpecData): void;
  setValidationResult(result: ValidationResult): void;
  setGenerationErrors(errs: Array<{ message: string; line?: number | null }>): void;
  markValid(): void;
  reset(): void;
}

export function createSpecStore(): SpecStore {
  let content = $state('');
  let status = $state<ValidationStatus>('pending');
  let errors = $state<SpecError[]>([]);

  return {
    get content() { return content; },
    set content(v: string) { content = v; },

    get status() { return status; },
    set status(v: ValidationStatus) { status = v; },
    get errors() { return errors; },

    get isValid() { return status === 'valid'; },
    get hasErrors() { return errors.length > 0; },

    setFromSpecData(data: SpecData) {
      content = data.content;
      status = data.valid ? 'valid' : 'error';
      errors = (data.errors ?? []).map((err, idx) => ({
        id: `err-${idx}`,
        message: err.message,
        line: err.line
      }));
    },

    setValidationResult(result: ValidationResult) {
      status = result.valid ? 'valid' : 'error';
      errors = (result.errors ?? []).map((err, idx) => ({
        id: `val-err-${idx}`,
        message: err.message,
        line: err.line
      }));
    },

    setGenerationErrors(errs: Array<{ message: string; line?: number | null }>) {
      status = 'error';
      errors = errs.map((err, idx) => ({
        id: `gen-err-${idx}`,
        message: err.message,
        line: err.line
      }));
    },

    markValid() {
      status = 'valid';
      errors = [];
    },

    reset() {
      content = '';
      status = 'pending';
      errors = [];
    }
  };
}

export function setSpecStore(store: SpecStore = createSpecStore()): SpecStore {
  setContext(SPEC_STORE_KEY, store);
  return store;
}

export function getSpecStore(): SpecStore {
  return getContext<SpecStore>(SPEC_STORE_KEY);
}
