import {
  Heart, Zap, Brain, Search, Globe, Code, Layers, Sparkles, Bot,
  type LucideIcon
} from 'lucide-react';

const ICON_MAP: Record<string, LucideIcon> = {
  heart: Heart,
  zap: Zap,
  brain: Brain,
  search: Search,
  globe: Globe,
  code: Code,
  layers: Layers,
  sparkles: Sparkles,
  bot: Bot,
};

export function getAgentIcon(iconName: string | null | undefined): LucideIcon {
  if (!iconName) return Sparkles;
  return ICON_MAP[iconName.toLowerCase()] || Sparkles;
}
