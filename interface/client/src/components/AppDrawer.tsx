import { useState, useEffect } from 'react';
import { ChevronDown, ChevronRight, LayoutGrid } from 'lucide-react';
import { API_URL } from '../config';

interface AppEntry {
  name: string;
  icon: string;
  entry: string;
  description: string;
}

interface AppDrawerProps {
  onSelectApp: (entryPath: string) => void;
}

export function AppDrawer({ onSelectApp }: AppDrawerProps) {
  const [expanded, setExpanded] = useState(true);
  const [apps, setApps] = useState<AppEntry[]>([]);

  useEffect(() => {
    fetch(`${API_URL}/apps`)
      .then(res => res.json())
      .then(data => {
        if (Array.isArray(data)) setApps(data);
      })
      .catch(err => console.error('Failed to load apps:', err));
  }, []);

  if (apps.length === 0) return null;

  return (
    <div style={{
      borderTop: '1px solid var(--border-color)',
      backgroundColor: 'var(--bg-primary)',
    }}>
      {/* Header */}
      <button
        onClick={() => setExpanded(!expanded)}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '6px',
          width: '100%',
          padding: '6px 12px',
          border: 'none',
          background: 'var(--bg-secondary)',
          color: 'var(--text-secondary)',
          cursor: 'pointer',
          fontSize: '11px',
          fontWeight: 600,
          textTransform: 'uppercase',
          letterSpacing: '0.05em',
        }}
      >
        {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <LayoutGrid size={14} />
        <span>Apps</span>
      </button>

      {/* Grid */}
      {expanded && (
        <div style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fill, minmax(64px, 1fr))',
          gap: '4px',
          padding: '8px',
          maxHeight: '200px',
          overflowY: 'auto',
        }}>
          {apps.map(app => (
            <button
              key={app.entry}
              onClick={() => onSelectApp(app.entry)}
              title={app.description}
              style={{
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                gap: '4px',
                padding: '6px 4px',
                border: 'none',
                borderRadius: '6px',
                background: 'transparent',
                cursor: 'pointer',
                color: 'var(--text-secondary)',
                transition: 'background 0.15s',
              }}
              onMouseEnter={e => {
                e.currentTarget.style.background = 'var(--bg-tertiary)';
              }}
              onMouseLeave={e => {
                e.currentTarget.style.background = 'transparent';
              }}
            >
              <img
                src={`${API_URL}/app-icon/${app.icon}`}
                alt={app.name}
                style={{
                  width: '40px',
                  height: '40px',
                  borderRadius: '8px',
                  objectFit: 'cover',
                }}
                onError={e => {
                  // Hide broken icon, show placeholder
                  (e.target as HTMLImageElement).style.display = 'none';
                }}
              />
              <span style={{
                fontSize: '11px',
                lineHeight: '1.2',
                textAlign: 'center',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
                width: '100%',
              }}>
                {app.name}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
