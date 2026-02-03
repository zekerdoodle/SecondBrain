import React, { useState, useCallback } from 'react';
import { ChevronDown, Check, ClipboardList } from 'lucide-react';
import type { FormMessageData, FormField } from '../types';

export interface InlineFormProps {
  formData: FormMessageData;
  onSubmit: (formId: string, values: Record<string, any>) => void;
}

export const InlineForm: React.FC<InlineFormProps> = ({ formData, onSubmit }) => {
  const { formId, title, description, fields, prefill = {}, status, submittedValues } = formData;

  // Initialize form values from prefill or defaults
  const getInitialValues = useCallback(() => {
    const values: Record<string, any> = {};
    fields.forEach((field) => {
      if (prefill[field.id] !== undefined) {
        values[field.id] = prefill[field.id];
      } else if (field.defaultValue !== undefined) {
        values[field.id] = field.defaultValue;
      } else {
        switch (field.type) {
          case 'checkbox':
            values[field.id] = false;
            break;
          case 'number':
            values[field.id] = '';
            break;
          default:
            values[field.id] = '';
        }
      }
    });
    return values;
  }, [fields, prefill]);

  const [values, setValues] = useState<Record<string, any>>(getInitialValues);
  const [errors, setErrors] = useState<Record<string, string>>({});

  const updateValue = (fieldId: string, value: any) => {
    setValues((prev) => ({ ...prev, [fieldId]: value }));
    if (errors[fieldId]) {
      setErrors((prev) => {
        const next = { ...prev };
        delete next[fieldId];
        return next;
      });
    }
  };

  const validate = (): boolean => {
    const newErrors: Record<string, string> = {};
    fields.forEach((field) => {
      if (field.required) {
        const value = values[field.id];
        if (value === undefined || value === null || value === '') {
          newErrors[field.id] = `${field.label} is required`;
        }
      }
    });
    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!validate()) return;
    onSubmit(formId, values);
  };

  // Render a single field based on its type
  const renderField = (field: FormField) => {
    const hasError = !!errors[field.id];
    const baseInputClass = `w-full px-3 py-2 text-sm border rounded-md outline-none transition-colors bg-[var(--bg-primary)] text-[var(--text-primary)] ${
      hasError
        ? 'border-red-400 focus:border-red-500 focus:ring-2 focus:ring-red-500/20'
        : 'border-[var(--border-color)] focus:border-[var(--accent-primary)] focus:ring-2 focus:ring-[var(--accent-primary)]/20'
    }`;

    switch (field.type) {
      case 'text':
        return (
          <input
            type="text"
            id={field.id}
            value={values[field.id] || ''}
            onChange={(e) => updateValue(field.id, e.target.value)}
            placeholder={field.placeholder}
            className={baseInputClass}
          />
        );

      case 'textarea':
        return (
          <textarea
            id={field.id}
            value={values[field.id] || ''}
            onChange={(e) => updateValue(field.id, e.target.value)}
            placeholder={field.placeholder}
            rows={3}
            className={`${baseInputClass} resize-y min-h-[80px]`}
          />
        );

      case 'select':
        return (
          <div className="relative">
            <select
              id={field.id}
              value={values[field.id] || ''}
              onChange={(e) => updateValue(field.id, e.target.value)}
              className={`${baseInputClass} appearance-none pr-8`}
            >
              <option value="">Select...</option>
              {field.options?.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
            <ChevronDown
              size={14}
              className="absolute right-3 top-1/2 -translate-y-1/2 pointer-events-none text-[var(--text-muted)]"
            />
          </div>
        );

      case 'checkbox':
        return (
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              id={field.id}
              checked={values[field.id] || false}
              onChange={(e) => updateValue(field.id, e.target.checked)}
              className="w-4 h-4 rounded border-[var(--border-color)] text-[var(--accent-primary)] focus:ring-[var(--accent-primary)]/20"
            />
            <span className="text-sm text-[var(--text-secondary)]">
              {field.placeholder || field.label}
            </span>
          </label>
        );

      case 'number':
        return (
          <input
            type="number"
            id={field.id}
            value={values[field.id] ?? ''}
            onChange={(e) => updateValue(field.id, e.target.value ? Number(e.target.value) : '')}
            placeholder={field.placeholder}
            className={baseInputClass}
          />
        );

      case 'date':
        return (
          <input
            type="date"
            id={field.id}
            value={values[field.id] || ''}
            onChange={(e) => updateValue(field.id, e.target.value)}
            className={baseInputClass}
          />
        );

      default:
        return null;
    }
  };

  // Submitted state - show summary
  if (status === 'submitted' && submittedValues) {
    return (
      <div className="w-full bg-[var(--bg-secondary)] border border-[var(--border-color)] rounded-2xl rounded-bl-md shadow-warm overflow-hidden">
        <div className="flex items-center gap-2 px-4 py-3 bg-emerald-50 dark:bg-emerald-900/20 border-b border-[var(--border-color)]">
          <div className="w-5 h-5 rounded-full bg-emerald-500 flex items-center justify-center">
            <Check size={12} className="text-white" />
          </div>
          <span className="text-sm font-medium text-emerald-700 dark:text-emerald-400">
            {title} - Submitted
          </span>
        </div>
        <div className="p-4 space-y-2">
          {fields.map((field) => (
            <div key={field.id} className="text-sm">
              <span className="text-[var(--text-muted)]">{field.label}:</span>{' '}
              <span className="text-[var(--text-primary)]">
                {field.type === 'checkbox'
                  ? submittedValues[field.id] ? 'Yes' : 'No'
                  : submittedValues[field.id] || '-'}
              </span>
            </div>
          ))}
        </div>
      </div>
    );
  }

  // Pending state - show editable form
  return (
    <div className="w-full bg-[var(--bg-secondary)] border border-[var(--accent-primary)]/30 rounded-2xl rounded-bl-md shadow-warm overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-2 px-4 py-3 bg-[var(--accent-light)] border-b border-[var(--border-color)]">
        <ClipboardList size={18} style={{ color: 'var(--accent-primary)' }} />
        <div>
          <h3 className="text-sm font-semibold text-[var(--text-primary)]">{title}</h3>
          {description && (
            <p className="text-xs text-[var(--text-muted)] mt-0.5">{description}</p>
          )}
        </div>
      </div>

      {/* Form */}
      <form onSubmit={handleSubmit} className="p-4">
        <div className="space-y-4">
          {fields.map((field) => (
            <div key={field.id}>
              {field.type !== 'checkbox' && (
                <label
                  htmlFor={field.id}
                  className="block text-sm font-medium text-[var(--text-secondary)] mb-1.5"
                >
                  {field.label}
                  {field.required && <span className="text-red-400 ml-1">*</span>}
                </label>
              )}
              {renderField(field)}
              {errors[field.id] && (
                <p className="mt-1.5 text-xs text-red-500">{errors[field.id]}</p>
              )}
            </div>
          ))}
        </div>

        {/* Submit button */}
        <div className="mt-4 pt-4 border-t border-[var(--border-color)]">
          <button
            type="submit"
            className="w-full px-4 py-2.5 text-sm bg-[var(--accent-primary)] text-white rounded-lg hover:bg-[var(--accent-hover)] transition-colors font-medium"
          >
            Submit
          </button>
        </div>
      </form>
    </div>
  );
};
