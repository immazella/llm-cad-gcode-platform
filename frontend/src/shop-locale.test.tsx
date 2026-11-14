import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import ShopTab from './components/ShopTab';
import { setLocale } from './i18n';
import type { GcodeRecord } from './types';

afterEach(()=>{cleanup();vi.unstubAllGlobals();});
it('keeps dangerous English workshop warnings red and avoids duplicate warning text', async()=>{
  setLocale('en');
  const warning='Rapid motion passes through finished-part material: collision!';
  vi.stubGlobal('fetch',vi.fn(async()=>new Response(JSON.stringify({ok:true,line:1,text:'G0 Z-1',warnings:[warning],summary:'N1: Rapid motion. WARNING: '+warning,after:{tool:null,spindle:{on:false,rpm:null},coolant:false,tool_length_comp:'G49',tool_length:null},issues:[]}))));
  render(<ShopTab gcode={{gcode_id:'g1',line_count:1} as GcodeRecord} activeLine={null} onShowLine={()=>{}} onAsk={()=>{}} onGcodeLoaded={()=>{}} onError={()=>{}} />);
  await screen.findByText(warning);
  expect(document.querySelector('.shop-alert.error')?.textContent).toBe(warning);
  expect(document.querySelector('.line-summary')?.textContent).toBe('Rapid motion.');
  expect(screen.getByText('Prove-out checklist')).toBeTruthy();
  expect(screen.getByText('Dimensional deviation')).toBeTruthy();
  expect(screen.getByPlaceholderText('e.g. PS0010, OT0501, error:20, ALARM:2')).toBeTruthy();
});
