import React from 'react';
import {AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';

export const SmokeTest: React.FC = () => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const entrance = spring({frame, fps, config: {damping: 16}});
  const opacity = interpolate(frame, [2.3 * fps, 3 * fps], [1, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  return (
    <AbsoluteFill
      style={{
        alignItems: 'center',
        background: 'radial-gradient(circle at 50% 30%, #123047 0%, #08111F 55%, #04070D 100%)',
        color: '#F5F7FA',
        display: 'flex',
        fontFamily: 'Arial, Microsoft YaHei, sans-serif',
        justifyContent: 'center',
        opacity,
      }}
    >
      <div
        style={{
          border: '3px solid #27E0B3',
          borderRadius: 36,
          padding: '64px 72px',
          textAlign: 'center',
          transform: `scale(${0.82 + entrance * 0.18}) translateY(${(1 - entrance) * 80}px)`,
          width: 760,
        }}
      >
        <div style={{color: '#27E0B3', fontSize: 38, letterSpacing: 8}}>AGENT STUDIO</div>
        <div style={{fontSize: 88, fontWeight: 800, lineHeight: 1.1, marginTop: 36}}>视频工具链<br />安装成功</div>
        <div style={{color: '#FFC857', fontSize: 34, marginTop: 42}}>Remotion · FFmpeg · Codex</div>
      </div>
    </AbsoluteFill>
  );
};
