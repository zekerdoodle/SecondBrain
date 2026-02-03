// API Configuration
// Single-port setup: API and frontend served from same origin

const protocol = window.location.protocol;
const host = window.location.host;

// Same origin for both local dev and production
export const API_BASE = `${protocol}//${host}`;
export const API_URL = `${API_BASE}/api`;

// WebSocket uses wss:// for https://, ws:// for http://
const wsProtocol = protocol === 'https:' ? 'wss:' : 'ws:';
export const WS_URL = `${wsProtocol}//${host}`;
