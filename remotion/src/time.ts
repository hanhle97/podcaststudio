import type {Chapter, Cue, KaraokeGroup, Word} from './types';

export const findChapter = (chapters: Chapter[], t: number): Chapter | null =>
  chapters.find((c) => t >= c.start && t < c.end) ?? chapters[chapters.length - 1] ?? null;

export const findCue = (cues: Cue[], t: number): Cue | null =>
  cues.find((c) => t >= c.start && t < c.end) ?? null;

/** Active karaoke group; holds the previous group through short gaps. */
export const findGroup = (groups: KaraokeGroup[], t: number, hold = 0.6): KaraokeGroup | null => {
  for (let i = 0; i < groups.length; i++) {
    const g = groups[i];
    const next = groups[i + 1];
    const until = next ? Math.min(next.start, g.end + hold) : g.end + hold;
    if (t >= g.start && t < until) return g;
  }
  return null;
};

export type WordState = 'past' | 'current' | 'future';

export const wordState = (words: Word[], i: number, t: number): WordState => {
  const w = words[i];
  const next = words[i + 1];
  if (t < w.start) return 'future';
  if (next ? t < next.start : t < w.end + 0.25) return 'current';
  return 'past';
};

export const hexToRgba = (hex: string, alpha: number): string => {
  const h = hex.replace('#', '');
  const n = parseInt(h.length === 3 ? h.split('').map((c) => c + c).join('') : h, 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
};
