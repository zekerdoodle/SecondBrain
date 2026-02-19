import React from 'react';

/**
 * Agent SVG Icon Library
 *
 * Replaces the old Lucide-based icon set with a comprehensive ~100-icon SVG library.
 * All icons use 24×24 viewBox, monoline stroke style.
 *
 * The returned component matches Lucide's interface: { size?, className?, style? }
 */

interface IconProps {
  size?: number;
  className?: string;
  style?: React.CSSProperties;
}

type IconComponent = React.FC<IconProps>;

/** Helper: create a React component from raw SVG path content */
function icon(paths: string): IconComponent {
  const Component: IconComponent = ({ size = 24, className, style }) =>
    React.createElement('svg', {
      viewBox: '0 0 24 24',
      width: size,
      height: size,
      fill: 'none',
      stroke: 'currentColor',
      strokeWidth: 2,
      strokeLinecap: 'round',
      strokeLinejoin: 'round',
      className,
      style,
      dangerouslySetInnerHTML: { __html: paths },
    });
  return Component;
}

// ─── AI / Computing ───────────────────────────────────────
const brain = icon('<path d="M12 2a5 5 0 0 1 4.9 4 4.5 4.5 0 0 1 2.1 4 4 4 0 0 1-1 7.5V20a2 2 0 0 1-2 2h-8a2 2 0 0 1-2-2v-2.5A4 4 0 0 1 3 10a4.5 4.5 0 0 1 2.1-4A5 5 0 0 1 12 2z"/><path d="M12 2v20"/><path d="M8 8h0"/><path d="M16 8h0"/>');
const cpu = icon('<rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6" rx="1"/><path d="M9 1v3"/><path d="M15 1v3"/><path d="M9 20v3"/><path d="M15 20v3"/><path d="M20 9h3"/><path d="M20 15h3"/><path d="M1 9h3"/><path d="M1 15h3"/>');
const circuit = icon('<rect x="2" y="10" width="4" height="4" rx="1"/><rect x="18" y="3" width="4" height="4" rx="1"/><rect x="18" y="17" width="4" height="4" rx="1"/><path d="M6 12h6"/><path d="M12 5V19"/><path d="M12 5h6"/><path d="M12 19h6"/><circle cx="12" cy="12" r="1.5"/>');
const neural = icon('<circle cx="5" cy="6" r="2"/><circle cx="5" cy="18" r="2"/><circle cx="12" cy="9" r="2"/><circle cx="12" cy="15" r="2"/><circle cx="19" cy="12" r="2"/><path d="M7 6.5l3 1.8"/><path d="M7 17.5l3-1.8"/><path d="M14 9.8l3 1.4"/><path d="M14 14.2l3-1.4"/><path d="M7 7l3 6.5"/><path d="M7 17l3-6.5"/>');
const bot = icon('<rect x="3" y="7" width="18" height="13" rx="3"/><circle cx="9" cy="13" r="1.5"/><circle cx="15" cy="13" r="1.5"/><path d="M8 17h8"/><path d="M12 2v5"/><circle cx="12" cy="2" r="1"/>');
const chip = icon('<path d="M7 4h10a3 3 0 0 1 3 3v10a3 3 0 0 1-3 3H7a3 3 0 0 1-3-3V7a3 3 0 0 1 3-3z"/><path d="M10 9l2 2 4-4"/><path d="M8 1v3"/><path d="M16 1v3"/><path d="M8 20v3"/><path d="M16 20v3"/>');
const binary = icon('<path d="M7 4v4"/><path d="M5 4h4"/><path d="M5 8h4"/><circle cx="16" cy="6" r="2.5"/><circle cx="7" cy="18" r="2.5"/><path d="M15 14v4"/><path d="M13 14h4"/><path d="M13 18h4"/>');
const algorithm = icon('<rect x="8" y="1" width="8" height="5" rx="1"/><rect x="1" y="18" width="8" height="5" rx="1"/><rect x="15" y="18" width="8" height="5" rx="1"/><path d="M12 6v4"/><path d="M5 14v4"/><path d="M19 14v4"/><path d="M5 14h14"/><path d="M12 10v4"/>');

