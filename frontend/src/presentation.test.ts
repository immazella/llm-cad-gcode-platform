import { afterEach, expect, it, vi } from 'vitest';
import { setLocale } from './i18n';
import { enumLabel, materialLabel, profileLabel, selectionLabel } from './presentation';
import { UNITS, fmtLen, getUnit, setUnit } from './units';
import { material, profile } from './test/fixtures';
import type { FaceInfo } from './types';
afterEach(()=>vi.unstubAllGlobals());

it('translates default display metadata without mutating data or custom names',()=>{
  const source=structuredClone({material,profile});setLocale('en');
  expect(materialLabel(material)).toBe('Aluminum 6061-T6');
  expect(materialLabel({...material,name:'Benim alaşımım'})).toBe('Benim alaşımım');
  expect(profileLabel(profile)).toBe('Vertical machining center — Fanuc 0i-MF style (example)');
  expect(profileLabel({...profile,builtin:false})).toBe(profile.name);
  expect({material,profile}).toEqual(source);
  expect(enumLabel('pocket')).toBe('Pocket'); expect(enumLabel('unknown-id')).toBe('unknown-id');
  setLocale('tr'); expect(enumLabel('pocket')).toBe('Cep');
});
it('changes units names but never unit selection, conversion or symbols',()=>{
  setUnit('in');setLocale('tr');const formatted=fmtLen(25.4,'in');
  expect(UNITS.in.name).toBe('İnç');
  setLocale('en');expect(UNITS.in.name).toBe('Inch');
  expect(getUnit()).toBe('in');expect(fmtLen(25.4,'in')).toBe(formatted);expect(UNITS.in.label).toBe('in');
});
it('localizes selected geometry labels from structured data without changing IDs',()=>{
  const face:FaceInfo={face_id:'f1',surface:'plane',area:10,center:[0,0,0],normal:[0,0,1],role:'parçanın üst yüzeyi',hole_id:null,feature_id:null,pocket_id:null};
  setLocale('en');expect(selectionLabel(face)).toBe('top face of the part');expect(face.face_id).toBe('f1');expect(face.role).toBe('parçanın üst yüzeyi');
  setLocale('tr');expect(selectionLabel(face)).toBe('parçanın üst yüzeyi');
});
it('loads the persisted locale when the module starts after a refresh',async()=>{
  localStorage.setItem('busproje.locale','en');vi.resetModules();
  const reloaded=await import('./i18n');expect(reloaded.getLocale()).toBe('en');
});
