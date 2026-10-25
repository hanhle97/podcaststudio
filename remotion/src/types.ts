// Props contract with media_service/app/render/props.py — keep both in sync.

export type Word = {word: string; start: number; end: number};

/** One SRT cue; each line is a list of timed words. */
export type Cue = {start: number; end: number; lines: Word[][]};

export type Chapter = {index: number; title: string; start: number; end: number};

export type KaraokeGroup = {start: number; end: number; text: string; words: Word[]};

export type Brand = {
  channelName: string;
  logo: string | null; // path inside the public dir
  colors: {
    primary: string;
    background: string;
    text: string;
    muted: string;
    gradient: string[];
  };
};

export type BackgroundSpec = {
  kind: 'kenburns' | 'loop' | 'gradient';
  images: string[]; // kenburns: one per chapter, cycled
  video: string | null; // loop
  videoDurationSec: number;
};

export type PodcastProps = {
  fps: number;
  durationSec: number;
  audio: string; // full mix, path inside the public dir
  viz: string; // small mono WAV used only for the waveform
  title: string;
  brand: Brand;
  background: BackgroundSpec;
  chapters: Chapter[];
  cues: Cue[];
  karaoke: KaraokeGroup[];
};

export type ShortProps = PodcastProps & {
  startSec: number;
  endSec: number;
  hook: string;
};
