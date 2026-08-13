import React from 'react';
import {Composition} from 'remotion';
import {SmokeTest} from './SmokeTest';

export const RemotionRoot: React.FC = () => (
  <Composition
    id="SmokeTest"
    component={SmokeTest}
    durationInFrames={90}
    fps={30}
    width={1080}
    height={1920}
  />
);

