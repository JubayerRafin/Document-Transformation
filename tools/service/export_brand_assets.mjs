import {createRequire} from 'node:module';
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
const root=fileURLToPath(new URL('../../',import.meta.url));
const require=createRequire(new URL('../../frontend/package.json',import.meta.url));
const sharp=require('sharp');
const dir=path.resolve(process.argv[2]||path.join(root,'output/transformx-brand'));
await mkdir(dir,{recursive:true});
const mark=await readFile(path.join(root,'frontend/public/transformx-mark.svg'),'utf8');
await writeFile(path.join(dir,'TransformX-Mark.svg'),mark);
await sharp(Buffer.from(mark)).resize(1024,1024).png().toFile(path.join(dir,'TransformX-Mark.png'));
const content=mark.slice(mark.indexOf('<defs>'),mark.lastIndexOf('</svg>'));
for(const [variant,ink] of [['Dark','#edf6fc'],['Light','#0b2639']]){
 const wordmark=`<svg xmlns="http://www.w3.org/2000/svg" width="480" height="120" viewBox="0 0 480 120"><g transform="translate(5 10)">${content}</g><text x="121" y="76" fill="${ink}" font-family="Arial,Segoe UI,sans-serif" font-size="52" font-weight="700" letter-spacing="-2">Transform<tspan fill="#0096d6">X</tspan></text></svg>`;
 await writeFile(path.join(dir,`TransformX-Wordmark-${variant}.svg`),wordmark);
 await sharp(Buffer.from(wordmark)).resize(1440,360).png().toFile(path.join(dir,`TransformX-Wordmark-${variant}.png`));
}
const mono=mark.replace(/<defs>[\s\S]*?<\/defs>/,'').replace(/fill="(?:url\([^)]+\)|#[0-9a-fA-F]+)"/g,'fill="#0b2639"').replace(/stroke="#[0-9a-fA-F]+"/g,'stroke="#0b2639"').replace(/opacity="[^"]+"/g,'opacity="1"');
await writeFile(path.join(dir,'TransformX-Mark-Monochrome.svg'),mono);
const swatches=[['Midnight','#07131f'],['Surface','#0d2032'],['HP-inspired blue','#0096d6'],['Sky / actions','#54c7f4'],['Cyan','#6fe3f2'],['Paper','#edf6fc']];
const board=`<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="800" viewBox="0 0 1280 800"><rect width="1280" height="800" fill="#07131f"/><text x="65" y="72" fill="#8bb6cb" font-family="Arial" font-size="14" letter-spacing="4">TRANSFORMX / BRAND SYSTEM</text><text x="65" y="129" fill="#edf6fc" font-family="Arial" font-weight="700" font-size="38">A new identity. Built around your documents.</text><rect x="60" y="178" width="560" height="337" rx="20" fill="#0d2032" stroke="#29455c"/><rect x="646" y="178" width="574" height="337" rx="20" fill="#edf6fc"/><g transform="translate(105 210) scale(2.25)">${content}</g><text x="340" y="357" fill="#edf6fc" font-family="Arial" font-weight="700" font-size="32" letter-spacing="-1">Transform<tspan fill="#54c7f4">X</tspan></text><g transform="translate(690 212) scale(2.2)">${content.replaceAll('id="paper"','id="paper2"').replaceAll('id="ribbon"','id="ribbon2"').replaceAll('url(#paper)','url(#paper2)').replaceAll('url(#ribbon)','url(#ribbon2)')}</g><text x="920" y="357" fill="#0b2639" font-family="Arial" font-weight="700" font-size="32" letter-spacing="-1">Transform<tspan fill="#0096d6">X</tspan></text>${swatches.map(([name,color],i)=>`<rect x="${60+i*196}" y="560" width="176" height="90" rx="11" fill="${color}" stroke="#29455c"/><text x="${62+i*196}" y="682" font-family="Arial" font-size="13" fill="#edf6fc">${name}</text><text x="${62+i*196}" y="706" font-family="monospace" font-size="13" fill="#a9bfce">${color.toUpperCase()}</text>`).join('')}<text x="64" y="763" fill="#a9bfce" font-family="Arial" font-size="14">Document layers + a folded X + transformation pixels. Scalable SVG masters and transparent PNG exports.</text></svg>`;
await writeFile(path.join(dir,'TransformX-Brand-Board.svg'),board);
await sharp(Buffer.from(board)).png().toFile(path.join(dir,'TransformX-Brand-Board.png'));
await writeFile(path.join(dir,'Brand-Guide.txt'),'TransformX brand system\n\nOriginal vector document/X mark, inspired by the supplied visual reference.\nPalette: dark navy #07131F, surface #0D2032, HP-inspired blue #0096D6, action sky #54C7F4, cyan #6FE3F2, paper #EDF6FC.\nUse dark text #051A29 on sky action buttons. Use #EDF6FC on dark surfaces.\nSVG masters and transparent PNG exports are included. Wordmark Dark uses light lettering; Wordmark Light uses dark lettering.\nSmall UI: mark at 40px. Avoid heavy glow at small sizes.\nReference for palette direction: https://www.hp.com/us-en/hp-information/brandcentral/our-visual-identity.html\n');
console.log('Brand assets exported to '+dir);
