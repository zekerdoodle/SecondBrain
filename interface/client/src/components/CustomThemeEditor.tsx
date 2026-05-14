import React, { useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import { X, Check, Sun, Moon, Trash2 } from 'lucide-react';
import { clsx } from 'clsx';
import ColorPicker from './ColorPicker';
import {
  THEME_PRESETS,
  applyTheme,
  type ThemePreset,
  type ThemePresetColors,
  type ThemePreferences,
} from './SettingsModal';

// Hex validator + hover-darken (mirrors logic in SettingsModal)
function isValidHex(s: string): boolean {
  return /^#[0-9a-fA-F]{6}$/.test(s);
}
function darken(hex: string, percent = -15): string {
  const num = parseInt(hex.replace('#', ''), 16);
  const r = Math.min(255, Math.max(0, (num >> 16) + percent));
  const g = Math.min(255, Math.max(0, ((num >> 8) & 0x00ff) + percent));
  const b = Math.min(255, Math.max(0, (num & 0x0000ff) + percent));
  return '#' + (0x1000000 + (r << 16) + (g << 8) + b).toString(16).slice(1);
}

function uniqueId(name: string): string {
  const slug = name
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 32) || 'custom';
  return `custom-${slug}-${Date.now().toString(36)}`;
}

// Groupings for the editor UI — keeps the 13 fields scannable
type FieldKey = keyof ThemePresetColors;
interface FieldDef {
  key: FieldKey;
  label: string;
  hint?: string;
}
interface FieldGroup {
  title: string;
  fields: FieldDef[];
}
const FIELD_GROUPS: FieldGroup[] = [
  {
    title: 'Backgrounds',
    fields: [
      { key: 'bgPrimary', label: 'Primary', hint: 'Main app background' },
      { key: 'bgSecondary', label: 'Secondary', hint: 'Panels, modals' },
      { key: 'bgTertiary', label: 'Tertiary', hint: 'Hover states, chips' },
    ],
  },
  {
    title: 'Text',
    fields: [
      { key: 'textPrimary', label: 'Primary', hint: 'Body copy' },
      { key: 'textSecondary', label: 'Secondary', hint: 'Labels' },
      { key: 'textMuted', label: 'Muted', hint: 'Hints, captions' },
    ],
  },
  {
    title: 'Borders',
    fields: [
      { key: 'borderColor', label: 'Default' },
      { key: 'borderHover', label: 'Hover' },
    ],
  },
  {
    title: 'Code & Pre',
    fields: [
      { key: 'codeBg', label: 'Inline code BG' },
      { key: 'preBg', label: 'Code block BG' },
      { key: 'preText', label: 'Code block text' },
    ],
  },
  {
    title: 'Scrollbar',
    fields: [
      { key: 'scrollbarThumb', label: 'Thumb' },
      { key: 'scrollbarThumbHover', label: 'Thumb hover' },
    ],
  },
  {
    title: 'User Bubble',
    fields: [
      { key: 'userText', label: 'Text color', hint: 'Text inside your message bubbles (bg follows accent)' },
    ],
  },
];

// Default starting point when no preset is given to clone from.
// Looked up lazily (not at module-load time) to dodge circular-import races
// with SettingsModal, which exports THEME_PRESETS.
function getFallbackColors(mode: 'light' | 'dark'): ThemePresetColors {
  const id = mode === 'light' ? 'parchment' : 'charcoal';
  const found = THEME_PRESETS.find(p => p.id === id);
  if (found) return found.colors;
  // Hard fallback if presets aren't available yet
  return mode === 'light'
    ? {
        bgPrimary: '#FAF9F7', bgSecondary: '#FFFFFF', bgTertiary: '#F5F4F2',
        textPrimary: '#1a1a1a', textSecondary: '#6B6B6B', textMuted: '#9CA3AF',
        borderColor: '#E8E6E3', borderHover: '#D4D2CF',
        codeBg: '#F3F4F6', preBg: '#1F2937', preText: '#E5E7EB',
        scrollbarThumb: '#D4D2CF', scrollbarThumbHover: '#B8B6B3',
        userText: '#FFFFFF',
      }
    : {
        bgPrimary: '#1a1a1a', bgSecondary: '#242424', bgTertiary: '#2d2d2d',
        textPrimary: '#f5f5f5', textSecondary: '#a0a0a0', textMuted: '#6b6b6b',
        borderColor: '#3d3d3d', borderHover: '#4d4d4d',
        codeBg: '#2d2d2d', preBg: '#0d0d0d', preText: '#E5E7EB',
        scrollbarThumb: '#4d4d4d', scrollbarThumbHover: '#5d5d5d',
        userText: '#FFFFFF',
      };
}

