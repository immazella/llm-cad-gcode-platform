import ts from 'typescript';
import { expect, it } from 'vitest';
import { en } from './catalogs';

const sources = import.meta.glob<string>(['./App.tsx','./api.ts','./units.ts','./components/*.tsx'], {eager:true,query:'?raw',import:'default'});
it('has catalog entries for every explicit UI key and no residual Turkish UI literals',()=>{
  const errors:string[]=[];
  for (const [file,source] of Object.entries(sources)) {
    const ast=ts.createSourceFile(file,source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
    function visit(node:ts.Node) {
      if (ts.isCallExpression(node)&&node.expression.getText(ast)==='tx') {
        const key=node.arguments[0];
        if (key&&ts.isStringLiteral(key)&&!Object.prototype.hasOwnProperty.call(en,key.text)) errors.push(`${file}: missing key ${key.text}`);
        node.arguments.slice(1).forEach(visit); return;
      }
      if(ts.isTaggedTemplateExpression(node)&&node.tag.getText(ast)==='String.raw')return; // markdown grammar, not UI
      if(ts.isStringLiteral(node)||ts.isJsxText(node)||ts.isTemplateLiteralToken(node)) {
        const value=node.text;
        // This is editable engraving content, not a UI label.
        if (file.endsWith('Inspector.tsx')&&value==='İTÜ')return;
        if(/[çğıöşüÇĞİÖŞÜ]/.test(value)) errors.push(`${file}:${ast.getLineAndCharacterOfPosition(node.getStart()).line+1} ${value}`);
      }
      ts.forEachChild(node,visit);
    }
    visit(ast);
  }
  expect(Object.keys(sources).length).toBeGreaterThan(10);
  expect(errors).toEqual([]);
});
