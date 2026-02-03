import React, { useEffect, useState, useCallback } from 'react';
import { createPortal } from 'react-dom';
import { X, Sun, Moon, Check, Palette, FolderX, Plus, Settings, FileEdit, Type } from 'lucide-react';
import { clsx } from 'clsx';
import { API_URL } from '../config';

// Font options for each category
const UI_FONTS = [
  { name: 'Inter', value: "'Inter', -apple-system, BlinkMacSystemFont, sans-serif", category: 'Sans-serif' },
  { name: 'Source Sans', value: "'Source Sans 3', -apple-system, BlinkMacSystemFont, sans-serif", category: 'Sans-serif' },
  { name: 'System Default', value: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif", category: 'Sans-serif' },
];

const CHAT_FONTS = [
  { name: 'Inter', value: "'Inter', -apple-system, BlinkMacSystemFont, sans-serif", category: 'Sans-serif' },
  { name: 'Source Sans', value: "'Source Sans 3', -apple-system, BlinkMacSystemFont, sans-serif", category: 'Sans-serif' },
  { name: 'Merriweather', value: "'Merriweather', Georgia, serif", category: 'Serif' },
  { name: 'Lora', value: "'Lora', Georgia, serif", category: 'Serif' },
  { name: 'Crimson Pro', value: "'Crimson Pro', Georgia, serif", category: 'Serif' },
];

const EDITOR_FONTS = [
  { name: 'Merriweather', value: "'Merriweather', Georgia, serif", category: 'Serif' },
  { name: 'Lora', value: "'Lora', Georgia, serif", category: 'Serif' },
  { name: 'Crimson Pro', value: "'Crimson Pro', Georgia, serif", category: 'Serif' },
  { name: 'Inter', value: "'Inter', -apple-system, BlinkMacSystemFont, sans-serif", category: 'Sans-serif' },
  { name: 'Source Sans', value: "'Source Sans 3', -apple-system, BlinkMacSystemFont, sans-serif", category: 'Sans-serif' },
];

const FONT_SIZES = [
  { name: 'Small', value: '14px', scale: 0.875 },
  { name: 'Medium', value: '16px', scale: 1 },
  { name: 'Large', value: '18px', scale: 1.125 },
  { name: 'Extra Large', value: '20px', scale: 1.25 },
];

// Accent color presets
const ACCENT_COLORS = [
  { name: 'Terracotta', value: '#D97757', hover: '#C4684A' },
  { name: 'Blue', value: '#3B82F6', hover: '#2563EB' },
  { name: 'Green', value: '#10B981', hover: '#059669' },
  { name: 'Purple', value: '#8B5CF6', hover: '#7C3AED' },
  { name: 'Pink', value: '#EC4899', hover: '#DB2777' },
  { name: 'Orange', value: '#F97316', hover: '#EA580C' },
  { name: 'Teal', value: '#14B8A6', hover: '#0D9488' },
  { name: 'Rose', value: '#F43F5E', hover: '#E11D48' },
];

export type ThemeMode = 'light' | 'dark' | 'system';
export type SettingsTab = 'appearance' | 'typography' | 'exclusions' | 'editor';

export interface ThemePreferences {
  mode: ThemeMode;
  accentColor: string;
  accentHover: string;
}

export interface TypographyPreferences {
  fontUI: string;
  fontChat: string;
  fontEditor: string;
  fontSize: string;
  fontSizeScale: number;
}

interface UIConfig {
  exclude_dirs: string[];
  exclude_files: string[];
  exclude_patterns: string[];
  default_editor_file: string;
}

const STORAGE_KEY = 'second-brain-theme';
const TYPOGRAPHY_STORAGE_KEY = 'second-brain-typography';

const DEFAULT_THEME: ThemePreferences = {
  mode: 'light',
  accentColor: '#D97757',
  accentHover: '#C4684A',
};

const DEFAULT_TYPOGRAPHY: TypographyPreferences = {
  fontUI: "'Inter', -apple-system, BlinkMacSystemFont, sans-serif",
  fontChat: "'Inter', -apple-system, BlinkMacSystemFont, sans-serif",
  fontEditor: "'Merriweather', Georgia, serif",
  fontSize: '16px',
  fontSizeScale: 1,
};

// Load theme from localStorage
export function loadThemePreferences(): ThemePreferences {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored) {
      return { ...DEFAULT_THEME, ...JSON.parse(stored) };
    }
  } catch (e) {
    console.error('Failed to load theme preferences:', e);
  }
  return DEFAULT_THEME;
}

// Save theme to localStorage
function saveThemePreferences(prefs: ThemePreferences): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(prefs));
  } catch (e) {
    console.error('Failed to save theme preferences:', e);
  }
}

// Load typography from localStorage
export function loadTypographyPreferences(): TypographyPreferences {
  try {
    const stored = localStorage.getItem(TYPOGRAPHY_STORAGE_KEY);
    if (stored) {
      return { ...DEFAULT_TYPOGRAPHY, ...JSON.parse(stored) };
    }
  } catch (e) {
    console.error('Failed to load typography preferences:', e);
  }
  return DEFAULT_TYPOGRAPHY;
}