// ─── Communication / Research ─────────────────────────────
const search = icon('<circle cx="11" cy="11" r="8"/><path d="M21 21l-4.3-4.3"/>');
const globe = icon('<circle cx="12" cy="12" r="10"/><path d="M2 12h20"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>');
const satellite = icon('<path d="M13 7L9 3 3 9l4 4"/><path d="M17 11l4 4-6 6-4-4"/><path d="M8 12l4 4"/><circle cx="18" cy="6" r="3"/><path d="M4.5 16.5l3 3"/>');
const radar = icon('<circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/><path d="M12 2v4"/><path d="M12 12l7-7"/>');
const antenna = icon('<path d="M12 10V22"/><path d="M6 22h12"/><circle cx="12" cy="5" r="3"/><path d="M3 3l3 3"/><path d="M21 3l-3 3"/>');
const broadcast = icon('<circle cx="12" cy="12" r="2"/><path d="M16.24 7.76a6 6 0 0 1 0 8.49"/><path d="M7.76 16.24a6 6 0 0 1 0-8.49"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14"/><path d="M4.93 19.07a10 10 0 0 1 0-14.14"/>');
const megaphone = icon('<path d="M18 3v18"/><path d="M18 8a6 6 0 0 0-6-6H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h6a6 6 0 0 0 6-6z"/><path d="M6 15v4a2 2 0 0 0 2 2h2a2 2 0 0 0 2-2v-1"/>');

// ─── Tools / Engineering ──────────────────────────────────
const wrench = icon('<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>');
const gear = icon('<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>');
const hammer = icon('<path d="M15 12l-8.5 8.5a2.12 2.12 0 1 1-3-3L12 9"/><path d="M17.64 4.22a2.83 2.83 0 0 1 4 0l-.17.17a2.83 2.83 0 0 1 0 4l-3.17 3.17-4-4 3.17-3.17z"/>');
const terminal = icon('<rect x="2" y="4" width="20" height="16" rx="2"/><path d="M6 10l4 4-4 4"/><path d="M14 18h4"/>');
const code = icon('<polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/>');
const pipeline = icon('<rect x="2" y="4" width="6" height="6" rx="1"/><rect x="16" y="14" width="6" height="6" rx="1"/><path d="M8 7h4a2 2 0 0 1 2 2v6a2 2 0 0 0 2 2h0"/><circle cx="12" cy="12" r="1.5"/>');
const toolkit = icon('<rect x="2" y="7" width="20" height="14" rx="2"/><path d="M16 7V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v2"/><path d="M12 12v4"/><path d="M2 12h20"/>');

