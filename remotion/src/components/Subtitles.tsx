import React from 'react';
import {interpolate, useCurrentFrame, useVideoConfig} from 'remotion';
import type {Brand, Cue} from '../types';
import {FONT_FAMILY} from '../fonts';
import {findCue, hexToRgba, wordState} from '../time';

type Props = {cues: Cue[]; brand: Brand; size: number};

/** Bottom subtitles (the SRT cues); the word being spoken turns the brand colour. */
export const Subtitles: React.FC<Props> = ({cues, brand, size}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const t = frame / fps;
  const cue = findCue(cues, t);
  if (!cue) return null;

  const opacity = interpolate(t, [cue.start, cue.start + 0.15, cue.end - 0.12, cue.end],
    [0, 1, 1, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  const words = cue.lines.flat();
  let k = 0;

  return (
    <div
      style={{
        opacity, fontFamily: FONT_FAMILY, fontSize: size, fontWeight: 700, lineHeight: 1.35,
        textAlign: 'center', padding: `${size * 0.3}px ${size * 0.7}px`, borderRadius: size * 0.4,
        background: hexToRgba(brand.colors.background, 0.55),
        boxShadow: '0 8px 40px rgba(0,0,0,0.25)',
      }}
    >
      {cue.lines.map((line, li) => (
        <div key={li}>
          {line.map((w, wi) => {
            const state = wordState(words, k++, t);
            const color = state === 'current' ? brand.colors.primary : brand.colors.text;
            return (
              <span key={wi} style={{color, opacity: state === 'future' ? 0.6 : 1}}>
                {w.word}
                {wi < line.length - 1 ? ' ' : ''}
              </span>
            );
          })}
        </div>
      ))}
    </div>
  );
};