interface CustomThemeEditorProps {
  isOpen: boolean;
  onClose: () => void;
  // Current prefs (used to live-preview without saving)
  currentPrefs: ThemePreferences;
  // The theme being edited, or undefined to create a new one
  editing?: ThemePreset;
  // Called when the user saves. Receives the full theme (with id).
  // If editing, the id is preserved; if creating, a new id is generated.
  onSave: (theme: ThemePreset) => void;
  // Called when the user deletes an existing custom theme.
  onDelete?: (id: string) => void;
  // Optional clone source — used when creating to seed the form with an existing preset's colors.
  cloneFrom?: ThemePreset;
}

export const CustomThemeEditor: React.FC<CustomThemeEditorProps> = ({
  isOpen,
  onClose,
  currentPrefs,
  editing,
  onSave,
  onDelete,
  cloneFrom,
}) => {
  // Seed state from the theme being edited / cloned, or fallback presets
  const seedTheme: ThemePreset = useMemo(() => {
    if (editing) return editing;
    if (cloneFrom) {
      return {
        ...cloneFrom,
        id: '',
        name: `${cloneFrom.name} Custom`,
      };
    }
    return {
      id: '',
      name: 'My Theme',
      mode: 'light',
      colors: { ...getFallbackColors('light') },
      defaultAccent: { color: '#D97757', hover: '#C4684A' },
    };
  }, [editing, cloneFrom]);

  const [name, setName] = useState<string>(seedTheme.name);
  const [mode, setMode] = useState<'light' | 'dark'>(seedTheme.mode);
  const [colors, setColors] = useState<ThemePresetColors>({ ...seedTheme.colors });
  const [accent, setAccent] = useState<{ color: string; hover: string }>({ ...seedTheme.defaultAccent });

  // Reset state when the modal opens or the seed changes
  useEffect(() => {
    if (isOpen) {
      setName(seedTheme.name);
      setMode(seedTheme.mode);
      setColors({ ...seedTheme.colors });
      setAccent({ ...seedTheme.defaultAccent });
    }
  }, [isOpen, seedTheme]);

  // Live preview: apply the draft palette as the editor changes
  useEffect(() => {
    if (!isOpen) return;
    const previewPrefs: ThemePreferences = {
      ...currentPrefs,
      mode,
      accentColor: accent.color,
      accentHover: accent.hover,
    };
    applyTheme(previewPrefs, { previewColors: colors });
  }, [isOpen, mode, colors, accent, currentPrefs]);

  // Cancel — restore the user's actual saved theme
  const cancel = () => {
    applyTheme(currentPrefs);
    onClose();
  };

  // Save — emit the new/updated theme to the parent
  const save = () => {
    const trimmedName = name.trim() || 'Untitled';
    const themeOut: ThemePreset = {
      id: editing?.id || uniqueId(trimmedName),
      name: trimmedName,
      mode,
      colors,
      defaultAccent: { ...accent },
    };
    onSave(themeOut);
    onClose();
  };

  const handleDelete = () => {
    if (!editing || !onDelete) return;
    if (confirm(`Delete "${editing.name}"? This cannot be undone.`)) {
      applyTheme(currentPrefs);
      onDelete(editing.id);
      onClose();
    }
  };

  // Quick action: reset all palette fields to a built-in preset of the same mode
  const seedFromPreset = (preset: ThemePreset) => {
    setColors({ ...preset.colors });
    setAccent({ ...preset.defaultAccent });
    setMode(preset.mode);
  };

  // When mode flips and the user hasn't customized much, suggest swapping defaults
  const swapModeFallback = (newMode: 'light' | 'dark') => {
    setMode(newMode);
    // Only swap palette if it still matches the previous mode's fallback
    const prevFallback = getFallbackColors(newMode === 'light' ? 'dark' : 'light');
    const matchesPrevFallback = JSON.stringify(colors) === JSON.stringify(prevFallback);
    if (matchesPrevFallback) {
      setColors({ ...getFallbackColors(newMode) });
    }
  };

  if (!isOpen) return null;

  const sameModePresets = THEME_PRESETS.filter(p => p.mode === mode);

  return createPortal(
    <div className="fixed inset-0 z-[70] flex items-center justify-center">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/50 animate-modal-backdrop"
        onClick={cancel}
      />

      {/* Modal */}
      <div className="relative bg-[var(--bg-secondary)] shadow-2xl w-full animate-modal-content border border-[var(--border-color)] flex flex-col md:rounded-xl md:max-w-2xl md:mx-4 md:max-h-[90vh] max-h-[100dvh] h-full md:h-auto">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-[var(--border-color)] shrink-0">
          <div className="flex items-center gap-2 min-w-0">
            <h3 className="text-base font-semibold text-[var(--text-primary)] truncate">
              {editing ? 'Edit Custom Theme' : 'Create Custom Theme'}
            </h3>
          </div>
          <button
            onClick={cancel}
            className="p-2.5 -mr-1 rounded-lg hover:bg-[var(--bg-tertiary)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors touch-manipulation"
            aria-label="Close"
          >
            <X size={20} />
          </button>
        </div>

        {/* Content */}
        <div className="p-5 space-y-6 overflow-y-auto flex-1">
          {/* Name */}
          <div>
            <label className="block text-sm font-medium text-[var(--text-primary)] mb-2">
              Name
            </label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Forest, Sakura, Late Night"
              className="w-full px-3 py-2.5 text-sm border border-[var(--border-color)] rounded-lg bg-[var(--bg-primary)] text-[var(--text-primary)] focus:outline-none focus:border-[var(--accent-primary)] focus:ring-2 focus:ring-[var(--accent-primary)]/20"
            />
          </div>

          {/* Mode */}
          <div>
            <label className="block text-sm font-medium text-[var(--text-primary)] mb-2">
              Mode
            </label>
            <div className="flex gap-2">
              {([
                { m: 'light' as const, icon: Sun, label: 'Light' },
                { m: 'dark' as const, icon: Moon, label: 'Dark' },
              ]).map(({ m, icon: Icon, label }) => (
                <button
                  key={m}
                  onClick={() => swapModeFallback(m)}
                  className={clsx(
                    'flex-1 py-2.5 px-3 rounded-lg text-sm font-medium transition-all flex items-center justify-center gap-2 touch-manipulation active:scale-95',
                    mode === m
                      ? 'text-white shadow-md'
                      : 'bg-[var(--bg-tertiary)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--border-color)]',
                  )}
                  style={mode === m ? { backgroundColor: accent.color } : undefined}
                >
                  <Icon size={16} />
                  {label}
                </button>
              ))}
            </div>
          </div>

          {/* Start from preset (only when creating) */}
          {!editing && (
            <div>
              <label className="block text-sm font-medium text-[var(--text-primary)] mb-2">
                Start from
              </label>
              <p className="text-xs text-[var(--text-muted)] mb-2">
                Quickly seed all colors from a built-in {mode} theme — then tweak.
              </p>
              <div className="grid grid-cols-4 gap-2">
                {sameModePresets.map(p => (
                  <button
                    key={p.id}
                    onClick={() => seedFromPreset(p)}
                    className="rounded-lg overflow-hidden border border-[var(--border-color)] hover:border-[var(--border-hover)] transition-colors text-left touch-manipulation"
                    title={`Seed from ${p.name}`}
                  >
                    <div className="h-8 flex">
                      <div className="flex-1" style={{ backgroundColor: p.colors.bgPrimary }} />
                      <div className="flex-1" style={{ backgroundColor: p.colors.bgSecondary }} />
                      <div className="flex-1" style={{ backgroundColor: p.colors.bgTertiary }} />
                    </div>
                    <div className="px-2 py-1 text-[10px] text-center" style={{ backgroundColor: p.colors.bgTertiary, color: p.colors.textPrimary }}>
                      {p.name}
                    </div>
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Accent */}
          <div>
            <label className="block text-sm font-medium text-[var(--text-primary)] mb-2">
              Default Accent
            </label>
            <p className="text-xs text-[var(--text-muted)] mb-3">
              The highlight color used when this theme is selected.
            </p>
            <ColorPicker
              color={accent.color}
              onChange={(hex) => {
                if (isValidHex(hex)) {
                  setAccent({ color: hex, hover: darken(hex) });
                }
              }}
            />
          </div>

          {/* Palette field groups */}
          {FIELD_GROUPS.map(group => (
            <div key={group.title}>
              <label className="block text-sm font-medium text-[var(--text-primary)] mb-3">
                {group.title}
              </label>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                {group.fields.map(field => (
                  <PaletteField
                    key={field.key}
                    label={field.label}
                    hint={field.hint}
                    value={colors[field.key]}
                    onChange={(hex) => setColors(prev => ({ ...prev, [field.key]: hex }))}
                  />
                ))}
              </div>
            </div>
          ))}

          {/* Live preview chip */}
          <div className="rounded-lg p-4 border" style={{ backgroundColor: colors.bgPrimary, borderColor: colors.borderColor }}>
            <div className="text-xs font-semibold uppercase tracking-widest mb-2" style={{ color: colors.textMuted }}>
              Live preview
            </div>
            <div className="text-base font-semibold mb-1" style={{ color: colors.textPrimary }}>
              {name || 'Untitled'}
            </div>
            <div className="text-sm mb-3" style={{ color: colors.textSecondary }}>
              Sample secondary text — labels and helper copy.
            </div>
            <div className="flex items-center gap-2 flex-wrap">
              <button
                className="px-3 py-1.5 rounded-md text-sm font-medium text-white"
                style={{ backgroundColor: accent.color }}
              >
                Accent button
              </button>
              <span
                className="px-2 py-1 text-xs rounded font-mono"
                style={{ backgroundColor: colors.codeBg, color: colors.textPrimary }}
              >
                const x = 42;
              </span>
              <span
                className="px-2 py-1 text-xs rounded font-mono"
                style={{ backgroundColor: colors.preBg, color: colors.preText }}
              >
                pre block
              </span>
            </div>
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between gap-3 px-5 py-3 border-t border-[var(--border-color)] shrink-0">
          {editing && onDelete ? (
            <button
              onClick={handleDelete}
              className="flex items-center gap-1.5 px-3 py-2 text-sm font-medium text-red-500 hover:bg-red-500/10 rounded-lg transition-colors touch-manipulation"
            >
              <Trash2 size={14} />
              Delete
            </button>
          ) : (
            <div />
          )}
          <div className="flex items-center gap-2">
            <button
              onClick={cancel}
              className="px-4 py-2 text-sm font-medium text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors touch-manipulation"
            >
              Cancel
            </button>
            <button
              onClick={save}
              className="flex items-center gap-1.5 px-4 py-2 text-sm font-medium text-white rounded-lg transition-all active:scale-95 touch-manipulation"
              style={{ backgroundColor: accent.color }}
            >
              <Check size={14} />
              {editing ? 'Save changes' : 'Save theme'}
            </button>
          </div>
        </div>
      </div>
    </div>,
    document.body,
  );
};

// ─── Single palette color field ─────────────────────────────────────────────

interface PaletteFieldProps {
  label: string;
  hint?: string;
  value: string;
  onChange: (hex: string) => void;
}

const PaletteField: React.FC<PaletteFieldProps> = ({ label, hint, value, onChange }) => {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState(value);
  useEffect(() => {
    setText(value);
  }, [value]);

  const commitText = () => {
    const normalized = text.startsWith('#') ? text : `#${text}`;
    if (isValidHex(normalized)) {
      onChange(normalized.toUpperCase());
    } else {
      setText(value); // revert
    }
  };

  return (
    <div className="rounded-lg border border-[var(--border-color)] bg-[var(--bg-primary)] p-2.5">
      <div className="flex items-center gap-2.5">
        <button
          onClick={() => setOpen(o => !o)}
          className="w-9 h-9 rounded-md border border-[var(--border-color)] shrink-0 transition-transform hover:scale-105 active:scale-95"
          style={{ backgroundColor: value }}
          aria-label={`Pick ${label}`}
        />
        <div className="flex-1 min-w-0">
          <div className="text-xs font-medium text-[var(--text-primary)] truncate">{label}</div>
          {hint && <div className="text-[10px] text-[var(--text-muted)] truncate">{hint}</div>}
        </div>
        <input
          type="text"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onBlur={commitText}
          onKeyDown={(e) => {
            if (e.key === 'Enter') (e.target as HTMLInputElement).blur();
          }}
          className="w-[88px] px-2 py-1 text-xs font-mono border border-[var(--border-color)] rounded bg-[var(--bg-secondary)] text-[var(--text-primary)] focus:outline-none focus:border-[var(--accent-primary)] text-center"
        />
      </div>
      {open && (
        <div className="mt-3">
          <ColorPicker
            color={value}
            onChange={(hex) => {
              if (isValidHex(hex)) onChange(hex.toUpperCase());
            }}
          />
        </div>
      )}
    </div>
  );
};

export default CustomThemeEditor;