// ─── Science / Knowledge ──────────────────────────────────
const flask = icon('<path d="M9 3h6"/><path d="M10 3v7.4a2 2 0 0 1-.5 1.3L4 19a2 2 0 0 0 1.5 3h13a2 2 0 0 0 1.5-3l-5.5-7.3A2 2 0 0 1 14 10.4V3"/>');
const atom = icon('<circle cx="12" cy="12" r="2"/><ellipse cx="12" cy="12" rx="10" ry="4"/><ellipse cx="12" cy="12" rx="10" ry="4" transform="rotate(60 12 12)"/><ellipse cx="12" cy="12" rx="10" ry="4" transform="rotate(120 12 12)"/>');
const microscope = icon('<path d="M6 18h8"/><path d="M3 22h18"/><path d="M14 22a7 7 0 1 0 0-14h-1"/><path d="M9 14h2"/><path d="M9 12a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v4a2 2 0 0 1-2 2z"/><path d="M12 6l2-2"/>');
const dna = icon('<path d="M2 15c6 0 6-6 12-6s6 6 12 6"/><path d="M2 9c6 0 6 6 12 6s6-6 12-6"/><path d="M7 3v4"/><path d="M17 17v4"/><path d="M12 10v4"/>');
const telescope = icon('<path d="M21 4l-4 14-6-6z"/><path d="M11 12L2 21"/><path d="M18 4l2-1"/><path d="M4 18l3 3"/>');
const book = icon('<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/><path d="M8 7h8"/><path d="M8 11h6"/>');
const scroll = icon('<path d="M8 21h12a2 2 0 0 0 2-2v-2H10v2a2 2 0 1 1-4 0V5a2 2 0 1 0-4 0v2h12"/><path d="M19 17V5a2 2 0 0 0-2-2H4"/>');
const library = icon('<path d="M3 21h18"/><path d="M5 21V7l7-4 7 4v14"/><path d="M9 21v-6h6v6"/><path d="M9 9h.01"/><path d="M15 9h.01"/><path d="M9 13h.01"/><path d="M15 13h.01"/>');
const lightbulb = icon('<path d="M9 18h6"/><path d="M10 22h4"/><path d="M12 2a7 7 0 0 0-4 12.7V17h8v-2.3A7 7 0 0 0 12 2z"/>');

// ─── Nature / Elements ────────────────────────────────────
const sparkles = icon('<path d="M12 3l1.5 5.5L19 10l-5.5 1.5L12 17l-1.5-5.5L5 10l5.5-1.5z"/><path d="M19 15l.75 2.25L22 18l-2.25.75L19 21l-.75-2.25L16 18l2.25-.75z"/>');
const flame = icon('<path d="M12 22c4-3 8-6 8-12a8 8 0 0 0-16 0c0 6 4 9 8 12z"/><path d="M12 22c-2-1.5-4-3-4-7a4 4 0 0 1 8 0c0 4-2 5.5-4 7z"/>');
const lightning = icon('<path d="M13 2L3 14h8l-1 8 10-12h-8l1-8z"/>');
const crystal = icon('<path d="M6 3h12l4 8-10 11L2 11z"/><path d="M2 11h20"/><path d="M12 22V11"/><path d="M6 3l6 8"/><path d="M18 3l-6 8"/>');
const leaf = icon('<path d="M11 20A7 7 0 0 1 4 13c0-5 7-11 8-11s8 6 8 11a7 7 0 0 1-7 7z"/><path d="M12 9v11"/><path d="M8 13c2-1 3-2 4-4"/>');
const tree = icon('<path d="M12 22v-6"/><path d="M12 3l-7 9h4l-3 4h12l-3-4h4z"/>');
const mountain = icon('<path d="M8 3l-6 18h20L16 9l-4 6z"/><path d="M4.1 16.4L8 12l3 3"/>');
const wave = icon('<path d="M2 12c2-3 4-3 6 0s4 3 6 0 4-3 6 0s4 3 6 0"/><path d="M2 17c2-3 4-3 6 0s4 3 6 0 4-3 6 0s4 3 6 0"/><path d="M2 7c2-3 4-3 6 0s4 3 6 0 4-3 6 0s4 3 6 0"/>');
const sun = icon('<circle cx="12" cy="12" r="5"/><path d="M12 1v2"/><path d="M12 21v2"/><path d="M4.22 4.22l1.42 1.42"/><path d="M18.36 18.36l1.42 1.42"/><path d="M1 12h2"/><path d="M21 12h2"/><path d="M4.22 19.78l1.42-1.42"/><path d="M18.36 5.64l1.42-1.42"/>');
const moon = icon('<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>');
const star = icon('<polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>');
const comet = icon('<circle cx="17" cy="7" r="4"/><path d="M2 22l11-11"/><path d="M2 18l7-7"/><path d="M6 22l7-7"/>');
const snowflake = icon('<path d="M12 2v20"/><path d="M2 12h20"/><path d="M4.93 4.93l14.14 14.14"/><path d="M19.07 4.93L4.93 19.07"/><path d="M9 2l3 3 3-3"/><path d="M9 22l3-3 3 3"/>');

