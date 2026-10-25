import React from 'react';
import {Img, interpolate, spring, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import type {Brand} from '../types';
import {FONT_FAMILY} from '../fonts';

type Props = {brand: Brand; title?: string; logoSize: number; nameSize: number};

/** Logo + channel name (+ optional episode title), gently sliding in. */
export const Header: React.FC<Props> = ({brand, title, logoSize, nameSize}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const enter = spring({frame, fps, config: {damping: 200}, durationInFrames: 30});
  const y = interpolate(enter, [0, 1], [-20, 0]);
  return (
    <div style={{opacity: enter, transform: `translateY(${y}px)`, fontFamily: FONT_FAMILY}}>
      <div style={{display: 'flex', alignItems: 'center', gap: logoSize * 0.3}}>
        {brand.logo ? (
          <Img
            src={staticFile(brand.logo)}
            style={{width: logoSize, height: logoSize, objectFit: 'contain', borderRadius: 12}}
          />
        ) : null}
        <div
          style={{
            color: brand.colors.text, fontSize: nameSize, fontWeight: 700,
            letterSpacing: 0.5, textShadow: '0 2px 12px rgba(0,0,0,0.4)',
          }}
        >
          {brand.channelName}
        </div>
      </div>
      {title ? (
        <div
          style={{
            marginTop: nameSize * 0.9, color: brand.colors.text, fontSize: nameSize * 1.25,
            fontWeight: 700, lineHeight: 1.25, maxWidth: 1250, opacity: 0.92,
            textShadow: '0 2px 16px rgba(0,0,0,0.45)',
          }}
        >
          {title}
        </div>
      ) : null}
    </div>
  );
};
