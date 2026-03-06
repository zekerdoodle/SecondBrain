import React, { useRef, useState, useCallback, useEffect } from 'react';

// ─── Color conversion helpers ───────────────────────────────────────────────

function hexToHsv(hex: string): [number, number, number] {
  const r = parseInt(hex.slice(1, 3), 16) / 255;
  const g = parseInt(hex.slice(3, 5), 16) / 255;
  const b = parseInt(hex.slice(5, 7), 16) / 255;
  const max = Math.max(r, g, b), min = Math.min(r, g, b);
  const d = max - min;
  let h = 0;
  const s = max === 0 ? 0 : d / max;
  const v = max;
  if (d !== 0) {
    switch (max) {
      case r: h = ((g - b) / d + (g < b ? 6 : 0)) / 6; break;
      case g: h = ((b - r) / d + 2) / 6; break;
      case b: h = ((r - g) / d + 4) / 6; break;
    }
  }
  return [h * 360, s * 100, v * 100];
}

function hsvToHex(h: number, s: number, v: number): string {
  h = h / 360;
  s = s / 100;
  v = v / 100;
  let r = 0, g = 0, b = 0;
  const i = Math.floor(h * 6);
  const f = h * 6 - i;
  const p = v * (1 - s);
  const q = v * (1 - f * s);
  const t = v * (1 - (1 - f) * s);
  switch (i % 6) {
    case 0: r = v; g = t; b = p; break;
    case 1: r = q; g = v; b = p; break;
    case 2: r = p; g = v; b = t; break;
    case 3: r = p; g = q; b = v; break;
    case 4: r = t; g = p; b = v; break;
    case 5: r = v; g = p; b = q; break;
  }
  const toHex = (n: number) => Math.round(n * 255).toString(16).padStart(2, '0');
  return `#${toHex(r)}${toHex(g)}${toHex(b)}`;
}

function hsvToRgb(h: number, s: number, v: number): [number, number, number] {
  const hex = hsvToHex(h, s, v);
  return [
    parseInt(hex.slice(1, 3), 16),
    parseInt(hex.slice(3, 5), 16),
    parseInt(hex.slice(5, 7), 16),
  ];
}

function hexToRgb(hex: string): [number, number, number] {
  return [
    parseInt(hex.slice(1, 3), 16),
    parseInt(hex.slice(3, 5), 16),
    parseInt(hex.slice(5, 7), 16),
  ];
}

function rgbToHex(r: number, g: number, b: number): string {
  const toHex = (n: number) => Math.max(0, Math.min(255, Math.round(n))).toString(16).padStart(2, '0');
  return `#${toHex(r)}${toHex(g)}${toHex(b)}`;
}

// ─── Types ──────────────────────────────────────────────────────────────────

interface ColorPickerProps {
  color: string;  // hex like "#D97757"
  onChange: (hex: string) => void;
}

// ─── Component ──────────────────────────────────────────────────────────────