// ─── Abstract / Shapes ────────────────────────────────────
const shield = icon('<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>');
const compass = icon('<circle cx="12" cy="12" r="10"/><polygon points="16.24 7.76 14.12 14.12 7.76 16.24 9.88 9.88 16.24 7.76"/>');
const target = icon('<circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/>');
const prism = icon('<path d="M12 2L2 20h20z"/><path d="M12 2v18"/><path d="M7 11h10"/>');
const infinity = icon('<path d="M12 12c-2-2.67-4-4-6-4a4 4 0 1 0 0 8c2 0 4-1.33 6-4zm0 0c2 2.67 4 4 6 4a4 4 0 0 0 0-8c-2 0-4 1.33-6 4z"/>');
const spiral = icon('<path d="M12 12a3 3 0 1 0 3-3 5.5 5.5 0 1 1-5.5-5.5A8 8 0 1 0 20 12"/>');
const hexagon = icon('<path d="M12 2l8.66 5v10L12 22l-8.66-5V7z"/>');
const diamond = icon('<path d="M12 2l10 10-10 10L2 12z"/><path d="M12 2v20"/><path d="M2 12h20"/>');
const orbit = icon('<circle cx="12" cy="12" r="2"/><ellipse cx="12" cy="12" rx="10" ry="4"/><ellipse cx="12" cy="12" rx="4" ry="10"/>');
const vortex = icon('<path d="M12 2a10 10 0 0 1 7.07 2.93"/><path d="M21 8a10 10 0 0 1 .07 3"/><path d="M22 14a10 10 0 0 1-2.93 5.07"/><path d="M17 20a10 10 0 0 1-5 2"/><path d="M8 22a10 10 0 0 1-4.07-2.93"/><circle cx="12" cy="12" r="3"/>');

