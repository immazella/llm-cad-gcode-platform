import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { useState } from 'react';
import MachiningSettings, { type SettingsSub } from './components/MachiningSettings';
import Inspector from './components/Inspector';
import { setLocale, LocaleProvider } from './i18n';
import { config, profile, model, material } from './test/fixtures';

beforeEach(()=> {setLocale('tr'); vi.stubGlobal('fetch', vi.fn(async()=>new Response(JSON.stringify({materials:[material]}))));});
afterEach(()=>{cleanup();vi.unstubAllGlobals();});
function Settings() {
  const [sub,setSub]=useState<SettingsSub>('setup');
  return <LocaleProvider><MachiningSettings config={config} profiles={[profile,{...profile,id:'custom',builtin:false,name:'Özel makinem'}]} model={model} gcode={null} sub={sub} setSub={setSub} onSaved={()=>{}} onProfiles={()=>{}} onActivate={()=>{}} onError={()=>{}} /></LocaleProvider>;
}
it('localizes built-in materials and machines without changing IDs, edits or custom names', async()=>{
  render(<Settings/>);
  await screen.findByRole('option',{name:'Alüminyum 6061-T6'});
  fireEvent.change(screen.getByLabelText(/Yan pay/),{target:{value:'2.5'}});
  act(()=>setLocale('en'));
  const option=screen.getByRole('option',{name:'Aluminum 6061-T6'}) as HTMLOptionElement;
  expect(option.value).toBe('al6061');
  expect((screen.getByLabelText(/Side allowance/) as HTMLInputElement).value).toBe('2.5');
  fireEvent.click(screen.getByRole('tab',{name:'Machine'}));
  expect(screen.getByText('Özel makinem')).toBeTruthy();
  expect(screen.getAllByText('Vertical machining center — Fanuc 0i-MF style (example)').length).toBeGreaterThan(0);
  fireEvent.click(screen.getByRole('tab',{name:'Tools'}));
  expect(screen.getByText('Özel takım adı')).toBeTruthy();
  fireEvent.click(screen.getByRole('tab',{name:'Operations'}));
  expect(screen.getByText('Face milling')).toBeTruthy();
  expect(screen.getByText('Pocket clearing')).toBeTruthy();
});
it('renders inspector labels in English while preserving a user model name',()=>{
  setLocale('en');
  render(<Inspector tab="model" setTab={()=>{}} model={model} selection={[]} gcode={null} config={config} profiles={[profile]} onProfileChange={()=>{}} activeLine={null} onAsk={()=>{}} onShowLine={()=>{}} onDeleteFeature={()=>{}} onSelectFace={()=>{}} regions={[]} onSelectRegion={()=>{}} onDeselect={()=>{}} onClearSelection={()=>{}} onDeleteRegions={()=>{}} onEngraved={()=>{}} onGenerate={()=>{}} onGcodeLoaded={()=>{}} onConfigSaved={()=>{}} onProfilesChanged={()=>{}} onError={()=>{}} />);
  expect(screen.getByText('Benim parçam')).toBeTruthy();
  expect(screen.getByText('Feature tree')).toBeTruthy();
  expect(screen.getByRole('button',{name:'Machining settings'})).toBeTruthy();
});