export default function ColorPicker({ color, onChange }: ColorPickerProps) {
  // Parse the incoming hex into HSV so we can drive the picker
  const [hsv, setHsv] = useState<[number, number, number]>(() => hexToHsv(color));
  const [hexInput, setHexInput] = useState(color.toUpperCase());
  const [rgbInputs, setRgbInputs] = useState<[string, string, string]>(() => {
    const [r, g, b] = hexToRgb(color);
    return [String(r), String(g), String(b)];
  });
  const [inputMode, setInputMode] = useState<'hex' | 'rgb'>('hex');

  // Refs for drag areas
  const svPanelRef = useRef<HTMLDivElement>(null);
  const hueBarRef = useRef<HTMLDivElement>(null);
  const isDraggingSV = useRef(false);
  const isDraggingHue = useRef(false);

  // Sync external color prop → internal state (only when prop changes externally)
  const lastEmitted = useRef(color);
  useEffect(() => {
    if (color.toLowerCase() !== lastEmitted.current.toLowerCase()) {
      const newHsv = hexToHsv(color);
      setHsv(newHsv);
      setHexInput(color.toUpperCase());
      const [r, g, b] = hexToRgb(color);
      setRgbInputs([String(r), String(g), String(b)]);
      lastEmitted.current = color;
    }
  }, [color]);

  // Emit color upstream
  const emitColor = useCallback((h: number, s: number, v: number) => {
    const hex = hsvToHex(h, s, v);
    lastEmitted.current = hex;
    setHexInput(hex.toUpperCase());
    const [r, g, b] = hsvToRgb(h, s, v);
    setRgbInputs([String(r), String(g), String(b)]);
    onChange(hex);
  }, [onChange]);

  // ─── Saturation/Value panel interaction ─────────────────────────────────

  const handleSVInteraction = useCallback((clientX: number, clientY: number) => {
    const panel = svPanelRef.current;
    if (!panel) return;
    const rect = panel.getBoundingClientRect();
    const x = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
    const y = Math.max(0, Math.min(1, (clientY - rect.top) / rect.height));
    const newS = x * 100;
    const newV = (1 - y) * 100;
    const newHsv: [number, number, number] = [hsv[0], newS, newV];
    setHsv(newHsv);
    emitColor(newHsv[0], newHsv[1], newHsv[2]);
  }, [hsv, emitColor]);

  const onSVPointerDown = useCallback((e: React.PointerEvent) => {
    e.preventDefault();
    isDraggingSV.current = true;
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
    handleSVInteraction(e.clientX, e.clientY);
  }, [handleSVInteraction]);

  const onSVPointerMove = useCallback((e: React.PointerEvent) => {
    if (!isDraggingSV.current) return;
    handleSVInteraction(e.clientX, e.clientY);
  }, [handleSVInteraction]);

  const onSVPointerUp = useCallback(() => {
    isDraggingSV.current = false;
  }, []);

  // ─── Hue bar interaction ────────────────────────────────────────────────

  const handleHueInteraction = useCallback((clientX: number) => {
    const bar = hueBarRef.current;
    if (!bar) return;
    const rect = bar.getBoundingClientRect();
    const x = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
    const newH = x * 360;
    const newHsv: [number, number, number] = [newH, hsv[1], hsv[2]];
    setHsv(newHsv);
    emitColor(newHsv[0], newHsv[1], newHsv[2]);
  }, [hsv, emitColor]);

  const onHuePointerDown = useCallback((e: React.PointerEvent) => {
    e.preventDefault();
    isDraggingHue.current = true;
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
    handleHueInteraction(e.clientX);
  }, [handleHueInteraction]);

  const onHuePointerMove = useCallback((e: React.PointerEvent) => {
    if (!isDraggingHue.current) return;
    handleHueInteraction(e.clientX);
  }, [handleHueInteraction]);

  const onHuePointerUp = useCallback(() => {
    isDraggingHue.current = false;
  }, []);

  // ─── Text input handlers ────────────────────────────────────────────────

  const handleHexInputChange = (val: string) => {
    // Allow typing — only apply when it's a valid 7-char hex
    setHexInput(val.toUpperCase());
    if (/^#[0-9A-Fa-f]{6}$/.test(val)) {
      const newHsv = hexToHsv(val);
      setHsv(newHsv);
      const [r, g, b] = hexToRgb(val);
      setRgbInputs([String(r), String(g), String(b)]);
      lastEmitted.current = val;
      onChange(val);
    }
  };

  const handleRgbInputChange = (index: number, val: string) => {
    const newRgb = [...rgbInputs] as [string, string, string];
    newRgb[index] = val;
    setRgbInputs(newRgb);

    // Parse and apply if all valid
    const nums = newRgb.map(v => parseInt(v, 10));
    if (nums.every(n => !isNaN(n) && n >= 0 && n <= 255)) {
      const hex = rgbToHex(nums[0], nums[1], nums[2]);
      const newHsv = hexToHsv(hex);
      setHsv(newHsv);
      setHexInput(hex.toUpperCase());
      lastEmitted.current = hex;
      onChange(hex);
    }
  };

  // ─── Derived values ─────────────────────────────────────────────────────

  const hueColor = hsvToHex(hsv[0], 100, 100);
  const currentHex = hsvToHex(hsv[0], hsv[1], hsv[2]);
  const svThumbX = hsv[1]; // 0..100%
  const svThumbY = 100 - hsv[2]; // 0..100% (inverted: top = bright)
  const hueThumbX = (hsv[0] / 360) * 100; // 0..100%

  // Decide thumb border color for contrast against current color
  const thumbBorderColor = hsv[2] > 60 && hsv[1] < 40 ? 'rgba(0,0,0,0.5)' : 'white';

  return (
    <div className="w-full space-y-3 select-none" style={{ touchAction: 'none' }}>
      {/* ── Saturation/Value gradient panel ──────────────────────────────── */}
      <div
        ref={svPanelRef}
        className="relative w-full rounded-lg cursor-crosshair overflow-hidden"
        style={{
          height: 160,
          background: `
            linear-gradient(to top, #000, transparent),
            linear-gradient(to right, #fff, ${hueColor})
          `,
        }}
        onPointerDown={onSVPointerDown}
        onPointerMove={onSVPointerMove}
        onPointerUp={onSVPointerUp}
      >
        {/* Thumb */}
        <div
          className="absolute pointer-events-none"
          style={{
            left: `${svThumbX}%`,
            top: `${svThumbY}%`,
            width: 18,
            height: 18,
            transform: 'translate(-50%, -50%)',
            borderRadius: '50%',
            border: `2.5px solid ${thumbBorderColor}`,
            boxShadow: '0 1px 4px rgba(0,0,0,0.4)',
            backgroundColor: currentHex,
          }}
        />
      </div>

      {/* ── Hue slider ───────────────────────────────────────────────────── */}
      <div
        ref={hueBarRef}
        className="relative w-full rounded-full cursor-pointer overflow-hidden"
        style={{
          height: 16,
          background: 'linear-gradient(to right, #f00 0%, #ff0 17%, #0f0 33%, #0ff 50%, #00f 67%, #f0f 83%, #f00 100%)',
        }}
        onPointerDown={onHuePointerDown}
        onPointerMove={onHuePointerMove}
        onPointerUp={onHuePointerUp}
      >
        <div
          className="absolute pointer-events-none"
          style={{
            left: `${hueThumbX}%`,
            top: '50%',
            width: 20,
            height: 20,
            transform: 'translate(-50%, -50%)',
            borderRadius: '50%',
            border: '2.5px solid white',
            boxShadow: '0 1px 4px rgba(0,0,0,0.4)',
            backgroundColor: hueColor,
          }}
        />
      </div>

      {/* ── Inputs row ───────────────────────────────────────────────────── */}
      <div className="flex items-center gap-2">
        {/* Color swatch */}
        <div
          className="w-9 h-9 rounded-lg shrink-0 border border-[var(--border-color)]"
          style={{ backgroundColor: currentHex }}
        />

        {/* Mode toggle */}
        <button
          onClick={() => setInputMode(inputMode === 'hex' ? 'rgb' : 'hex')}
          className="px-2 py-1.5 text-[11px] font-semibold tracking-wide uppercase rounded-md bg-[var(--bg-tertiary)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors shrink-0 touch-manipulation"
        >
          {inputMode === 'hex' ? 'HEX' : 'RGB'}
        </button>

        {inputMode === 'hex' ? (
          <input
            type="text"
            value={hexInput}
            onChange={(e) => {
              const val = e.target.value;
              if (/^#?[0-9A-Fa-f]{0,6}$/.test(val.startsWith('#') ? val : '#' + val)) {
                handleHexInputChange(val.startsWith('#') ? val : '#' + val);
              }
            }}
            className="flex-1 min-w-0 px-2.5 py-1.5 text-sm font-mono border border-[var(--border-color)] rounded-lg bg-[var(--bg-primary)] text-[var(--text-primary)] focus:outline-none focus:border-[var(--accent-primary)] focus:ring-2 focus:ring-[var(--accent-primary)]/20 text-center"
            maxLength={7}
            spellCheck={false}
          />
        ) : (
          <div className="flex-1 flex gap-1.5">
            {(['R', 'G', 'B'] as const).map((label, i) => (
              <div key={label} className="flex-1 min-w-0 relative">
                <input
                  type="text"
                  inputMode="numeric"
                  value={rgbInputs[i]}
                  onChange={(e) => {
                    const val = e.target.value;
                    if (/^\d{0,3}$/.test(val)) {
                      handleRgbInputChange(i, val);
                    }
                  }}
                  className="w-full px-1.5 py-1.5 text-sm font-mono border border-[var(--border-color)] rounded-lg bg-[var(--bg-primary)] text-[var(--text-primary)] focus:outline-none focus:border-[var(--accent-primary)] focus:ring-2 focus:ring-[var(--accent-primary)]/20 text-center"
                  maxLength={3}
                  spellCheck={false}
                />
                <span className="absolute -bottom-4 left-1/2 -translate-x-1/2 text-[10px] text-[var(--text-muted)] font-medium">
                  {label}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Spacer for RGB labels */}
      {inputMode === 'rgb' && <div className="h-1" />}
    </div>
  );
}
