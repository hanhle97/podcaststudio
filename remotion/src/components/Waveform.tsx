import React from 'react';
import {staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import {useWindowedAudioData, visualizeAudio} from '@remotion/media-utils';
import {hexToRgba} from '../time';

type Props = {
  src: string;
  color: string;
  width: number;
  height: number;
  bars?: number;
  /** Frame offset into the audio (Shorts start mid-episode). */
  frameOffset?: number;
};

/** Mirrored frequency bars that react to the audio. */
export const Waveform: React.FC<Props> = ({
  src, color, width, height, bars = 48, frameOffset = 0,
}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  // only a window around the current frame is decoded, so long episodes stay cheap
  const {audioData, dataOffsetInSeconds} = useWindowedAudioData({
    src: staticFile(src), frame: frame + frameOffset, fps, windowInSeconds: 10,
  });
  if (!audioData) return null;

  const half = Math.ceil(bars / 2);
  const raw = visualizeAudio({
    fps, frame: frame + frameOffset, audioData, numberOfSamples: 64, smoothing: true,
    dataOffsetInSeconds,
  }).slice(1, half + 1);
  // speech energy sits in the low bins: lift the quiet ones so the shape stays lively
  const values = raw.map((v) => Math.min(1, Math.pow(v * 3, 0.6)));
  const mirrored = [...values.slice().reverse(), ...values];
  const gap = 6;
  const barWidth = (width - gap * (mirrored.length - 1)) / mirrored.length;

  return (
    <div style={{display: 'flex', alignItems: 'center', gap, width, height}}>
      {mirrored.map((v, i) => {
        const h = Math.max(6, v * height);
        const center = 1 - Math.abs(i - (mirrored.length - 1) / 2) / (mirrored.length / 2);
        return (
          <div
            key={i}
            style={{
              width: barWidth, height: h, borderRadius: barWidth,
              background: hexToRgba(color, 0.45 + 0.5 * center),
            }}
          />
        );
      })}
    </div>
  );
};
