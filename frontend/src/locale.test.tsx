// Real rendering and real api.ts requests; only the network and browser-only APIs are stubbed.
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import ChatPanel from './components/ChatPanel';
import ConversationDrawer from './components/ConversationDrawer';
import Markdown from './components/Markdown';
import { api } from './api';
import { getLocale, LanguageSelector, LocaleProvider, normalizeLocale, readLocale, setLocale, translate, tx } from './i18n';
import { en, tr } from './catalogs';

beforeEach(() => {
  const data = new Map<string, string>();
  vi.stubGlobal('localStorage', {getItem: (k: string) => data.get(k) ?? null, setItem: (k: string, v: string) => data.set(k, v), clear: () => data.clear()});
  vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', {status: 200})));
  HTMLElement.prototype.scrollTo = vi.fn();
  setLocale('tr');
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it('falls back to Turkish for absent, invalid and inaccessible preferences', () => {
  localStorage.clear(); expect(readLocale()).toBe('tr');
  localStorage.setItem('busproje.locale', 'de'); expect(readLocale()).toBe('tr');
  expect(normalizeLocale('EN')).toBe('tr');
  vi.stubGlobal('localStorage', { getItem: () => { throw Error('denied'); }, setItem: () => { throw Error('denied'); } });
  expect(readLocale()).toBe('tr');
  expect(() => setLocale('en')).not.toThrow(); expect(getLocale()).toBe('en');
});
it('persists a valid preference and reads it on reload', () => {
  setLocale('en'); expect(localStorage.getItem('busproje.locale')).toBe('en'); expect(readLocale()).toBe('en');
  setLocale('tr'); expect(readLocale()).toBe('tr');
});
it('uses explicit catalogs with matching placeholders and safe fallback', () => {
  expect(Object.keys(en).sort()).toEqual(Object.keys(tr).sort());
  for (const key of Object.keys(en)) expect(en[key].match(/\{\w+\}/g)?.sort() ?? []).toEqual(tr[key].match(/\{\w+\}/g)?.sort() ?? []);
  expect(translate('en', '{0} bölge silindi.', {0: 3})).toBe('3 regions deleted.');
  expect(translate('tr', '{0} bölge silindi.', {0: 3})).toBe('3 bölge silindi.');
  expect(translate('en', 'Unknown message')).toBe('Unknown message');
  expect(translate('en', 'constructor')).toBe('constructor');
  expect(translate('en', '{0} bölge silindi.')).toBe('{0} regions deleted.');
  expect(translate('en', 'Hata: {0}', {0: '$& <script>'})).toBe('Error: $& <script>');
});
it('links English and Turkish line references without rewriting message text', () => {
  setLocale('en'); const onLine=vi.fn();
  render(<Markdown text="Line 30, satır 20, N10 and hole_1" handlers={{onLine}} />);
  fireEvent.click(screen.getByRole('button', {name:'Line 30'}));
  fireEvent.click(screen.getByRole('button', {name:'satır 20'}));
  expect(onLine.mock.calls).toEqual([[30],[20]]);
  expect(document.querySelector('.md')?.textContent).toBe('Line 30, satır 20, N10 and hole_1');
});
it('attaches Turkish by default to state requests', async () => {
  await api.state();
  expect(new Headers(vi.mocked(fetch).mock.calls[0][1]?.headers).get('Accept-Language')).toBe('tr');
});
it('changes chat UI without changing history, custom titles or an unsent draft', () => {
  const onSend = vi.fn();
  render(<LocaleProvider><LanguageSelector /><ChatPanel messages={[{role:'user', text:'Benim Türkçe mesajım'}, {role:'assistant', text:'Eski Türkçe yanıt G1 X10'}]} busy={false} selection={[]} onRemove={() => {}} onClearSelection={() => {}} onSend={onSend} onReset={() => {}} title="Benim özel başlığım" conversationCount={1} onOpenDrawer={() => {}} /></LocaleProvider>);
  fireEvent.change(screen.getByRole('textbox'), {target: {value: 'çapını 12 mm yap'}});
  fireEvent.change(screen.getByRole('combobox', {name:'Dil'}), {target: {value:'en'}});
  expect(screen.getByRole('button', {name:'Send'})).toBeTruthy();
  expect(screen.getByText('Benim Türkçe mesajım')).toBeTruthy();
  expect(document.querySelector('.msg.assistant .md')?.textContent).toBe('Eski Türkçe yanıt G1 X10');
  expect(screen.getByText('Benim özel başlığım')).toBeTruthy();
  expect((screen.getByRole('textbox') as HTMLTextAreaElement).value).toBe('çapını 12 mm yap');
  expect(document.documentElement.lang).toBe('en');
  fireEvent.click(screen.getByRole('button', {name:'Send'}));
  expect(onSend).toHaveBeenCalledWith('çapını 12 mm yap');
  act(() => setLocale('tr'));
  expect(screen.getByRole('button', {name:'Gönder'})).toBeTruthy();
});
it('updates example commands when language changes', () => {
  render(<LocaleProvider><ChatPanel messages={[]} busy={false} selection={[]} onRemove={() => {}} onClearSelection={() => {}} onSend={() => {}} onReset={() => {}} title="" conversationCount={1} onOpenDrawer={() => {}} /></LocaleProvider>);
  act(() => setLocale('en'));
  fireEvent.click(screen.getByRole('button', {name:'Generate G-code for this part and explain the result.'}));
  expect((screen.getByRole('textbox') as HTMLTextAreaElement).value).toBe('Generate G-code for this part and explain the result.');
});
it('updates memoized conversation groups and confirmation dialogs without translating a custom title', () => {
  render(<LocaleProvider><ConversationDrawer open conversations={[{id:'one', title:'Özel Türkçe başlık', titled:true, created:new Date().toISOString(), updated:new Date().toISOString(), count:1,user_count:1,active:true,preview:'Önceki mesaj'}]} busy={false} onClose={()=>{}} onNew={()=>{}} onSelect={()=>{}} onRename={()=>{}} onDelete={()=>{}} /></LocaleProvider>);
  act(() => setLocale('en'));
  expect(screen.getByText('Today')).toBeTruthy();
  expect(screen.getByText('Özel Türkçe başlık')).toBeTruthy();
  fireEvent.click(screen.getByTitle('Delete'));
  expect(screen.getByText('Delete?')).toBeTruthy();
  expect(screen.getByRole('button', {name:'Cancel'})).toBeTruthy();
});
it('sends the selected locale in new chat requests while preserving message text', async () => {
  setLocale('en'); await api.chat('Türkçe kullanıcı metni', 'f1', 2, null, ['f1'], 'c1');
  const [, init] = vi.mocked(fetch).mock.calls[0];
  expect(new Headers(init?.headers).get('Accept-Language')).toBe('en');
  expect(JSON.parse(init?.body as string)).toMatchObject({language:'en',message:'Türkçe kullanıcı metni',selected_ids:['f1']});
  setLocale('tr'); await api.chat('hello', null, null);
  expect(JSON.parse(vi.mocked(fetch).mock.calls[1][1]?.body as string).language).toBe('tr');
});
it('localizes multipart uploads without supplying a wrong Content-Type boundary', async () => {
  setLocale('en');
  await api.uploadStep(new File(['STEP'], 'parça.step'));
  await api.engraveSvg(new File(['<svg/>'], 'logo.svg'), {depth:1});
  for (const [, init] of vi.mocked(fetch).mock.calls) {
    expect(new Headers(init?.headers).get('Accept-Language')).toBe('en');
    expect(new Headers(init?.headers).has('Content-Type')).toBe(false);
    expect(init?.body).toBeInstanceOf(FormData);
  }
});
it('localizes downloads, including audit exports, and preserves download bytes', async () => {
  setLocale('en'); const code = 'G21\nG1 X10 F200\n(özel)';
  vi.mocked(fetch).mockImplementation(async () => new Response(code));
  const create = vi.fn(()=>'blob:test');
  vi.stubGlobal('URL', class extends URL { static createObjectURL = create; static revokeObjectURL = vi.fn(); });
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(()=>{});
  await api.download('/api/gcode/abc/download','program.nc');
  await api.download('/api/models/current/export.step','model.step');
  await api.auditDownload(['abc']);
  for (const [, init] of vi.mocked(fetch).mock.calls) expect(new Headers(init?.headers).get('Accept-Language')).toBe('en');
  const downloaded = (create.mock.calls[0] as unknown as [Blob])[0];
  const text = await new Promise(resolve => { const reader = new FileReader(); reader.onload = () => resolve(reader.result); reader.readAsText(downloaded); });
  expect(text).toBe(code);
});
it('preserves server-supplied download filenames, including Unicode', async () => {
  const filenames: string[] = [];
  vi.stubGlobal('URL', class extends URL { static createObjectURL = () => 'blob:test'; static revokeObjectURL = vi.fn(); });
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function(this: HTMLAnchorElement) { filenames.push(this.download); });
  vi.mocked(fetch).mockResolvedValueOnce(new Response('STEP', {headers:{'Content-Disposition': "attachment; filename*=utf-8''par%C3%A7a.step"}}));
  await api.download('/api/models/current/export.step','model.step');
  expect(filenames).toEqual(['parça.step']);
});

it('keeps translator identity stable across language changes', () => {
  const previous = tx; setLocale('en'); expect(tx).toBe(previous); expect(tx('Gönder')).toBe('Send');
});
