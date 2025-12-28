<script lang="ts">
  import { createEventDispatcher } from 'svelte';

  interface FormField {
    name: string;
    type: string;
    label?: string;
    placeholder?: string;
    required?: boolean;
    disabled?: boolean;
    default?: unknown;
    options?: Array<string | { value: string; label: string }>;
    validation?: Record<string, unknown>;
    helpText?: string;
    width?: 'full' | 'half' | 'third';
  }

  interface Props {
    title?: string;
    description?: string;
    fields?: FormField[];
    submitLabel?: string;
    cancelLabel?: string;
    layout?: 'vertical' | 'horizontal' | 'inline';
    onResult?: (result: Record<string, unknown>) => void;
  }

  let {
    title = '',
    description = '',
    fields = [],
    submitLabel = 'Submit',
    cancelLabel = undefined,
    layout = 'vertical',
    onResult = undefined
  }: Props = $props();

  const dispatch = createEventDispatcher<{ submit: Record<string, unknown>; cancel: void }>();

  let values = $state<Record<string, unknown>>({});
  let errors = $state<Record<string, string>>({});
  let touched = $state<Record<string, boolean>>({});

  $effect(() => {
    for (const field of fields) {
      if (values[field.name] === undefined && field.default !== undefined) {
        values[field.name] = field.default;
      }
    }
  });

  function validate(): boolean {
    errors = {};
    let valid = true;

    for (const field of fields) {
      const value = values[field.name];
      const isEmptyArray = Array.isArray(value) && value.length === 0;

      if (field.required && (value === undefined || value === '' || value === null || isEmptyArray)) {
        errors[field.name] = `${field.label || field.name} is required`;
        valid = false;
        continue;
      }

      const v = field.validation as Record<string, unknown> | undefined;
      if (v && value !== undefined && value !== '') {
        if (v.min !== undefined && typeof value === 'number' && value < Number(v.min)) {
          errors[field.name] = `Minimum value is ${v.min}`;
          valid = false;
        }
        if (v.max !== undefined && typeof value === 'number' && value > Number(v.max)) {
          errors[field.name] = `Maximum value is ${v.max}`;
          valid = false;
        }
        if (v.minLength !== undefined && typeof value === 'string' && value.length < Number(v.minLength)) {
          errors[field.name] = `Minimum length is ${v.minLength}`;
          valid = false;
        }
        if (v.pattern && typeof value === 'string' && !new RegExp(String(v.pattern)).test(value)) {
          errors[field.name] = (v.message as string) || 'Invalid format';
          valid = false;
        }
      }
    }

    return valid;
  }

  function handleSubmit() {
    for (const field of fields) {
      touched[field.name] = true;
    }

    if (!validate()) return;

    const result = { ...values };
    dispatch('submit', result);
    onResult?.(result);
  }

  function handleCancel() {
    dispatch('cancel');
    onResult?.({ _cancelled: true });
  }

  function normalizeOptions(options: typeof fields[number]['options']) {
    return options?.map((o) => (typeof o === 'string' ? { value: o, label: o } : o)) ?? [];
  }

  function handleFileChange(fieldName: string, files: FileList | null) {
    const payload = files
      ? Array.from(files).map((file) => ({
        name: file.name,
        size: file.size,
        type: file.type,
      }))
      : [];
    values[fieldName] = payload;
  }
</script>

<form
  class="artifact-form"
  class:horizontal={layout === 'horizontal'}
  class:inline={layout === 'inline'}
  onsubmit={(e: SubmitEvent) => { e.preventDefault(); handleSubmit(); }}
