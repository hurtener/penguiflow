import type { SpecError, ValidationStatus, SpecData, ValidationResult } from '$lib/types';

function createSpecStore() {
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

export const specStore = createSpecStore();
