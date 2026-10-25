import React from 'react';
import {Composition} from 'remotion';
import {PodcastLandscape, landscapeFrames} from './compositions/PodcastLandscape';
import {PodcastShort, shortFrames} from './compositions/PodcastShort';
import type {PodcastProps, ShortProps} from './types';

// Preview defaults for `npm run studio`; real renders always pass --props.
const sample: PodcastProps = {
  fps: 30,
  durationSec: 8,
  audio: 'audio.wav',
  viz: 'viz.wav',
  title: 'Người ở đâu? Tập thử nghiệm',
  brand: {
    channelName: 'Podcast Studio',
    logo: null,
    colors: {primary: '#F5B700', background: '#0F172A', text: '#F8FAFC', muted: '#94A3B8',
      gradient: ['#0F172A', '#1E3A8A', '#4C1D95']},
  },
  background: {kind: 'gradient', images: [], video: null, videoDurationSec: 0},
  chapters: [{index: 0, title: 'Mở đầu', start: 0, end: 8}],
  cues: [{start: 0.5, end: 3, lines: [[{word: 'Người', start: 0.5, end: 0.9},
    {word: 'ở', start: 0.9, end: 1.1}, {word: 'đâu?', start: 1.1, end: 1.6}]]}],
  karaoke: [{start: 0.5, end: 1.6, text: 'Người ở đâu?', words: [
    {word: 'Người', start: 0.5, end: 0.9}, {word: 'ở', start: 0.9, end: 1.1},
    {word: 'đâu?', start: 1.1, end: 1.6}]}],
};

const sampleShort: ShortProps = {...sample, startSec: 0, endSec: 8, hook: 'Người ở đâu?'};

export const Root: React.FC = () => (
  <>
    <Composition
      id="PodcastLandscape"
      component={PodcastLandscape}
      width={1920}
      height={1080}
      fps={30}
      durationInFrames={landscapeFrames(sample)}
      defaultProps={sample}
      calculateMetadata={({props}) => ({durationInFrames: landscapeFrames(props), fps: props.fps})}
    />
    <Composition
      id="PodcastShort"
      component={PodcastShort}
      width={1080}
      height={1920}
      fps={30}
      durationInFrames={shortFrames(sampleShort)}
      defaultProps={sampleShort}
      calculateMetadata={({props}) => ({durationInFrames: shortFrames(props), fps: props.fps})}
    />
  </>
);