// ─── People / Creatures ───────────────────────────────────
const eye = icon('<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8S1 12 1 12z"/><circle cx="12" cy="12" r="3"/>');
const heart = icon('<path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/>');
const crown = icon('<path d="M2 18h20l-3-12-5 6-2-8-2 8-5-6z"/><path d="M2 18v2h20v-2"/>');
const wizard = icon('<path d="M12 2l3 10H9z"/><path d="M6 12c0 0-2 1-2 4 0 2 2 4 2 4h12s2-2 2-4c0-3-2-4-2-4"/><path d="M12 2l1.5 5.5L19 6"/>');
const mask = icon('<path d="M12 3c-4 0-8 2-8 7s3 8 8 8 8-3 8-8-4-7-8-7z"/><circle cx="9" cy="10" r="1.5"/><circle cx="15" cy="10" r="1.5"/><path d="M9 15c1 1 2 1.5 3 1.5s2-.5 3-1.5"/>');
const ghost = icon('<path d="M9 10h.01"/><path d="M15 10h.01"/><path d="M12 2a8 8 0 0 0-8 8v12l3-3 2.5 2.5L12 19l2.5 2.5L17 19l3 3V10a8 8 0 0 0-8-8z"/>');
const paw = icon('<circle cx="11" cy="4" r="2"/><circle cx="18" cy="8" r="2"/><circle cx="4" cy="8" r="2"/><path d="M12 11c-2.8 0-5 2-5 4.5S9.2 20 12 20s5-2 5-4.5S14.8 11 12 11z"/>');
const bird = icon('<path d="M16 7c0-2.8-2.2-5-5-5-1.7 0-3.2.9-4.1 2.2"/><path d="M3 14h5l4-7c3.5 0 6 1.5 8 5H10l-4 4"/><path d="M6 18l-3 3"/>');
const butterfly = icon('<path d="M12 7V22"/><path d="M12 7C12 7 7 2 3 5s1 9 9 7"/><path d="M12 7c0 0 5-5 9-2s-1 9-9 7"/>');
const dragon = icon('<path d="M5 18l2-6 4 2 3-6 5 3V6l3-2-2 5-5 2-3 5-4-2-2 5"/><path d="M3 20l2-2"/><circle cx="18" cy="5" r="1"/>');
const phoenix = icon('<path d="M12 18c-4 0-7-2-7-5 0-4 3-7 7-9 4 2 7 5 7 9 0 3-3 5-7 5z"/><path d="M12 4V2"/><path d="M12 18v4"/><path d="M8 3L10 6"/><path d="M16 3l-2 3"/><path d="M5 6l3 2"/><path d="M19 6l-3 2"/>');
const owl = icon('<circle cx="9" cy="11" r="3"/><circle cx="15" cy="11" r="3"/><circle cx="9" cy="11" r="1"/><circle cx="15" cy="11" r="1"/><path d="M3 11c0-5 4-9 9-9s9 4 9 9"/><path d="M12 14v2"/><path d="M10 18c1 1 2 1 4 0"/><path d="M5 14l-2 4"/><path d="M19 14l2 4"/>');
const fox = icon('<path d="M4 3l4 7h8l4-7"/><path d="M8 10C4 10 2 14 2 17h20c0-3-2-7-6-7"/><circle cx="10" cy="14" r="1"/><circle cx="14" cy="14" r="1"/><path d="M12 16v1"/><path d="M10 18l2 1 2-1"/>');
const wolf = icon('<path d="M3 4l4 4v4c0 4 2 7 5 8 3-1 5-4 5-8V8l4-4"/><circle cx="9" cy="11" r="1"/><circle cx="15" cy="11" r="1"/><path d="M12 14v2"/><path d="M10 17l2 1 2-1"/>');
const cat = icon('<path d="M12 22c4.42 0 8-2.69 8-6V8l-4-6-2 4h-4L8 2 4 8v8c0 3.31 3.58 6 8 6z"/><circle cx="9" cy="12" r="1"/><circle cx="15" cy="12" r="1"/><path d="M10 16h4"/>');
const octopus = icon('<circle cx="12" cy="8" r="6"/><path d="M6 14c-1 3-2 5 0 6"/><path d="M10 14c0 3-1 5 1 6"/><path d="M14 14c0 3 1 5-1 6"/><path d="M18 14c1 3 2 5 0 6"/><circle cx="10" cy="7" r="1"/><circle cx="14" cy="7" r="1"/>');

// ─── Music / Art ──────────────────────────────────────────
const palette = icon('<circle cx="13.5" cy="6.5" r="1"/><circle cx="17" cy="11" r="1"/><circle cx="8" cy="8" r="1"/><circle cx="6.5" cy="13" r="1"/><path d="M12 2C6.5 2 2 6.5 2 12s4.5 10 10 10c.9 0 1.5-.7 1.5-1.5 0-.4-.1-.7-.4-1-.2-.3-.4-.7-.4-1.1 0-.8.7-1.5 1.5-1.5H16c3.3 0 6-2.7 6-6 0-5.5-4.5-9-10-9z"/>');
const music = icon('<path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>');
const pen = icon('<path d="M17 3a2.83 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5z"/>');
const camera = icon('<path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="4"/>');
const brush = icon('<path d="M9.06 11.9l8.07-8.06a2.85 2.85 0 1 1 4.03 4.03l-8.06 8.08"/><path d="M7.07 14.94c-1.66 0-3 1.35-3 3.02 0 1.33-2.5 1.52-2 2.02 1.08 1.1 2.49 2.02 4 2.02 2.2 0 4-1.8 4-4.04a3.01 3.01 0 0 0-3-3.02z"/>');

