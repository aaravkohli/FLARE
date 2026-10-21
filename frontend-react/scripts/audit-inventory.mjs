// Syntax inventory only: symbols are not marked verified from mere execution.
import fs from 'node:fs';
import path from 'node:path';
import ts from 'typescript';
const output=process.argv[2];if(!output)throw new Error('Supply audit inventory directory');
fs.mkdirSync(output,{recursive:true});const rows=[],calls=[];
function walk(folder){for(const name of fs.readdirSync(folder)){const file=path.join(folder,name);if(fs.statSync(file).isDirectory())walk(file);else if(/\.(tsx?|mjs)$/.test(name)){
 const text=fs.readFileSync(file,'utf8'),source=ts.createSourceFile(file,text,ts.ScriptTarget.Latest,true);let parents=[];
 function visit(node){if(ts.isClassDeclaration(node)||ts.isClassExpression(node)){parents.push(node.name?.getText(source)||`class@${source.getLineAndCharacterOfPosition(node.getStart()).line+1}`);ts.forEachChild(node,visit);parents.pop();return;}const isFunction=ts.isFunctionDeclaration(node)||ts.isMethodDeclaration(node)||ts.isArrowFunction(node)||ts.isFunctionExpression(node)||ts.isGetAccessorDeclaration(node)||ts.isSetAccessorDeclaration(node)||ts.isConstructorDeclaration(node);let name;
  if(isFunction){name=node.name?.getText(source);if(!name&&ts.isVariableDeclaration(node.parent))name=node.parent.name.getText(source);if(!name&&ts.isPropertyAssignment(node.parent))name=node.parent.name.getText(source);name??=`anonymous@${source.getLineAndCharacterOfPosition(node.getStart()).line+1}:${source.getLineAndCharacterOfPosition(node.getStart()).character}`;parents.push(name);
   rows.push({id:`frontend-react/${file}:${parents.join('.')}`,file:`frontend-react/${file}`,symbol:parents.join('.'),start:node.getStart(),end:node.end,line:source.getLineAndCharacterOfPosition(node.getStart()).line+1,surface:file.includes('.test.')||file.startsWith('e2e/')?'test':'frontend',status:'Not Tested',evidence:[]});}
  if(ts.isCallExpression(node))calls.push({file:`frontend-react/${file}`,line:source.getLineAndCharacterOfPosition(node.getStart()).line+1,caller:parents.join('.'),target:node.expression.getText(source)});
  ts.forEachChild(node,visit);if(isFunction)parents.pop();
 }visit(source);
}}}
walk('src');walk('e2e');walk('scripts');for(const [name,data] of [['frontend_functions',rows],['frontend_calls',calls]])fs.writeFileSync(path.join(output,name+'.json'),JSON.stringify(data,null,2));console.log(JSON.stringify({functions:rows.length,calls:calls.length}));
