import React, { useEffect, useRef, useState, useCallback } from 'react';
import { createPortal } from 'react-dom';
import { X, ChevronDown } from 'lucide-react';

// Field definition from the backend
export interface FormField {
  id: string;
  type: 'text' | 'textarea' | 'select' | 'checkbox' | 'number' | 'date';
  label: string;
  required?: boolean;
  placeholder?: string;
  options?: Array<{ label: string; value: string }>;
  defaultValue?: any;
}

export interface FormModalProps {
  isOpen: boolean;
  formId: string;
  title: string;
  description?: string;
  fields: FormField[];
  prefill?: Record<string, any>;
  onSubmit: (formId: string, values: Record<string, any>) => void;
  onCancel: () => void;
}

export const FormModal: React.FC<FormModalProps> = ({
  isOpen,
  formId,
  title,
  description,
  fields,
  prefill = {},
  onSubmit,
  onCancel,
}) => {
  // Initialize form values from prefill or defaults
  const getInitialValues = useCallback(() => {
    const values: Record<string, any> = {};
    fields.forEach((field) => {
      if (prefill[field.id] !== undefined) {
        values[field.id] = prefill[field.id];
      } else if (field.defaultValue !== undefined) {
        values[field.id] = field.defaultValue;
      } else {
        // Default values by type
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
  const firstInputRef = useRef<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>(null);

  // Reset values when modal opens
  useEffect(() => {
    if (isOpen) {
      setValues(getInitialValues());
      setErrors({});
    }
  }, [isOpen, getInitialValues]);

  // Auto-focus first input when modal opens
  useEffect(() => {
    if (isOpen && firstInputRef.current) {
      setTimeout(() => firstInputRef.current?.focus(), 100);
    }
  }, [isOpen]);

  // Handle Escape key
  useEffect(() => {
    if (!isOpen) return;

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onCancel();
      }
    };

    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [isOpen, onCancel]);

  const updateValue = (fieldId: string, value: any) => {
    setValues((prev) => ({ ...prev, [fieldId]: value }));
    // Clear error when user starts typing
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

    if (!validate()) {
      return;
    }

    onSubmit(formId, values);
  };

  // Render a single field based on its type
  const renderField = (field: FormField, index: number) => {
    const hasError = !!errors[field.id];
    const baseInputClass = `w-full px-3 py-2 text-sm border rounded-md outline-none transition-colors bg-[var(--bg-primary)] text-[var(--text-primary)] ${
      hasError
        ? 'border-red-400 focus:border-red-500 focus:ring-2 focus:ring-red-500/20'
        : 'border-[var(--border-color)] focus:border-[var(--accent-primary)] focus:ring-2 focus:ring-[var(--accent-primary)]/20'
    }`;

    const refProp = index === 0 ? { ref: firstInputRef as any } : {};

    switch (field.type) {
      case 'text':
        return (
          <input
            {...refProp}
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
            {...refProp}
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
              {...refProp}
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
              {...refProp}
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
            {...refProp}
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
            {...refProp}
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

  if (!isOpen) return null;

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/40 animate-modal-backdrop"
        onClick={onCancel}
      />

      {/* Modal */}
      <div className="relative bg-[var(--bg-secondary)] rounded-lg shadow-xl w-full max-w-lg mx-4 animate-modal-content border border-[var(--border-color)] max-h-[90vh] flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-[var(--border-color)] shrink-0">
          <div>
            <h3 className="text-base font-semibold text-[var(--text-primary)]">{title}</h3>
            {description && (
              <p className="text-sm text-[var(--text-muted)] mt-1">{description}</p>
            )}
          </div>
          <button
            onClick={onCancel}
            className="p-1.5 rounded-md hover:bg-[var(--bg-tertiary)] text-[var(--text-muted)] hover:text-[var(--text-secondary)] transition-colors"
          >
            <X size={18} />
          </button>
        </div>

        {/* Content - scrollable if many fields */}
        <form onSubmit={handleSubmit} className="flex flex-col flex-1 overflow-hidden">
          <div className="p-5 space-y-4 overflow-y-auto flex-1">
            {fields.map((field, index) => (
              <div key={field.id}>
                {/* Label - skip for checkbox as it's inline */}
                {field.type !== 'checkbox' && (
                  <label
                    htmlFor={field.id}
                    className="block text-sm font-medium text-[var(--text-secondary)] mb-1.5"
                  >
                    {field.label}
                    {field.required && (
                      <span className="text-red-400 ml-1">*</span>
                    )}
                  </label>
                )}

                {/* Field */}
                {renderField(field, index)}

                {/* Error */}
                {errors[field.id] && (
                  <p className="mt-1.5 text-xs text-red-500">{errors[field.id]}</p>
                )}
              </div>
            ))}
          </div>

          {/* Actions - fixed at bottom */}
          <div className="flex justify-end gap-2 px-5 py-4 border-t border-[var(--border-color)] shrink-0 bg-[var(--bg-secondary)]">
            <button
              type="button"
              onClick={onCancel}
              className="px-4 py-2 text-sm text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)] rounded-md transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              className="px-4 py-2 text-sm bg-[var(--accent-primary)] text-white rounded-md hover:bg-[var(--accent-hover)] transition-colors font-medium"
            >
              Submit
            </button>
          </div>
        </form>
      </div>
    </div>,
    document.body
  );
};
