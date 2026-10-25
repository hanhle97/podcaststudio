import React from 'react';
import {
  AbsoluteFill, Img, Loop, OffthreadVideo, interpolate, staticFile, useCurrentFrame,
  useVideoConfig,
} from 'remotion';
import type {BackgroundSpec, Brand, Chapter} from '../types';
import {hexToRgba} from '../time';

type Props = {
  spec: BackgroundSpec;
  brand: Brand;
  chapters: Chapter[];
  /** Episode time (s) at frame 0 of this composition (Shorts start mid-episode). */
  t0?: number;
};

const CROSSFADE = 1.2;

const Gradient: React.FC<{colors: string[]; t: number}> = ({colors, t}) => {
  const angle = 135 + 25 * Math.sin(t / 18);
  const glowX = 50 + 25 * Math.sin(t / 23);
  const glowY = 40 + 15 * Math.cos(t / 29);
  const [a, b, c] = [colors[0], colors[1] ?? colors[0], colors[2] ?? colors[1] ?? colors[0]];
  return (
    <AbsoluteFill
      style={{
        background: `radial-gradient(circle at ${glowX}% ${glowY}%, ${hexToRgba(c, 0.55)} 0%, transparent 55%),
          linear-gradient(${angle}deg, ${a} 0%, ${b} 55%, ${c} 100%)`,
      }}
    />
  );
};

const KenBurns: React.FC<{images: string[]; chapters: Chapter[]; t: number}> = ({
  images, chapters, t,
}) => {
  return (
    <AbsoluteFill>
      {chapters.map((ch, i) => {
        const visible = t >= ch.start - 0.01 && t < ch.end + CROSSFADE;
        if (!visible) return null;
        const src = images[ch.index % images.length];
        const span = Math.max(1, ch.end - ch.start + CROSSFADE);
        const p = Math.min(1, Math.max(0, (t - ch.start) / span));
        const scale = 1.04 + 0.1 * p;
        const dir = i % 2 === 0 ? 1 : -1;
        const opacity = i === 0 ? 1 : interpolate(t, [ch.start, ch.start + CROSSFADE], [0, 1], {
          extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
        });
        return (
          <AbsoluteFill key={ch.index} style={{opacity}}>
            <Img
              src={staticFile(src)}
              style={{
                width: '100%', height: '100%', objectFit: 'cover',
                transform: `scale(${scale}) translateX(${dir * (p - 0.5) * 3}%)`,
              }}
            />
          </AbsoluteFill>
        );
      })}
    </AbsoluteFill>
  );
};

export const Background: React.FC<Props> = ({spec, brand, chapters, t0 = 0}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const t = t0 + frame / fps;
  const bg = brand.colors.background;

  let layer: React.ReactNode;
  if (spec.kind === 'kenburns' && spec.images.length > 0) {
    layer = <KenBurns images={spec.images} chapters={chapters} t={t} />;
  } else if (spec.kind === 'loop' && spec.video) {
    const loopFrames = Math.max(1, Math.round(spec.videoDurationSec * fps));
    layer = (
      <Loop durationInFrames={loopFrames}>
        <OffthreadVideo
          src={staticFile(spec.video)} muted
          style={{width: '100%', height: '100%', objectFit: 'cover'}}
        />
      </Loop>
    );
  } else {
    layer = <Gradient colors={brand.colors.gradient} t={t} />;
  }

  return (
    <AbsoluteFill style={{backgroundColor: bg}}>
      {layer}
      {/* readability scrim: darker at the top (header) and bottom (subtitles) */}
      <AbsoluteFill
        style={{
          background: `linear-gradient(180deg, ${hexToRgba(bg, 0.55)} 0%, ${hexToRgba(bg, 0.1)}
            30%, ${hexToRgba(bg, 0.15)} 60%, ${hexToRgba(bg, 0.8)} 100%)`,
        }}
      />
    </AbsoluteFill>
  );
};