// Save typography to localStorage
function saveTypographyPreferences(prefs: TypographyPreferences): void {
  try {
    localStorage.setItem(TYPOGRAPHY_STORAGE_KEY, JSON.stringify(prefs));
  } catch (e) {
    console.error('Failed to save typography preferences:', e);
  }
}

// Apply typography to document
export function applyTypography(prefs: TypographyPreferences): void {
  const root = document.documentElement;
  root.style.setProperty('--font-ui', prefs.fontUI);
  root.style.setProperty('--font-chat', prefs.fontChat);
  root.style.setProperty('--font-editor', prefs.fontEditor);
  root.style.setProperty('--font-size-base', prefs.fontSize);
  root.style.setProperty('--font-size-scale', String(prefs.fontSizeScale));
}

// Determine effective mode (resolves 'system' to actual light/dark)
function getEffectiveMode(mode: ThemeMode): 'light' | 'dark' {
  if (mode === 'system') {
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }
  return mode;
}

// Apply theme to document
export function applyTheme(prefs: ThemePreferences): void {
  const root = document.documentElement;
  const effectiveMode = getEffectiveMode(prefs.mode);

  // Set color mode attribute
  root.setAttribute('data-theme', effectiveMode);

  // Set accent color CSS variables
  root.style.setProperty('--accent-primary', prefs.accentColor);
  root.style.setProperty('--accent-hover', prefs.accentHover);

  // Generate a lighter variant for backgrounds
  const accentLight = prefs.accentColor + '15'; // 15 = ~9% opacity in hex
  root.style.setProperty('--accent-light', accentLight);
}

interface SettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
  onExclusionsChanged?: () => void; // Callback when exclusions are saved
  onDefaultEditorFileChanged?: (filePath: string) => void; // Callback when default editor file changes
  files?: string[]; // Available files for the file picker
}