// ─── Navigation / Time ────────────────────────────────────
const hourglass = icon('<path d="M5 22h14"/><path d="M5 2h14"/><path d="M17 22v-4.17a2 2 0 0 0-.59-1.42L12 12l-4.41 4.41a2 2 0 0 0-.59 1.42V22"/><path d="M7 2v4.17a2 2 0 0 0 .59 1.42L12 12l4.41-4.41A2 2 0 0 0 17 6.17V2"/>');
const calendar = icon('<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4"/><path d="M8 2v4"/><path d="M3 10h18"/><path d="M8 14h.01"/><path d="M12 14h.01"/><path d="M16 14h.01"/><path d="M8 18h.01"/><path d="M12 18h.01"/>');
const clock = icon('<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>');
const rocket = icon('<path d="M4.5 16.5c-1.5 1.26-2 5-2 5s3.74-.5 5-2c.71-.84.7-2.13-.09-2.91a2.18 2.18 0 0 0-2.91-.09z"/><path d="M12 15l-3-3a22 22 0 0 1 2-3.95A12.88 12.88 0 0 1 22 2c0 2.72-.78 7.5-6 11a22 22 0 0 1-4 2z"/><path d="M9 12H4s.55-3.03 2-4c1.62-1.08 5 0 5 0"/><path d="M12 15v5s3.03-.55 4-2c1.08-1.62 0-5 0-5"/>');
const anchor = icon('<circle cx="12" cy="5" r="3"/><path d="M12 8v14"/><path d="M5 12H2a10 10 0 0 0 20 0h-3"/>');

// ─── Data / Organization ──────────────────────────────────
const chart = icon('<path d="M18 20V10"/><path d="M12 20V4"/><path d="M6 20v-6"/>');
const layers = icon('<polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/><polyline points="2 12 12 17 22 12"/>');
const network = icon('<circle cx="12" cy="5" r="3"/><circle cx="5" cy="19" r="3"/><circle cx="19" cy="19" r="3"/><path d="M10.5 7.5L7 16.5"/><path d="M13.5 7.5L17 16.5"/><path d="M8 19h8"/>');
const filter = icon('<polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3"/>');
const cube = icon('<path d="M12 2l10 6v8l-10 6L2 16V8z"/><path d="M12 22V10"/><path d="M22 8L12 14 2 8"/>');
const grid = icon('<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>');
const stack = icon('<rect x="4" y="14" width="16" height="6" rx="1"/><rect x="4" y="4" width="16" height="6" rx="1"/>');
const vault = icon('<rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="12" cy="12" r="4"/><path d="M12 8v4l2 2"/><path d="M3 9h2"/><path d="M3 15h2"/><path d="M19 9h2"/><path d="M19 15h2"/>');

