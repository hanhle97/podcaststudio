import React from 'react';
import {useCurrentFrame, useVideoConfig} from 'remotion';
import type {Brand, Chapter} from '../types';
import {hexToRgba} from '../time';

type Props = {
  brand: Brand;
  width: number;
  height: number;
  /** Shown range in episode seconds (Shorts show only their clip). */
  from: number;
  to: number;
  chapters?: Chapter[];
};

/** Progress bar with tick marks at chapter starts. */
export const ProgressBar: React.FC<Props> = ({brand, width, height, from, to, chapters = []}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const t = from + frame / fps;
  const span = Math.max(0.001, to - from);
  const progress = Math.min(1, Math.max(0, (t - from) / span));

  return (
    <div style={{position: 'relative', width, height: height * 3}}>
      <div
        style={{
          position: 'absolute', top: height, left: 0, width, height, borderRadius: height,
          background: hexToRgba(brand.colors.text, 0.22),
        }}
      />
      <div
        style={{
          position: 'absolute', top: height, left: 0, width: width * progress, height,
          borderRadius: height, background: brand.colors.primary,
        }}
      />
      {chapters.slice(1).map((c) => {
        const x = ((c.start - from) / span) * width;
        if (x <= 0 || x >= width) return null;
        return (
          <div
            key={c.index}
            style={{
              position: 'absolute', top: 0, left: x - 2, width: 4, height: height * 3,
              borderRadius: 2, background: t >= c.start ? brand.colors.primary : brand.colors.text,
              opacity: 0.9,
            }}
          />
        );
      })}
    </div>
  );
};
