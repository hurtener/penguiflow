import { describe, it, expect, beforeEach } from 'vitest';
import { createSpecStore } from '$lib/stores';
import type { SpecData, ValidationResult } from '$lib/types';

const specStore = createSpecStore();

describe('specStore', () => {
  beforeEach(() => {
    specStore.reset();
  });

  describe('initial state', () => {
    it('has empty content', () => {
      expect(specStore.content).toBe('');
    });

    it('has pending status', () => {
      expect(specStore.status).toBe('pending');
    });

    it('has no errors', () => {
      expect(specStore.errors).toEqual([]);
      expect(specStore.hasErrors).toBe(false);
    });

    it('is not valid', () => {
      expect(specStore.isValid).toBe(false);
    });
  });

  describe('setFromSpecData', () => {
    it('sets valid spec data', () => {
      const data: SpecData = {
        content: 'name: agent\nversion: 1.0',
        valid: true,
        errors: []
      };

      specStore.setFromSpecData(data);

      expect(specStore.content).toBe('name: agent\nversion: 1.0');
      expect(specStore.status).toBe('valid');
      expect(specStore.isValid).toBe(true);
      expect(specStore.hasErrors).toBe(false);
    });

    it('sets invalid spec data with errors', () => {
      const data: SpecData = {
        content: 'invalid yaml',
        valid: false,
        errors: [
          { message: 'Missing required field: name', line: 1 },
          { message: 'Invalid syntax', line: 2 }
        ]
      };

      specStore.setFromSpecData(data);

      expect(specStore.status).toBe('error');
      expect(specStore.isValid).toBe(false);
      expect(specStore.hasErrors).toBe(true);
      expect(specStore.errors).toHaveLength(2);
      const [first] = specStore.errors;
      expect(first?.message).toBe('Missing required field: name');
      expect(first?.line).toBe(1);
    });
  });

  describe('setValidationResult', () => {
    it('sets valid result', () => {
      specStore.content = 'name: test';

      const result: ValidationResult = { valid: true };
      specStore.setValidationResult(result);

      expect(specStore.status).toBe('valid');
      expect(specStore.errors).toEqual([]);
    });

    it('sets invalid result with errors', () => {
      const result: ValidationResult = {
        valid: false,
        errors: [{ message: 'Schema validation failed', line: 5 }]
      };

      specStore.setValidationResult(result);

      expect(specStore.status).toBe('error');
      expect(specStore.errors).toHaveLength(1);
      const [first] = specStore.errors;
      expect(first?.id).toBe('val-err-0');
    });
  });

  describe('setGenerationErrors', () => {
    it('sets generation errors', () => {
      specStore.setGenerationErrors([
        { message: 'Template not found' },
        { message: 'Invalid tool reference', line: 10 }
      ]);

      expect(specStore.status).toBe('error');
      expect(specStore.errors).toHaveLength(2);
      const [first, second] = specStore.errors;
      expect(first?.id).toBe('gen-err-0');
      expect(second?.line).toBe(10);
    });
  });

  describe('markValid', () => {
    it('clears errors and marks valid', () => {
      specStore.setGenerationErrors([{ message: 'Error' }]);

      specStore.markValid();

      expect(specStore.status).toBe('valid');
      expect(specStore.errors).toEqual([]);
      expect(specStore.isValid).toBe(true);
    });
  });

  describe('content setter', () => {
    it('updates content', () => {
      specStore.content = 'updated spec content';
      expect(specStore.content).toBe('updated spec content');
    });
  });

  describe('status setter', () => {
    it('updates status', () => {
      specStore.status = 'valid';
      expect(specStore.status).toBe('valid');
    });
  });

  describe('reset', () => {
    it('resets all state', () => {
      specStore.content = 'some content';
      specStore.setGenerationErrors([{ message: 'Error' }]);

      specStore.reset();

      expect(specStore.content).toBe('');
      expect(specStore.status).toBe('pending');
      expect(specStore.errors).toEqual([]);
    });
  });
});
