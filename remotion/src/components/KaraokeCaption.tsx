import React from 'react';
import {interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import type {Brand, KaraokeGroup} from '../types';
import {FONT_FAMILY} from '../fonts';
import {findGroup, wordState} from '../time';

type Props = {groups: KaraokeGroup[]; brand: Brand; t0: number; size: number; maxWidth: number};

/** Big centred 2-4 word caption for Shorts; the spoken word pops in the brand colour. */
export const KaraokeCaption: React.FC<Props> = ({groups, brand, t0, size, maxWidth}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const t = t0 + frame / fps;
  const g = findGroup(groups, t);
  if (!g) return null;

  const since = Math.max(0, Math.round((t - g.start) * fps));
  const enter = spring({frame: since, fps, config: {damping: 14, mass: 0.6}});
  const scale = interpolate(enter, [0, 1], [0.85, 1]);

  return (
    <div
      style={{
        fontFamily: FONT_FAMILY, fontSize: size, fontWeight: 700, lineHeight: 1.2,
        textAlign: 'center', maxWidth, opacity: enter, transform: `scale(${scale})`,
        textShadow: '0 6px 30px rgba(0,0,0,0.55)', WebkitTextStroke: `2px rgba(0,0,0,0.35)`,
      }}
    >
      {g.words.map((w, i) => {
        const state = wordState(g.words, i, t);
        const pop = state === 'current' ? 1.08 : 1;
        return (
          <span
            key={i}
            style={{
              display: 'inline-block', margin: `0 ${size * 0.12}px`, transform: `scale(${pop})`,
              color: state === 'current' ? brand.colors.primary : brand.colors.text,
              opacity: state === 'future' ? 0.55 : 1,
            }}
          >
            {w.word}
          </span>
        );
      })}
    </div>
  );
};