>
  {#if title}
    <h3 class="form-title">{title}</h3>
  {/if}

  {#if description}
    <p class="form-description">{description}</p>
  {/if}

  <div class="form-fields">
    {#each fields as field}
      {@const fieldId = `field-${field.name}`}
      {@const hasError = touched[field.name] && errors[field.name]}

      <div
        class="form-field"
        class:half={field.width === 'half'}
        class:third={field.width === 'third'}
        class:error={hasError}
      >
        {#if field.type !== 'checkbox' && field.type !== 'switch'}
          <label for={fieldId}>
            {field.label || field.name}
            {#if field.required}<span class="required">*</span>{/if}
          </label>
        {/if}

        {#if ['text', 'email', 'password', 'url', 'tel'].includes(field.type)}
          <input
            type={field.type}
            id={fieldId}
            bind:value={values[field.name]}
            onblur={() => touched[field.name] = true}
            placeholder={field.placeholder}
            required={field.required}
            disabled={field.disabled}
          />

        {:else if field.type === 'number'}
          <input
            type="number"
            id={fieldId}
            bind:value={values[field.name]}
            onblur={() => touched[field.name] = true}
            placeholder={field.placeholder}
            required={field.required}
            disabled={field.disabled}
            min={field.validation?.min}
            max={field.validation?.max}
          />

        {:else if field.type === 'textarea'}
          <textarea
            id={fieldId}
            bind:value={values[field.name]}
            onblur={() => touched[field.name] = true}
            placeholder={field.placeholder}
            required={field.required}
            disabled={field.disabled}
            rows="3"
          ></textarea>

        {:else if field.type === 'select'}
          <select
            id={fieldId}
            bind:value={values[field.name]}
            onblur={() => touched[field.name] = true}
            required={field.required}
            disabled={field.disabled}
          >
            <option value="">Select...</option>
            {#each normalizeOptions(field.options) as opt}
              <option value={opt.value}>{opt.label}</option>
            {/each}
          </select>

        {:else if field.type === 'multiselect'}
          <select
            id={fieldId}
            bind:value={values[field.name]}
            onblur={() => touched[field.name] = true}
            multiple
            disabled={field.disabled}
          >
            {#each normalizeOptions(field.options) as opt}
              <option value={opt.value}>{opt.label}</option>
            {/each}
          </select>

        {:else if field.type === 'checkbox' || field.type === 'switch'}
          <label class="checkbox-label">
            <input
              type="checkbox"
              id={fieldId}
              bind:checked={values[field.name]}
              disabled={field.disabled}
            />
            <span>{field.label || field.name}</span>
          </label>

        {:else if field.type === 'radio'}
          <div class="radio-group">
            {#each normalizeOptions(field.options) as opt}
              <label class="radio-label">
                <input
                  type="radio"
                  name={field.name}
                  value={opt.value}
                  bind:group={values[field.name]}
                  disabled={field.disabled}
                />
                <span>{opt.label}</span>
              </label>
            {/each}
          </div>

        {:else if field.type === 'date'}
          <input
            type="date"
            id={fieldId}
            bind:value={values[field.name]}
            onblur={() => touched[field.name] = true}
            required={field.required}
            disabled={field.disabled}
          />

        {:else if field.type === 'datetime'}
          <input
            type="datetime-local"
            id={fieldId}
            bind:value={values[field.name]}
            onblur={() => touched[field.name] = true}
            required={field.required}
            disabled={field.disabled}
          />

        {:else if field.type === 'time'}
          <input
            type="time"
            id={fieldId}
            bind:value={values[field.name]}
            onblur={() => touched[field.name] = true}
            required={field.required}
            disabled={field.disabled}
          />

        {:else if field.type === 'range'}
          <input
            type="range"
            id={fieldId}
            bind:value={values[field.name]}
            min={field.validation?.min ?? 0}
            max={field.validation?.max ?? 100}
            disabled={field.disabled}
          />
          <span class="range-value">{values[field.name]}</span>

        {:else if field.type === 'color'}
          <input
            type="color"
            id={fieldId}
            bind:value={values[field.name]}
            disabled={field.disabled}
          />

        {:else if field.type === 'file'}
          <input
            type="file"
            id={fieldId}
            onchange={(event: Event & { currentTarget: HTMLInputElement }) => handleFileChange(field.name, event.currentTarget.files)}
            required={field.required}
            disabled={field.disabled}
          />
          {#if Array.isArray(values[field.name]) && values[field.name].length > 0}
            <div class="file-list">
              {#each values[field.name] as file}
                <span>{file.name} ({Math.round(file.size / 1024)} KB)</span>
              {/each}
            </div>
          {/if}
        {/if}

        {#if field.helpText && !hasError}
          <span class="help-text">{field.helpText}</span>
        {/if}

        {#if hasError}
          <span class="error-text">{errors[field.name]}</span>
        {/if}
      </div>
    {/each}
  </div>

  <div class="form-actions">
    {#if cancelLabel}
      <button type="button" class="btn-cancel" onclick={handleCancel}>
        {cancelLabel}
      </button>
    {/if}
    <button type="submit" class="btn-submit">
      {submitLabel}
    </button>
  </div>
</form>

<style>
  .artifact-form {
    padding: 1.5rem;
  }

  .form-title {
    margin: 0 0 0.5rem;
    font-size: 1.25rem;
  }

  .form-description {
    margin: 0 0 1.5rem;
    color: #6b7280;
  }

  .form-fields {
    display: grid;
    grid-template-columns: repeat(6, 1fr);
    gap: 1rem;
  }

  .form-field {
    grid-column: span 6;
    display: flex;
    flex-direction: column;
    gap: 0.375rem;
  }

  .form-field.half {
    grid-column: span 3;
  }

  .form-field.third {
    grid-column: span 2;
  }

  label {
    font-weight: 500;
    font-size: 0.875rem;
  }

  .required {
    color: #ef4444;
  }

  input, select, textarea {
    padding: 0.5rem 0.75rem;
    border: 1px solid #d1d5db;
    border-radius: 0.375rem;
    font-size: 0.875rem;
  }

  input:focus, select:focus, textarea:focus {
    outline: none;
    border-color: #3b82f6;
    box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.1);
  }

  .form-field.error input,
  .form-field.error select,
  .form-field.error textarea {
    border-color: #ef4444;
  }

  .help-text {
    font-size: 0.75rem;
    color: #6b7280;
  }

  .error-text {
    font-size: 0.75rem;
    color: #ef4444;
  }

  .checkbox-label, .radio-label {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    font-weight: normal;
    cursor: pointer;
  }

  .file-list {
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
    font-size: 0.75rem;
    color: #64748b;
  }

  .radio-group {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
  }

  .form-actions {
    display: flex;
    justify-content: flex-end;
    gap: 0.75rem;
    margin-top: 1.5rem;
    padding-top: 1.5rem;
    border-top: 1px solid #e5e7eb;
  }

  button {
    padding: 0.5rem 1rem;
    border-radius: 0.375rem;
    font-weight: 500;
    cursor: pointer;
  }

  .btn-submit {
    background: #3b82f6;
    color: white;
    border: none;
  }

  .btn-submit:hover {
    background: #2563eb;
  }

  .btn-cancel {
    background: white;
    color: #374151;
    border: 1px solid #d1d5db;
  }

  .btn-cancel:hover {
    background: #f9fafb;
  }
</style>
