import React from 'react';
import {AbsoluteFill, Audio, interpolate, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import '../fonts';
import type {PodcastProps} from '../types';
import {Background} from '../components/Background';
import {ChapterTitle} from '../components/ChapterTitle';
import {Header} from '../components/Header';
import {ProgressBar} from '../components/ProgressBar';
import {Subtitles} from '../components/Subtitles';
import {Waveform} from '../components/Waveform';

const PAD = 96;

export const PodcastLandscape: React.FC<PodcastProps> = (props) => {
  const frame = useCurrentFrame();
  const {durationInFrames, width} = useVideoConfig();
  const fade = interpolate(frame, [0, 15, durationInFrames - 20, durationInFrames - 1],
    [0, 1, 1, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});

  return (
    <AbsoluteFill style={{backgroundColor: '#000'}}>
      <AbsoluteFill style={{opacity: fade}}>
        <Background spec={props.background} brand={props.brand} chapters={props.chapters} />

        <div style={{position: 'absolute', top: 64, left: PAD}}>
          <Header brand={props.brand} title={props.title} logoSize={72} nameSize={30} />
        </div>

        <AbsoluteFill
          style={{justifyContent: 'center', alignItems: 'center', flexDirection: 'column',
            gap: 56, paddingTop: 40}}
        >
          <ChapterTitle chapters={props.chapters} brand={props.brand} size={84} />
          <Waveform src={props.viz} color={props.brand.colors.primary} width={760} height={150} />
        </AbsoluteFill>

        <AbsoluteFill style={{justifyContent: 'flex-end', alignItems: 'center', paddingBottom: 120}}>
          <div style={{maxWidth: 1500}}>
            <Subtitles cues={props.cues} brand={props.brand} size={50} />
          </div>
        </AbsoluteFill>

        <div style={{position: 'absolute', left: PAD, bottom: 44}}>
          <ProgressBar brand={props.brand} width={width - PAD * 2} height={8} from={0}
            to={props.durationSec} chapters={props.chapters} />
        </div>
      </AbsoluteFill>
      <Audio src={staticFile(props.audio)} />
    </AbsoluteFill>
  );
};

export const landscapeFrames = (p: PodcastProps) => Math.max(1, Math.ceil(p.durationSec * p.fps));