export const SettingsModal: React.FC<SettingsModalProps> = ({ isOpen, onClose, onExclusionsChanged, onDefaultEditorFileChanged, files = [] }) => {
  const [activeTab, setActiveTab] = useState<SettingsTab>('appearance');
  const [preferences, setPreferences] = useState<ThemePreferences>(loadThemePreferences);
  const [customColor, setCustomColor] = useState(preferences.accentColor);
  const [typography, setTypography] = useState<TypographyPreferences>(loadTypographyPreferences);

  // UI Config state
  const [uiConfig, setUiConfig] = useState<UIConfig | null>(null);
  const [originalDefaultFile, setOriginalDefaultFile] = useState<string>('');
  const [uiConfigLoading, setUiConfigLoading] = useState(false);
  const [uiConfigError, setUiConfigError] = useState<string | null>(null);
  const [uiConfigDirty, setUiConfigDirty] = useState(false);

  // New item inputs
  const [newDir, setNewDir] = useState('');
  const [newFile, setNewFile] = useState('');
  const [newPattern, setNewPattern] = useState('');

  // Apply theme when preferences change
  useEffect(() => {
    applyTheme(preferences);
    saveThemePreferences(preferences);
  }, [preferences]);

  // Apply typography when preferences change
  useEffect(() => {
    applyTypography(typography);
    saveTypographyPreferences(typography);
  }, [typography]);

  // Listen for system theme changes when in 'system' mode
  useEffect(() => {
    if (preferences.mode !== 'system') return;

    const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');
    const handler = () => applyTheme(preferences);

    mediaQuery.addEventListener('change', handler);
    return () => mediaQuery.removeEventListener('change', handler);
  }, [preferences]);

  // Load UI config when switching to exclusions or editor tab
  useEffect(() => {
    if ((activeTab === 'exclusions' || activeTab === 'editor') && !uiConfig && !uiConfigLoading) {
      loadUIConfig();
    }
  }, [activeTab, uiConfig, uiConfigLoading]);

  // Handle keyboard events
  useEffect(() => {
    if (!isOpen) return;

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose();
      }
    };

    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [isOpen, onClose]);

  const loadUIConfig = useCallback(async () => {
    setUiConfigLoading(true);
    setUiConfigError(null);
    try {
      const res = await fetch(`${API_URL}/ui-config`);
      if (!res.ok) throw new Error('Failed to load config');
      const data = await res.json();
      setUiConfig(data);
      setOriginalDefaultFile(data.default_editor_file || '');
      setUiConfigDirty(false);
    } catch (e) {
      setUiConfigError('Failed to load UI configuration');
      console.error(e);
    } finally {
      setUiConfigLoading(false);
    }
  }, []);

  const saveUIConfig = useCallback(async () => {
    if (!uiConfig) return;

    setUiConfigLoading(true);
    setUiConfigError(null);
    try {
      const res = await fetch(`${API_URL}/ui-config`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(uiConfig)
      });
      if (!res.ok) throw new Error('Failed to save config');
      setUiConfigDirty(false);
      onExclusionsChanged?.();
      // Notify if default editor file changed
      if (uiConfig.default_editor_file !== originalDefaultFile) {
        onDefaultEditorFileChanged?.(uiConfig.default_editor_file);
        setOriginalDefaultFile(uiConfig.default_editor_file);
      }
    } catch (e) {
      setUiConfigError('Failed to save UI configuration');
      console.error(e);
    } finally {
      setUiConfigLoading(false);
    }
  }, [uiConfig, onExclusionsChanged, onDefaultEditorFileChanged, originalDefaultFile]);

  const setMode = (mode: ThemeMode) => {
    setPreferences(prev => ({ ...prev, mode }));
  };

  const setAccentColor = (color: string, hover: string) => {
    setPreferences(prev => ({ ...prev, accentColor: color, accentHover: hover }));
    setCustomColor(color);
  };

  const handleCustomColorChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const color = e.target.value;
    setCustomColor(color);
    // Calculate a darker hover color
    const hover = adjustBrightness(color, -15);
    setPreferences(prev => ({ ...prev, accentColor: color, accentHover: hover }));
  };

  // Simple brightness adjustment for custom colors
  const adjustBrightness = (hex: string, percent: number): string => {
    const num = parseInt(hex.replace('#', ''), 16);
    const r = Math.min(255, Math.max(0, (num >> 16) + percent));
    const g = Math.min(255, Math.max(0, ((num >> 8) & 0x00FF) + percent));
    const b = Math.min(255, Math.max(0, (num & 0x0000FF) + percent));
    return '#' + (0x1000000 + (r << 16) + (g << 8) + b).toString(16).slice(1);
  };

  const isPresetSelected = (color: string) => {
    return preferences.accentColor.toLowerCase() === color.toLowerCase();
  };

  // Exclusion list helpers
  const addExcludeDir = () => {
    if (!newDir.trim() || !uiConfig) return;
    if (uiConfig.exclude_dirs.includes(newDir.trim())) return;
    setUiConfig({ ...uiConfig, exclude_dirs: [...uiConfig.exclude_dirs, newDir.trim()] });
    setNewDir('');
    setUiConfigDirty(true);
  };

  const removeExcludeDir = (dir: string) => {
    if (!uiConfig) return;
    setUiConfig({ ...uiConfig, exclude_dirs: uiConfig.exclude_dirs.filter(d => d !== dir) });
    setUiConfigDirty(true);
  };

  const addExcludeFile = () => {
    if (!newFile.trim() || !uiConfig) return;
    if (uiConfig.exclude_files.includes(newFile.trim())) return;
    setUiConfig({ ...uiConfig, exclude_files: [...uiConfig.exclude_files, newFile.trim()] });
    setNewFile('');
    setUiConfigDirty(true);
  };

  const removeExcludeFile = (file: string) => {
    if (!uiConfig) return;
    setUiConfig({ ...uiConfig, exclude_files: uiConfig.exclude_files.filter(f => f !== file) });
    setUiConfigDirty(true);
  };

  const addExcludePattern = () => {
    if (!newPattern.trim() || !uiConfig) return;
    // Validate regex
    try {
      new RegExp(newPattern.trim());
    } catch {
      setUiConfigError('Invalid regex pattern');
      return;
    }
    if (uiConfig.exclude_patterns.includes(newPattern.trim())) return;
    setUiConfig({ ...uiConfig, exclude_patterns: [...uiConfig.exclude_patterns, newPattern.trim()] });
    setNewPattern('');
    setUiConfigDirty(true);
  };

  const removeExcludePattern = (pattern: string) => {
    if (!uiConfig) return;
    setUiConfig({ ...uiConfig, exclude_patterns: uiConfig.exclude_patterns.filter(p => p !== pattern) });
    setUiConfigDirty(true);
  };

  if (!isOpen) return null;

  return createPortal(
    <div className="fixed inset-0 z-[60] flex items-center justify-center">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/40 animate-modal-backdrop"
        onClick={onClose}
      />

      {/* Modal - full screen on mobile, centered card on desktop */}
      <div className="relative bg-[var(--bg-secondary)] shadow-xl w-full animate-modal-content border border-[var(--border-color)] flex flex-col md:rounded-xl md:max-w-lg md:mx-4 md:max-h-[85vh] max-h-[100dvh] h-full md:h-auto">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-[var(--border-color)] shrink-0">
          <div className="flex items-center gap-2">
            <Settings size={18} style={{ color: preferences.accentColor }} />
            <h3 className="text-base font-semibold text-[var(--text-primary)]">Settings</h3>
          </div>
          <button
            onClick={onClose}
            className="p-2.5 -mr-1 rounded-lg hover:bg-[var(--bg-tertiary)] active:bg-[var(--bg-tertiary)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors touch-manipulation"
          >
            <X size={20} />
          </button>
        </div>

        {/* Tabs */}
        <div className="flex border-b border-[var(--border-color)] shrink-0">
          <button
            onClick={() => setActiveTab('appearance')}
            className={clsx(
              "flex-1 py-3.5 md:py-3 px-4 text-sm font-medium transition-colors relative touch-manipulation",
              activeTab === 'appearance'
                ? "text-[var(--text-primary)]"
                : "text-[var(--text-secondary)] hover:text-[var(--text-primary)] active:text-[var(--text-primary)]"
            )}
          >
            <div className="flex items-center justify-center gap-2">
              <Palette size={16} />
              Appearance
            </div>
            {activeTab === 'appearance' && (
              <div
                className="absolute bottom-0 left-0 right-0 h-0.5"
                style={{ backgroundColor: preferences.accentColor }}
              />
            )}
          </button>
          <button
            onClick={() => setActiveTab('typography')}
            className={clsx(
              "flex-1 py-3.5 md:py-3 px-4 text-sm font-medium transition-colors relative touch-manipulation",
              activeTab === 'typography'
                ? "text-[var(--text-primary)]"
                : "text-[var(--text-secondary)] hover:text-[var(--text-primary)] active:text-[var(--text-primary)]"
            )}
          >
            <div className="flex items-center justify-center gap-2">
              <Type size={16} />
              Typography
            </div>
            {activeTab === 'typography' && (
              <div
                className="absolute bottom-0 left-0 right-0 h-0.5"
                style={{ backgroundColor: preferences.accentColor }}
              />
            )}
          </button>
          <button
            onClick={() => setActiveTab('exclusions')}
            className={clsx(
              "flex-1 py-3.5 md:py-3 px-4 text-sm font-medium transition-colors relative touch-manipulation",
              activeTab === 'exclusions'
                ? "text-[var(--text-primary)]"
                : "text-[var(--text-secondary)] hover:text-[var(--text-primary)] active:text-[var(--text-primary)]"
            )}
          >
            <div className="flex items-center justify-center gap-2">
              <FolderX size={16} />
              Exclusions
            </div>
            {activeTab === 'exclusions' && (
              <div
                className="absolute bottom-0 left-0 right-0 h-0.5"
                style={{ backgroundColor: preferences.accentColor }}
              />
            )}
          </button>
          <button
            onClick={() => setActiveTab('editor')}
            className={clsx(
              "flex-1 py-3.5 md:py-3 px-4 text-sm font-medium transition-colors relative touch-manipulation",
              activeTab === 'editor'
                ? "text-[var(--text-primary)]"
                : "text-[var(--text-secondary)] hover:text-[var(--text-primary)] active:text-[var(--text-primary)]"
            )}
          >
            <div className="flex items-center justify-center gap-2">
              <FileEdit size={16} />
              Editor
            </div>
            {activeTab === 'editor' && (
              <div
                className="absolute bottom-0 left-0 right-0 h-0.5"
                style={{ backgroundColor: preferences.accentColor }}
              />
            )}
          </button>
        </div>

        {/* Content */}
        <div className="p-5 space-y-6 overflow-y-auto flex-1">
          {activeTab === 'appearance' && (
            <>
              {/* Color Mode */}
              <div>
                <label className="block text-sm font-medium text-[var(--text-primary)] mb-3">
                  Appearance
                </label>
                <div className="flex gap-2">
                  {[
                    { mode: 'light' as ThemeMode, icon: Sun, label: 'Light' },
                    { mode: 'dark' as ThemeMode, icon: Moon, label: 'Dark' },
                    { mode: 'system' as ThemeMode, icon: null, label: 'System' },
                  ].map(({ mode, icon: Icon, label }) => (
                    <button
                      key={mode}
                      onClick={() => setMode(mode)}
                      className={clsx(
                        "flex-1 py-3 md:py-2.5 px-3 rounded-lg text-sm font-medium transition-all flex items-center justify-center gap-2 touch-manipulation active:scale-95",
                        preferences.mode === mode
                          ? "text-white shadow-md"
                          : "bg-[var(--bg-tertiary)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--border-color)]"
                      )}
                      style={preferences.mode === mode ? { backgroundColor: preferences.accentColor } : undefined}
                    >
                      {Icon && <Icon size={16} />}
                      {label}
                    </button>
                  ))}
                </div>
              </div>

              {/* Accent Color */}
              <div>
                <label className="block text-sm font-medium text-[var(--text-primary)] mb-3">
                  Accent Color
                </label>
                <div className="grid grid-cols-4 gap-2 md:gap-2 gap-3">
                  {ACCENT_COLORS.map(({ name, value, hover }) => (
                    <button
                      key={name}
                      onClick={() => setAccentColor(value, hover)}
                      className={clsx(
                        "relative h-11 md:h-10 rounded-lg transition-all hover:scale-105 active:scale-95 focus:outline-none touch-manipulation",
                        isPresetSelected(value) && "ring-2 ring-offset-2"
                      )}
                      style={{
                        backgroundColor: value,
                        // @ts-expect-error ringColor is a valid CSS property but not in React.CSSProperties
                        '--tw-ring-color': value,
                      }}
                      title={name}
                    >
                      {isPresetSelected(value) && (
                        <Check size={18} className="absolute inset-0 m-auto text-white drop-shadow-sm" />
                      )}
                    </button>
                  ))}
                </div>

                {/* Custom Color Picker */}
                <div className="mt-4 flex items-center gap-3">
                  <label className="text-sm text-[var(--text-secondary)]">Custom:</label>
                  <div className="relative flex items-center gap-2 flex-1">
                    <input
                      type="color"
                      value={customColor}
                      onChange={handleCustomColorChange}
                      className="w-10 h-10 rounded-lg border-2 border-[var(--border-color)] cursor-pointer appearance-none bg-transparent"
                      style={{ padding: 0 }}
                    />
                    <input
                      type="text"
                      value={customColor.toUpperCase()}
                      onChange={(e) => {
                        const val = e.target.value;
                        if (/^#[0-9A-Fa-f]{0,6}$/.test(val)) {
                          setCustomColor(val);
                          if (val.length === 7) {
                            const hover = adjustBrightness(val, -15);
                            setPreferences(prev => ({ ...prev, accentColor: val, accentHover: hover }));
                          }
                        }
                      }}
                      className="flex-1 px-3 py-2 text-sm font-mono border border-[var(--border-color)] rounded-lg bg-[var(--bg-primary)] text-[var(--text-primary)] focus:outline-none focus:border-[var(--accent-primary)] focus:ring-2 focus:ring-[var(--accent-primary)]/20"
                      placeholder="#D97757"
                    />
                  </div>
                </div>
              </div>

              {/* Preview */}
              <div>
                <label className="block text-sm font-medium text-[var(--text-primary)] mb-3">
                  Preview
                </label>
                <div className="p-4 rounded-xl border border-[var(--border-color)] bg-[var(--bg-primary)]">
                  <div className="flex items-center gap-3 mb-3">
                    <div
                      className="w-10 h-10 rounded-lg flex items-center justify-center text-white"
                      style={{ backgroundColor: preferences.accentColor }}
                    >
                      <Palette size={20} />
                    </div>
                    <div>
                      <div className="text-sm font-medium text-[var(--text-primary)]">Second Brain</div>
                      <div className="text-xs text-[var(--text-secondary)]">Your personal knowledge base</div>
                    </div>
                  </div>
                  <div className="flex gap-2">
                    <button
                      className="px-3 py-1.5 text-xs font-medium rounded-lg text-white transition-colors"
                      style={{ backgroundColor: preferences.accentColor }}
                    >
                      Primary Button
                    </button>
                    <button className="px-3 py-1.5 text-xs font-medium rounded-lg bg-[var(--bg-tertiary)] text-[var(--text-secondary)] border border-[var(--border-color)]">
                      Secondary
                    </button>
                  </div>
                </div>
              </div>
            </>
          )}

          {activeTab === 'typography' && (
            <>
              {/* Font Size */}
              <div>
                <label className="block text-sm font-medium text-[var(--text-primary)] mb-3">
                  Font Size
                </label>
                <div className="flex gap-2">
                  {FONT_SIZES.map(({ name, value, scale }) => (
                    <button
                      key={name}
                      onClick={() => setTypography(prev => ({ ...prev, fontSize: value, fontSizeScale: scale }))}
                      className={clsx(
                        "flex-1 py-3 md:py-2.5 px-3 rounded-lg text-sm font-medium transition-all flex items-center justify-center gap-2 touch-manipulation active:scale-95",
                        typography.fontSize === value
                          ? "text-white shadow-md"
                          : "bg-[var(--bg-tertiary)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--border-color)]"
                      )}
                      style={typography.fontSize === value ? { backgroundColor: preferences.accentColor } : undefined}
                    >
                      {name}
                    </button>
                  ))}
                </div>
              </div>

              {/* UI Font */}
              <div>
                <label className="block text-sm font-medium text-[var(--text-primary)] mb-2">
                  Interface Font
                </label>
                <p className="text-xs text-[var(--text-muted)] mb-3">
                  Used for buttons, labels, and navigation
                </p>
                <select
                  value={typography.fontUI}
                  onChange={(e) => setTypography(prev => ({ ...prev, fontUI: e.target.value }))}
                  className="w-full px-3 py-2.5 text-sm border border-[var(--border-color)] rounded-lg bg-[var(--bg-primary)] text-[var(--text-primary)] focus:outline-none focus:border-[var(--accent-primary)] focus:ring-2 focus:ring-[var(--accent-primary)]/20"
                >
                  {UI_FONTS.map(({ name, value, category }) => (
                    <option key={name} value={value}>{name} ({category})</option>
                  ))}
                </select>
              </div>

              {/* Chat Font */}
              <div>
                <label className="block text-sm font-medium text-[var(--text-primary)] mb-2">
                  Chat Font
                </label>
                <p className="text-xs text-[var(--text-muted)] mb-3">
                  Used for messages in the chat panel
                </p>
                <select
                  value={typography.fontChat}
                  onChange={(e) => setTypography(prev => ({ ...prev, fontChat: e.target.value }))}
                  className="w-full px-3 py-2.5 text-sm border border-[var(--border-color)] rounded-lg bg-[var(--bg-primary)] text-[var(--text-primary)] focus:outline-none focus:border-[var(--accent-primary)] focus:ring-2 focus:ring-[var(--accent-primary)]/20"
                >
                  {CHAT_FONTS.map(({ name, value, category }) => (
                    <option key={name} value={value}>{name} ({category})</option>
                  ))}
                </select>
              </div>

              {/* Editor Font */}
              <div>
                <label className="block text-sm font-medium text-[var(--text-primary)] mb-2">
                  Editor Font
                </label>
                <p className="text-xs text-[var(--text-muted)] mb-3">
                  Used for reading and editing documents
                </p>
                <select
                  value={typography.fontEditor}
                  onChange={(e) => setTypography(prev => ({ ...prev, fontEditor: e.target.value }))}
                  className="w-full px-3 py-2.5 text-sm border border-[var(--border-color)] rounded-lg bg-[var(--bg-primary)] text-[var(--text-primary)] focus:outline-none focus:border-[var(--accent-primary)] focus:ring-2 focus:ring-[var(--accent-primary)]/20"
                >
                  {EDITOR_FONTS.map(({ name, value, category }) => (
                    <option key={name} value={value}>{name} ({category})</option>
                  ))}
                </select>
              </div>

              {/* Live Preview */}
              <div>
                <label className="block text-sm font-medium text-[var(--text-primary)] mb-3">
                  Live Preview
                </label>
                <div className="p-4 rounded-xl border border-[var(--border-color)] bg-[var(--bg-primary)] space-y-4">
                  {/* UI Preview */}
                  <div>
                    <div className="text-xs text-[var(--text-muted)] mb-2 uppercase tracking-wide">Interface</div>
                    <div className="flex gap-2" style={{ fontFamily: typography.fontUI, fontSize: typography.fontSize }}>
                      <button
                        className="px-3 py-1.5 text-sm font-medium rounded-lg text-white transition-colors"
                        style={{ backgroundColor: preferences.accentColor }}
                      >
                        Button
                      </button>
                      <span className="px-3 py-1.5 text-sm text-[var(--text-secondary)]">Navigation Item</span>
                    </div>
                  </div>

                  {/* Chat Preview */}
                  <div>
                    <div className="text-xs text-[var(--text-muted)] mb-2 uppercase tracking-wide">Chat Message</div>
                    <div
                      className="p-3 rounded-lg bg-[var(--bg-tertiary)] text-[var(--text-primary)]"
                      style={{ fontFamily: typography.fontChat, fontSize: typography.fontSize }}
                    >
                      This is how your chat messages will look. The quick brown fox jumps over the lazy dog.
                    </div>
                  </div>

                  {/* Editor Preview */}
                  <div>
                    <div className="text-xs text-[var(--text-muted)] mb-2 uppercase tracking-wide">Editor Content</div>
                    <div
                      className="p-3 rounded-lg bg-[var(--bg-secondary)] border border-[var(--border-color)] text-[var(--text-primary)]"
                      style={{ fontFamily: typography.fontEditor, fontSize: typography.fontSize, lineHeight: 1.7 }}
                    >
                      <strong>Your documents will appear like this.</strong> This preview shows how your notes and markdown content will be displayed in the editor view.
                    </div>
                  </div>
                </div>
              </div>
            </>
          )}

          {activeTab === 'exclusions' && (
            <>
              {uiConfigLoading && !uiConfig && (
                <div className="text-center py-8 text-[var(--text-secondary)]">
                  Loading configuration...
                </div>
              )}

              {uiConfigError && (
                <div className="p-3 rounded-lg bg-red-500/10 border border-red-500/20 text-red-500 text-sm">
                  {uiConfigError}
                </div>
              )}

              {uiConfig && (
                <>
                  <p className="text-sm text-[var(--text-secondary)]">
                    Configure which files and directories are hidden from the file explorer.
                  </p>

                  {/* Excluded Directories */}
                  <div>
                    <label className="block text-sm font-medium text-[var(--text-primary)] mb-2">
                      Excluded Directories
                    </label>
                    <p className="text-xs text-[var(--text-muted)] mb-3">
                      Exact directory names to hide (matched at any level)
                    </p>
                    <div className="flex gap-2 mb-2">
                      <input
                        type="text"
                        value={newDir}
                        onChange={(e) => setNewDir(e.target.value)}
                        onKeyDown={(e) => e.key === 'Enter' && addExcludeDir()}
                        placeholder="e.g., node_modules"
                        className="flex-1 px-3 py-2 text-sm border border-[var(--border-color)] rounded-lg bg-[var(--bg-primary)] text-[var(--text-primary)] focus:outline-none focus:border-[var(--accent-primary)] focus:ring-2 focus:ring-[var(--accent-primary)]/20"
                      />
                      <button
                        onClick={addExcludeDir}
                        disabled={!newDir.trim()}
                        className="p-2 rounded-lg text-white disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                        style={{ backgroundColor: preferences.accentColor }}
                      >
                        <Plus size={18} />
                      </button>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      {uiConfig.exclude_dirs.map((dir) => (
                        <span
                          key={dir}
                          className="inline-flex items-center gap-1 px-2 py-1 text-sm bg-[var(--bg-tertiary)] text-[var(--text-primary)] rounded-lg border border-[var(--border-color)]"
                        >
                          {dir}
                          <button
                            onClick={() => removeExcludeDir(dir)}
                            className="p-0.5 rounded hover:bg-red-500/20 text-[var(--text-muted)] hover:text-red-500 transition-colors"
                          >
                            <X size={14} />
                          </button>
                        </span>
                      ))}
                    </div>
                  </div>

                  {/* Excluded Files */}
                  <div>
                    <label className="block text-sm font-medium text-[var(--text-primary)] mb-2">
                      Excluded Files
                    </label>
                    <p className="text-xs text-[var(--text-muted)] mb-3">
                      Exact file names to hide (matched at any level)
                    </p>
                    <div className="flex gap-2 mb-2">
                      <input
                        type="text"
                        value={newFile}
                        onChange={(e) => setNewFile(e.target.value)}
                        onKeyDown={(e) => e.key === 'Enter' && addExcludeFile()}
                        placeholder="e.g., .DS_Store"
                        className="flex-1 px-3 py-2 text-sm border border-[var(--border-color)] rounded-lg bg-[var(--bg-primary)] text-[var(--text-primary)] focus:outline-none focus:border-[var(--accent-primary)] focus:ring-2 focus:ring-[var(--accent-primary)]/20"
                      />
                      <button
                        onClick={addExcludeFile}
                        disabled={!newFile.trim()}
                        className="p-2 rounded-lg text-white disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                        style={{ backgroundColor: preferences.accentColor }}
                      >
                        <Plus size={18} />
                      </button>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      {uiConfig.exclude_files.map((file) => (
                        <span
                          key={file}
                          className="inline-flex items-center gap-1 px-2 py-1 text-sm bg-[var(--bg-tertiary)] text-[var(--text-primary)] rounded-lg border border-[var(--border-color)]"
                        >
                          {file}
                          <button
                            onClick={() => removeExcludeFile(file)}
                            className="p-0.5 rounded hover:bg-red-500/20 text-[var(--text-muted)] hover:text-red-500 transition-colors"
                          >
                            <X size={14} />
                          </button>
                        </span>
                      ))}
                    </div>
                  </div>

                  {/* Excluded Patterns */}
                  <div>
                    <label className="block text-sm font-medium text-[var(--text-primary)] mb-2">
                      Excluded Patterns (Regex)
                    </label>
                    <p className="text-xs text-[var(--text-muted)] mb-3">
                      Regular expressions applied to full relative paths
                    </p>
                    <div className="flex gap-2 mb-2">
                      <input
                        type="text"
                        value={newPattern}
                        onChange={(e) => setNewPattern(e.target.value)}
                        onKeyDown={(e) => e.key === 'Enter' && addExcludePattern()}
                        placeholder="e.g., .*\.pyc$"
                        className="flex-1 px-3 py-2 text-sm font-mono border border-[var(--border-color)] rounded-lg bg-[var(--bg-primary)] text-[var(--text-primary)] focus:outline-none focus:border-[var(--accent-primary)] focus:ring-2 focus:ring-[var(--accent-primary)]/20"
                      />
                      <button
                        onClick={addExcludePattern}
                        disabled={!newPattern.trim()}
                        className="p-2 rounded-lg text-white disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                        style={{ backgroundColor: preferences.accentColor }}
                      >
                        <Plus size={18} />
                      </button>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      {uiConfig.exclude_patterns.map((pattern) => (
                        <span
                          key={pattern}
                          className="inline-flex items-center gap-1 px-2 py-1 text-sm font-mono bg-[var(--bg-tertiary)] text-[var(--text-primary)] rounded-lg border border-[var(--border-color)]"
                        >
                          {pattern}
                          <button
                            onClick={() => removeExcludePattern(pattern)}
                            className="p-0.5 rounded hover:bg-red-500/20 text-[var(--text-muted)] hover:text-red-500 transition-colors"
                          >
                            <X size={14} />
                          </button>
                        </span>
                      ))}
                    </div>
                  </div>
                </>
              )}
            </>
          )}

          {activeTab === 'editor' && (
            <>
              {uiConfigLoading && !uiConfig && (
                <div className="text-center py-8 text-[var(--text-secondary)]">
                  Loading configuration...
                </div>
              )}

              {uiConfigError && (
                <div className="p-3 rounded-lg bg-red-500/10 border border-red-500/20 text-red-500 text-sm">
                  {uiConfigError}
                </div>
              )}

              {uiConfig && (
                <>
                  <p className="text-sm text-[var(--text-secondary)]">
                    Configure the default file to open when the editor loads.
                  </p>

                  {/* Default Editor File */}
                  <div>
                    <label className="block text-sm font-medium text-[var(--text-primary)] mb-2">
                      Default Editor File
                    </label>
                    <p className="text-xs text-[var(--text-muted)] mb-3">
                      This file will automatically open in the editor when you start Second Brain. Leave empty to start with a blank editor.
                    </p>
                    <select
                      value={uiConfig.default_editor_file}
                      onChange={(e) => {
                        setUiConfig({ ...uiConfig, default_editor_file: e.target.value });
                        setUiConfigDirty(true);
                      }}
                      className="w-full px-3 py-2 text-sm border border-[var(--border-color)] rounded-lg bg-[var(--bg-primary)] text-[var(--text-primary)] focus:outline-none focus:border-[var(--accent-primary)] focus:ring-2 focus:ring-[var(--accent-primary)]/20"
                    >
                      <option value="">No default file (start blank)</option>
                      {files.filter(f => f.endsWith('.md') || f.endsWith('.txt')).map((file) => (
                        <option key={file} value={file}>{file}</option>
                      ))}
                    </select>
                    {uiConfig.default_editor_file && (
                      <button
                        onClick={() => {
                          setUiConfig({ ...uiConfig, default_editor_file: '' });
                          setUiConfigDirty(true);
                        }}
                        className="mt-2 text-sm text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors"
                      >
                        Clear default file
                      </button>
                    )}
                  </div>
                </>
              )}
            </>
          )}
        </div>

        {/* Footer */}
        <div className="px-5 py-4 border-t border-[var(--border-color)] flex justify-between items-center shrink-0 pb-safe">
          {activeTab === 'appearance' ? (
            <>
              <button
                onClick={() => {
                  setPreferences(DEFAULT_THEME);
                  setCustomColor(DEFAULT_THEME.accentColor);
                }}
                className="text-sm text-[var(--text-secondary)] hover:text-[var(--text-primary)] active:text-[var(--text-primary)] transition-colors py-2 touch-manipulation"
              >
                Reset to Default
              </button>
              <button
                onClick={onClose}
                className="px-5 py-2.5 text-sm font-medium text-white rounded-lg transition-colors active:scale-95 touch-manipulation"
                style={{ backgroundColor: preferences.accentColor }}
              >
                Done
              </button>
            </>
          ) : activeTab === 'typography' ? (
            <>
              <button
                onClick={() => setTypography(DEFAULT_TYPOGRAPHY)}
                className="text-sm text-[var(--text-secondary)] hover:text-[var(--text-primary)] active:text-[var(--text-primary)] transition-colors py-2 touch-manipulation"
              >
                Reset to Default
              </button>
              <button
                onClick={onClose}
                className="px-5 py-2.5 text-sm font-medium text-white rounded-lg transition-colors active:scale-95 touch-manipulation"
                style={{ backgroundColor: preferences.accentColor }}
              >
                Done
              </button>
            </>
          ) : (
            <>
              <div className="text-sm text-[var(--text-secondary)]">
                {uiConfigDirty && <span className="text-amber-500">Unsaved changes</span>}
              </div>
              <div className="flex gap-2">
                <button
                  onClick={onClose}
                  className="px-4 py-2.5 text-sm font-medium text-[var(--text-secondary)] hover:text-[var(--text-primary)] active:text-[var(--text-primary)] transition-colors touch-manipulation"
                >
                  Cancel
                </button>
                <button
                  onClick={saveUIConfig}
                  disabled={!uiConfigDirty || uiConfigLoading}
                  className="px-5 py-2.5 text-sm font-medium text-white rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed active:scale-95 touch-manipulation"
                  style={{ backgroundColor: preferences.accentColor }}
                >
                  {uiConfigLoading ? 'Saving...' : 'Save Changes'}
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>,
    document.body
  );
};

// Hook for initializing theme and typography on app load
export function useThemeInit(): void {
  useEffect(() => {
    // Initialize theme
    const themePrefs = loadThemePreferences();
    applyTheme(themePrefs);

    // Initialize typography
    const typographyPrefs = loadTypographyPreferences();
    applyTypography(typographyPrefs);

    // Listen for system theme changes
    const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');
    const handler = () => {
      const currentPrefs = loadThemePreferences();
      if (currentPrefs.mode === 'system') {
        applyTheme(currentPrefs);
      }
    };

    mediaQuery.addEventListener('change', handler);
    return () => mediaQuery.removeEventListener('change', handler);
  }, []);
}

// Re-export the old name for backward compatibility
export const ThemeCustomizer = SettingsModal;