// ─── Symbols / Misc ───────────────────────────────────────
const bolt = icon('<path d="M13 2L3 14h8l-1 8 10-12h-8l1-8z"/>');
const crosshair = icon('<circle cx="12" cy="12" r="10"/><path d="M22 12h-4"/><path d="M6 12H2"/><path d="M12 6V2"/><path d="M12 22v-4"/>');
const fingerprint = icon('<path d="M2 12C2 6.48 6.48 2 12 2s10 4.48 10 10"/><path d="M12 6a6 6 0 0 1 6 6"/><path d="M12 6c-3.31 0-6 2.69-6 6"/><path d="M12 10a2 2 0 0 1 2 2c0 1.1-.3 3-1 5"/><path d="M10 12a2 2 0 0 1 .15-.76"/><path d="M8 15a7 7 0 0 0 .78 3"/>');
const key = icon('<path d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.78 7.78 5.5 5.5 0 0 1 7.78-7.78zM15.5 7.5l-1 1"/><path d="M14 4l6 6"/>');
const lock = icon('<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>');
const link = icon('<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>');
const flag = icon('<path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z"/><path d="M4 22v-7"/>');
const badge = icon('<path d="M12 2l2.4 3.6L18 4l-1.2 3.8L21 10l-4 .8-.4 4.2L12 12.5 7.4 15l-.4-4.2L3 10l4.2-2.2L6 4l3.6 1.6z"/>');
const wand = icon('<path d="M15 4V2"/><path d="M15 16v-2"/><path d="M8 9h2"/><path d="M20 9h2"/><path d="M17.8 11.8L19 13"/><path d="M15 9h.01"/><path d="M11 6.2L9.8 5"/><path d="M11 11.8L9.8 13"/><path d="M17.8 6.2L19 5"/><path d="M3 21l10-10"/>');
const lantern = icon('<path d="M9 2h6"/><path d="M12 2v3"/><path d="M8 5h8a2 2 0 0 1 2 2v6a6 6 0 0 1-12 0V7a2 2 0 0 1 2-2z"/><path d="M10 22h4"/><path d="M12 18v4"/>');
const lotus = icon('<path d="M12 4c-2 3-5 5-5 8a5 5 0 0 0 10 0c0-3-3-5-5-8z"/><path d="M12 4c-5 2-8 5-9 8 2 1 5 1 9-1"/><path d="M12 4c5 2 8 5 9 8-2 1-5 1-9-1"/>');
const zap = icon('<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>');
const swords = icon('<path d="M14.5 17.5L3 6V3h3l11.5 11.5"/><path d="M13 19l6 -6"/><path d="M16 16l4 4"/><path d="M9.5 6.5L21 18v3h-3L6.5 9.5"/><path d="M11 5l-6 6"/><path d="M4 8L8 4"/>');
const scrollText = icon('<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7z"/><path d="M14 2v4a1 1 0 0 0 1 1h3"/><path d="M10 13h4"/><path d="M10 17h4"/><path d="M10 9h1"/>');
const mail = icon('<rect x="2" y="4" width="20" height="16" rx="2"/><path d="M22 7l-10 7L2 7"/>');
const send = icon('<path d="M22 2L11 13"/><path d="M22 2l-7 20-4-9-9-4z"/>');
const users = icon('<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>');
const gears = icon('<circle cx="9" cy="9" r="2"/><circle cx="16" cy="16" r="2"/><path d="M5.7 5.7L2.8 2.8"/><path d="M12 9H6"/><path d="M9 12V6"/><path d="M12.3 12.3l-2.6-2.6"/><path d="M22 16h-6"/><path d="M16 22v-6"/><path d="M18.3 18.3l2.9 2.9"/>');

// ─── Master icon map ─────────────────────────────────────
const ICON_MAP: Record<string, IconComponent> = {
  // AI
  brain, cpu, circuit, neural, bot, chip, binary, algorithm,
  // Research
  search, globe, satellite, radar, antenna, broadcast, megaphone,
  // Tools
  wrench, gear, gears, hammer, terminal, code, pipeline, toolkit,
  // Science
  flask, atom, microscope, dna, telescope, book, scroll, library, lightbulb,
  // Nature
  sparkles, flame, lightning, crystal, leaf, tree, mountain, wave, sun, moon, star, comet, snowflake,
  // Abstract
  shield, compass, target, prism, infinity, spiral, hexagon, diamond, orbit, vortex,
  // Creatures
  eye, heart, crown, wizard, mask, ghost, paw, bird, butterfly, dragon, phoenix, owl, fox, wolf, cat, octopus,
  // Art
  palette, music, pen, camera, brush,
  // Time
  hourglass, calendar, clock, rocket, anchor,
  // Data
  chart, layers, network, filter, cube, grid, stack, vault,
  // Symbols
  bolt, crosshair, fingerprint, key, lock, link, flag, badge, wand, lantern, lotus, zap, swords,
  'scroll-text': scrollText, mail, send, users,
};

export function getAgentIcon(iconName: string | null | undefined): IconComponent {
  if (!iconName) return sparkles;
  return ICON_MAP[iconName.toLowerCase()] || sparkles;
}

/** Get all available icon names (for tooling/autocomplete) */
export function getIconNames(): string[] {
  return Object.keys(ICON_MAP);
}

export type { IconComponent, IconProps };
