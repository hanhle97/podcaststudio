import React from 'react';
import {interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import type {Brand, Chapter} from '../types';
import {FONT_FAMILY} from '../fonts';

type Props = {chapters: Chapter[]; brand: Brand; t0?: number; size: number};

const EXIT = 0.5;

/** Current chapter name; each change slides up with a soft spring and fades out at its end. */
export const ChapterTitle: React.FC<Props> = ({chapters, brand, t0 = 0, size}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const t = t0 + frame / fps;
  const ch = chapters.find((c) => t >= c.start && t < c.end) ?? chapters[chapters.length - 1];
  if (!ch) return null;

  const sinceStart = Math.max(0, Math.round((t - Math.max(ch.start, t0)) * fps));
  const enter = spring({frame: sinceStart, fps, config: {damping: 18, mass: 0.8}});
  const exit = ch.index === chapters[chapters.length - 1].index ? 1 :
    interpolate(t, [ch.end - EXIT, ch.end], [1, 0], {
      extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
    });
  const y = interpolate(enter, [0, 1], [size * 0.5, 0]);

  return (
    <div
      style={{
        fontFamily: FONT_FAMILY, textAlign: 'center', opacity: Math.min(enter, exit),
        transform: `translateY(${y}px)`,
      }}
    >
      <div
        style={{
          color: brand.colors.primary, fontSize: size * 0.32, fontWeight: 700,
          letterSpacing: size * 0.04, textTransform: 'uppercase', marginBottom: size * 0.15,
        }}
      >
        Phần {ch.index + 1}/{chapters.length}
      </div>
      <div
        style={{
          color: brand.colors.text, fontSize: size, fontWeight: 700, lineHeight: 1.15,
          textShadow: '0 4px 24px rgba(0,0,0,0.45)',
        }}
      >
        {ch.title}
      </div>
    </div>
  );
};
