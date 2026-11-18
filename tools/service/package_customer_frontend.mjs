// Publish the static Next.js customer app into cloud/public with its exact inline-script CSP hashes.
import {readFile,writeFile,mkdir,cp,readdir} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
const root=fileURLToPath(new URL('../../',import.meta.url));
const source=path.join(root,'frontend/out'),target=path.join(root,'cloud/public');
await cp(source,target,{recursive:true});
// Static Next documents contain inline hydration scripts. Authorize their exact
// bytes with hashes, never an unrestricted inline-script policy.
const policies={};
for(const file of await readdir(source)){
 if(!file.endsWith('.html'))continue;
 const html=await readFile(path.join(source,file),'utf8');
 const hashes=[...html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/g)]
  .filter(m=>! /\bsrc\s*=/.test(m[1])&&m[2])
  .map(m=>`'sha256-${createHash('sha256').update(m[2]).digest('base64')}'`);
 const page=file==='index.html'?'/':'/'+file.slice(0,-5);
 policies[page]=[...new Set(hashes)];
}
await mkdir(path.join(root,'cloud/generated'),{recursive:true});
await writeFile(path.join(root,'cloud/generated/frontend-csp.json'),JSON.stringify(policies,null,2)+'\n');
console.log('Customer app packaged; exact hydration CSP hashes generated.');
