import tsParser from '@typescript-eslint/parser';
import tsPlugin from '@typescript-eslint/eslint-plugin';
import svelteParser from 'svelte-eslint-parser';

const baseRules = {
  '@typescript-eslint/no-explicit-any': 'error',
  '@typescript-eslint/explicit-function-return-type': 'warn',
  'max-lines-per-function': ['warn', 50],
  'max-depth': ['warn', 3],
  'complexity': ['warn', 10],
};

export default [
  {
    ignores: [
      'dist/**',
      'node_modules/**',
      'playwright-report/**',
      'test-results/**'
    ]
  },
  {
    files: ['src/**/*.{ts,js}', 'tests/**/*.{ts,js}'],
    languageOptions: {
      parser: tsParser,
      parserOptions: {
        project: './tsconfig.json',
        sourceType: 'module'
      }
    },
    plugins: { '@typescript-eslint': tsPlugin },
    rules: baseRules
  },
  {
    files: ['src/**/*.svelte', 'tests/**/*.svelte'],
    languageOptions: {
      parser: svelteParser,
      parserOptions: {
        parser: tsParser,
        project: './tsconfig.json',
        extraFileExtensions: ['.svelte']
      }
    },
    plugins: { '@typescript-eslint': tsPlugin },
    rules: baseRules
  }
];
