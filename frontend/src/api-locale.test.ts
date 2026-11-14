import { afterEach, expect, it, vi } from 'vitest';
import { api } from './api';
import { setLocale } from './i18n';
import { config, profile } from './test/fixtures';

const requests: Record<keyof typeof api, () => unknown> = {
  tool:()=>api.tool('get_geometry_info'),state:()=>api.state(),mesh:()=>api.mesh(),face:()=>api.face('f1',1),primitive:()=>api.primitive({type:'box'}),
  uploadStep:()=>api.uploadStep(new File(['STEP'],'part.step')),engraveText:()=>api.engraveText({text:'özel'}),engraveSvg:()=>api.engraveSvg(new File(['<svg/>'],'logo.svg'),{depth:1}),undo:()=>api.undo(),deleteFeature:()=>api.deleteFeature('hole_1'),
  regions:()=>api.regions(),createRegion:()=>api.createRegion({shape:'rect',face_id:'f1',model_version:1,anchor_point:[0,0,0]}),deleteRegion:()=>api.deleteRegion('r1'),
  conversations:()=>api.conversations(),newConversation:()=>api.newConversation(),activateConversation:()=>api.activateConversation('c1'),renameConversation:()=>api.renameConversation('c1','Özel ad'),deleteConversation:()=>api.deleteConversation('c1'),chat:()=>api.chat('hi',null,null),
  auditList:()=>api.auditList({}),auditIds:()=>api.auditIds({}),auditEntry:()=>api.auditEntry('a1'),auditDownload:()=>api.auditDownload(null),
  chatReset:()=>api.chatReset(),download:()=>api.download('/api/models/current/export.step','model.step'),putUi:()=>api.putUi({messages:[]}),putSelection:()=>api.putSelection('f1',1),sessionReset:()=>api.sessionReset(),setLlmMode:()=>api.setLlmMode(true),
  getConfig:()=>api.getConfig(),putConfig:()=>api.putConfig(config),profiles:()=>api.profiles(),setProfile:()=>api.setProfile('fanuc_vmc'),generate:()=>api.generate(['pocket']),gcode:()=>api.gcode('g1'),validate:()=>api.validate('g1'),gcodeText:()=>api.gcodeText('G21\nG1 X1','özel.nc'),toolpath:()=>api.toolpath('g1'),materials:()=>api.materials(),recommend:()=>api.recommend({diameter:6}),saveMachine:()=>api.saveMachine(profile),deleteMachine:()=>api.deleteMachine('custom'),
};
afterEach(()=>{vi.unstubAllGlobals();vi.restoreAllMocks();});
it.each(['tr','en'] as const)('all API methods attach %s without altering canonical request data', async(locale)=>{
  setLocale(locale);
  const fetcher=vi.fn(async()=>new Response('{}'));vi.stubGlobal('fetch',fetcher);
  vi.stubGlobal('URL',class extends URL {static createObjectURL=()=> 'blob:test';static revokeObjectURL=vi.fn();});
  vi.spyOn(HTMLAnchorElement.prototype,'click').mockImplementation(()=>{});
  expect(Object.keys(requests).sort()).toEqual(Object.keys(api).sort());
  for(const [method,request] of Object.entries(requests)) {
    const before=fetcher.mock.calls.length; await request();
    expect(fetcher.mock.calls.length,method).toBe(before+1);
    const [,init]=vi.mocked(fetch).mock.calls[before];
    expect(new Headers(init?.headers).get('Accept-Language'),method).toBe(locale);
  }
  const configRequest=vi.mocked(fetch).mock.calls.find(([url,init])=>url==='/api/config'&&init?.method==='PUT');
  expect(JSON.parse(configRequest?.[1]?.body as string)).toEqual(config);
  const codeRequest=vi.mocked(fetch).mock.calls.find(([url])=>url==='/api/gcode/text');
  expect(JSON.parse(codeRequest?.[1]?.body as string)).toEqual({text:'G21\nG1 X1',name:'özel.nc'});
});
it('renders selected-language fallback errors but preserves server error details',async()=>{
  setLocale('en');
  const fetcher=vi.fn(async()=>new Response(JSON.stringify({ok:false})));vi.stubGlobal('fetch',fetcher);
  await expect(api.tool('x')).rejects.toThrow('Operation failed.');
  fetcher.mockImplementation(async()=>new Response(JSON.stringify({detail:'Server supplied error'}),{status:400}));
  await expect(api.state()).rejects.toThrow('Server supplied error');
  fetcher.mockImplementation(async()=>new Response('',{status:500}));
  await expect(api.download('/api/gcode/g1/download','x.nc')).rejects.toThrow('Download failed: 500');
});
