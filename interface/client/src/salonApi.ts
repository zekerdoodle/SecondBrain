// Salons API client — wraps /api/salons endpoints.
//
// See interface/server/main.py for the server-side handlers and
// interface/server/salon_manager.py for the data model.

import { API_URL } from './config';
import type { SalonFull, SalonSummary } from './types';

export async function listSalons(participant?: string): Promise<SalonSummary[]> {
  const url = new URL(`${API_URL}/salons`, window.location.origin);
  if (participant) url.searchParams.set('participant', participant);
  const res = await fetch(url.toString());
  if (!res.ok) throw new Error(`listSalons failed: ${res.status}`);
  const data = await res.json();
  return data.salons || [];
}

export async function getSalon(salonId: string): Promise<SalonFull> {
  const res = await fetch(`${API_URL}/salons/${salonId}`);
  if (!res.ok) throw new Error(`getSalon ${salonId} failed: ${res.status}`);
  return await res.json();
}

export async function createSalon(opts: {
  title: string;
  participants: string[];
  opening_message?: string;
}): Promise<{ salon_id: string }> {
  const res = await fetch(`${API_URL}/salons`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(opts),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`createSalon failed: ${res.status} ${text}`);
  }
  return await res.json();
}

export async function postToSalon(salonId: string, content: string): Promise<{ message_id: string }> {
  const res = await fetch(`${API_URL}/salons/${salonId}/messages`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content }),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`postToSalon failed: ${res.status} ${text}`);
  }
  return await res.json();
}

export async function addParticipant(salonId: string, participant: string): Promise<{ added: boolean }> {
  const res = await fetch(`${API_URL}/salons/${salonId}/participants`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ participant }),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`addParticipant failed: ${res.status} ${text}`);
  }
  return await res.json();
}

export async function deleteSalon(salonId: string): Promise<void> {
  const res = await fetch(`${API_URL}/salons/${salonId}`, { method: 'DELETE' });
  if (!res.ok && res.status !== 404) {
    throw new Error(`deleteSalon failed: ${res.status}`);
  }
}

export async function setSalonTitle(salonId: string, title: string): Promise<void> {
  const res = await fetch(`${API_URL}/salons/${salonId}/title`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title }),
  });
  if (!res.ok) throw new Error(`setSalonTitle failed: ${res.status}`);
}

export async function promoteChatToSalon(opts: {
  chat_id: string;
  participant: string;
  title?: string;
}): Promise<{ salon_id: string }> {
  const res = await fetch(`${API_URL}/salons/promote-chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(opts),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`promoteChatToSalon failed: ${res.status} ${text}`);
  }
  return await res.json();
}
