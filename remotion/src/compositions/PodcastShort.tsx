import React from 'react';
import {
  AbsoluteFill, Audio, interpolate, spring, staticFile, useCurrentFrame, useVideoConfig,
} from 'remotion';
import '../fonts';
import {FONT_FAMILY} from '../fonts';
import type {ShortProps} from '../types';
import {Background} from '../components/Background';
import {Header} from '../components/Header';
import {KaraokeCaption} from '../components/KaraokeCaption';
import {ProgressBar} from '../components/ProgressBar';
import {Waveform} from '../components/Waveform';
import {findChapter} from '../time';

const HOOK_SECONDS = 2.6;

const Hook: React.FC<{text: string; color: string; accent: string}> = ({text, color, accent}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const enter = spring({frame, fps, config: {damping: 16}});
  const out = interpolate(frame, [HOOK_SECONDS * fps - 10, HOOK_SECONDS * fps], [1, 0], {
    extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
  });
  if (frame > HOOK_SECONDS * fps) return null;
  return (
    <div
      style={{
        fontFamily: FONT_FAMILY, fontSize: 78, fontWeight: 700, lineHeight: 1.2, color,
        textAlign: 'center', maxWidth: 920, opacity: Math.min(enter, out),
        transform: `translateY(${interpolate(enter, [0, 1], [40, 0])}px)`,
        padding: '28px 40px', borderRadius: 28, background: 'rgba(0,0,0,0.35)',
        borderBottom: `8px solid ${accent}`,
      }}
    >
      {text}
    </div>
  );
};

export const PodcastShort: React.FC<ShortProps> = (props) => {
  const frame = useCurrentFrame();
  const {fps, durationInFrames} = useVideoConfig();
  const t0 = props.startSec;
  const startFrame = Math.round(props.startSec * fps);
  const endFrame = Math.round(props.endSec * fps);
  const chapter = findChapter(props.chapters, t0 + frame / fps);
  const fade = interpolate(frame, [0, 8, durationInFrames - 12, durationInFrames - 1],
    [0, 1, 1, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});

  return (
    <AbsoluteFill style={{backgroundColor: '#000'}}>
      <AbsoluteFill style={{opacity: fade}}>
        <Background spec={props.background} brand={props.brand} chapters={props.chapters}
          t0={t0} />

        <AbsoluteFill style={{alignItems: 'center', paddingTop: 120}}>
          <Header brand={props.brand} logoSize={64} nameSize={34} />
          {chapter ? (
            <div style={{fontFamily: FONT_FAMILY, marginTop: 26, fontSize: 34, fontWeight: 700,
              color: props.brand.colors.primary, textAlign: 'center', maxWidth: 900}}>
              {chapter.title}
            </div>
          ) : null}
        </AbsoluteFill>

        <AbsoluteFill style={{alignItems: 'center', top: 430, height: 420,
          justifyContent: 'center'}}>
          <Hook text={props.hook} color={props.brand.colors.text}
            accent={props.brand.colors.primary} />
        </AbsoluteFill>

        <AbsoluteFill style={{justifyContent: 'center', alignItems: 'center', paddingTop: 260}}>
          <KaraokeCaption groups={props.karaoke} brand={props.brand} t0={t0} size={104}
            maxWidth={940} />
        </AbsoluteFill>

        <AbsoluteFill style={{alignItems: 'center', justifyContent: 'flex-end',
          paddingBottom: 300}}>
          <Waveform src={props.viz} color={props.brand.colors.primary} width={760} height={140}
            frameOffset={startFrame} bars={36} />
        </AbsoluteFill>

        <div style={{position: 'absolute', left: 90, bottom: 150}}>
          <ProgressBar brand={props.brand} width={900} height={8} from={props.startSec}
            to={props.endSec} />
        </div>
      </AbsoluteFill>
      <Audio
        src={staticFile(props.audio)} trimBefore={startFrame} trimAfter={endFrame}
        volume={(f) => interpolate(f, [0, 8, durationInFrames - 12, durationInFrames],
          [0, 1, 1, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'})}
      />
    </AbsoluteFill>
  );
};

export const shortFrames = (p: ShortProps) =>
  Math.max(1, Math.ceil((p.endSec - p.startSec) * p.fps));
