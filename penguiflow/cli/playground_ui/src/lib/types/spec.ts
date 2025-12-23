export type ValidationStatus = 'pending' | 'valid' | 'error';

export type SpecError = {
  id: string;
  message: string;
  line?: number | null;
};

export interface SpecData {
  content: string;
  valid: boolean;
  errors?: Array<{ message: string; line?: number | null }>;
}

export interface ValidationResult {
  valid: boolean;
  errors?: Array<{ message: string; line?: number | null }>;
}
