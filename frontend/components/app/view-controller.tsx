'use client';

import React from 'react';
import type { AppConfig } from '@/app-config';
import { MyCitiPadCard } from './mycitipad-card';

interface ViewControllerProps {
  appConfig?: AppConfig;
}

export function ViewController({ appConfig }: ViewControllerProps) {
  return (
    <div className="flex min-h-svh w-full items-center justify-center p-4">
      <MyCitiPadCard />
      {appConfig && <span className="sr-only">{appConfig.companyName}</span>}
    </div>
  );
}
