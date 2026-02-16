import { useState, useEffect, useCallback } from 'react';
import { API_URL } from '../config';
import type { Agent } from '../types';

export function useAgents() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch(`${API_URL}/agents`)
      .then(res => res.json())
      .then(data => {
        setAgents(data.agents || []);
        setLoading(false);
      })
      .catch(err => {
        console.error('Failed to fetch agents:', err);
        setLoading(false);
      });
  }, []);

  const defaultAgent = agents.find(a => a.is_default) || agents[0] || null;

  const getAgent = useCallback((name: string): Agent | undefined => {
    return agents.find(a => a.name === name);
  }, [agents]);

  return { agents, loading, defaultAgent, getAgent };
}
