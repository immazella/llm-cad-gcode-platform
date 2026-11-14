import { tx } from './i18n';
import { en } from './catalogs';
import { isRegion, type Material, type MachineProfile, type Selection } from './types';

/** Display adapters only. Never use these values in request bodies or stored identifiers. */
const materialNames: Record<string, string> = {
  al6061:'Alüminyum 6061-T6', al7075:'Alüminyum 7075-T6', brass:'Pirinç (CuZn39Pb3)',
  steel_mild:'Yapı çeliği (S235 / 1018)', steel_alloy:'Alaşımlı çelik (42CrMo4 / 4140, ~30 HRC)',
  stainless:'Paslanmaz çelik (304)', cast_iron:'Gri dökme demir (GG25)', pom:'POM (Delrin)', acrylic:'Akrilik (PMMA)', mdf:'MDF / ahşap',
};
export const materialLabel = (m: Pick<Material,'id'|'name'>) => {
  const key = materialNames[m.id];
  return key && [key, en[key], m.id].includes(m.name) ? tx(key) : m.name;
};
/** Only call for system-owned metadata, never for editable names or conversation text. */
export const builtinText = (value: string) => tx(Object.keys(en).find(key => en[key] === value) ?? value);
export function roleLabel(value: string): string {
  const hole = /^(hole_\d+) deliğinin iç yüzeyi \(Ø([\d.]+) mm\)$/.exec(value);
  if (hole) return tx('{0} deliğinin iç yüzeyi (Ø{1} mm)', {0:hole[1],1:hole[2]});
  const pocket = /^(pocket_\d+) cebinin tabanı$/.exec(value);
  if (pocket) return tx('{0} cebinin tabanı', {0:pocket[1]});
  const feature = /^(\w+_\d+) \([a-z]+\) özelliğine ait yüzey$/.exec(value);
  if (feature) return tx('{0} özelliğine ait yüzey',{0:feature[1]});
  const surface = /^([a-z]+) yüzey$/.exec(value);
  if (surface) return tx('{0} yüzey',{0:enumLabel(surface[1])});
  return builtinText(value);
}
export const materialGroup = (group: string, fallback: string) => tx(({P:'çelik',M:'paslanmaz',K:'dökme demir',N:'demir dışı',O:'plastik / ahşap'} as Record<string,string>)[group] ?? fallback);
const profileNames: Record<string,string> = {
  fanuc_vmc:'Dik işleme merkezi — Fanuc 0i-MF tarzı (örnek)',
  linuxcnc_mill:'Küçük freze — LinuxCNC (örnek)',
  grbl_router:'Masaüstü CNC router — GRBL 1.1 (örnek)',
};
export const profileLabel = (p: Pick<MachineProfile,'id'|'name'|'builtin'>) => p.builtin ? tx(profileNames[p.id] ?? p.name) : p.name;
export const enumLabel = (id: string): string => tx(({
  plane:'Düzlem', cylinder:'Silindir', cone:'Koni', sphere:'Küre', torus:'Torus', bspline:'B-spline', bezier:'Bézier', other:'Diğer',
  rect:'Dikdörtgen', circle:'Daire', lasso:'Serbest çizim', polygon:'Çokgen',
  hole:'Delik', pocket:'Cep', fillet:'Kavis', chamfer:'Pah', engrave:'Kazıma', box:'Kutu',
} as Record<string,string>)[id] ?? id);
export function selectionLabel(s: Selection): string {
  if (isRegion(s)) return tx('{0} bölgesi · {1}', {0:enumLabel(s.shape),1:s.face_id});
  if (s.hole_id) return tx('{0} deliğinin iç yüzeyi (Ø{1} mm)',{0:s.hole_id,1:s.diameter});
  if (s.pocket_id) return tx('{0} cebinin tabanı',{0:s.pocket_id});
  if (s.feature_id) return tx('{0} özelliğine ait yüzey',{0:s.feature_id});
  if (s.surface === 'plane' && s.normal) {
    if (s.normal[2] > 0.9) return tx('parçanın üst yüzeyi');
    if (s.normal[2] < -0.9) return tx('parçanın alt yüzeyi');
    return tx('yan / iç düzlemsel yüzey');
  }
  return tx('{0} yüzey',{0:enumLabel(s.surface)});
}
