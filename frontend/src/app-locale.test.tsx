import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { MeshData } from './types';
import { config, model, profile } from './test/fixtures';
import App from './App';
import { LocaleProvider, setLocale } from './i18n';

// WebGL is outside jsdom; all application state and API code is real.
vi.mock('./components/ModelViewer',()=>({default:({mesh,selectedIds,onSelect}:{mesh:MeshData|null;selectedIds:Set<string>;onSelect:(id:string)=>void})=><button data-testid="cad-state" onClick={()=>onSelect('f1')}>{mesh?.model_id}:{[...selectedIds].join(',')}</button>}));
vi.mock('./components/ToolpathViewer',()=>({default:()=>null}));
afterEach(()=>{cleanup();vi.unstubAllGlobals();});

it('switches language without reloading the workspace, dropping selection, or changing G-code/history',async()=>{
  setLocale('tr'); HTMLElement.prototype.scrollTo=vi.fn();
  const code='G21\nG1 X10 F200\n(Özel açıklama)';
  const record={gcode_id:'g1',source:'generated',model_id:'m1',model_version:1,plan:[],warnings:[],line_count:3,stale:false,generated_for:'fanuc_vmc',post:'fanuc',text:code,validation:{valid:true,counts:{error:0,warning:0,info:0},levels:{},issues:[],profile,setup:{work_offset:[0,0,0]},not_checked:[],stats:{line_count:3,estimated_time_s:1,tool_changes:0,tool_change_time_s:0},disclaimer:'Türkçe doğrulama açıklaması'}};
  const mesh={model_id:'m1',version:1,summary:model,faces:[],edges:[]};
  const network=vi.fn(async(url:RequestInfo|URL,init?:RequestInit)=>{
    const path=String(url);const english=new Headers(init?.headers).get('Accept-Language')==='en';
    const data=path==='/api/state'?{config,model,gcode:record,llm:{provider:'offline',available:false},ui:{messages:[{role:'assistant',text:'Eski Türkçe cevap'}],tab:'gcode'},external:{rev:0}}
      :path==='/api/profiles'?[profile]
      :path==='/api/models/current/mesh'?mesh
      :path.startsWith('/api/models/current/faces/')?{face_id:'f1',surface:'plane',area:10,center:[0,0,10],normal:[0,0,1],role:'parçanın üst yüzeyi',feature_id:null,hole_id:null,pocket_id:null}
      :path==='/api/models/current/regions'?{regions:[],dropped:[]}
      :path==='/api/conversations'?{conversations:[],active_id:'c1'}
      :path==='/api/gcode/g1'?{...record,validation:{...record.validation,disclaimer:english?'English validation explanation':record.validation.disclaimer}}
      :path==='/api/gcode/g1/toolpath'?{segments:[],stats:{}}
      :path==='/api/materials'?{materials:[]}:{};
    return new Response(JSON.stringify(data));
  });vi.stubGlobal('fetch',network);
  render(<LocaleProvider><App/></LocaleProvider>);
  await screen.findByText('Türkçe doğrulama açıklaması');
  fireEvent.click(screen.getByTestId('cad-state'));
  await waitFor(()=>expect(screen.getByTestId('cad-state').textContent).toBe('m1:f1'));
  const savedCode=document.querySelector('.code')?.textContent;
  act(()=>setLocale('en'));
  expect(screen.getByTestId('cad-state').textContent).toBe('m1:f1');
  expect(screen.getByText('Eski Türkçe cevap')).toBeTruthy();
  expect(document.querySelector('.code')?.textContent).toBe(savedCode);
  expect(network.mock.calls.filter(([url])=>url==='/api/state')).toHaveLength(1);
  expect(network.mock.calls.filter(([url])=>url==='/api/models/current/mesh')).toHaveLength(1);
  await screen.findByText('English validation explanation');
  expect(document.querySelector('.code')?.textContent).toBe(savedCode);
});
