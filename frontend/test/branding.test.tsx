import {it,expect} from 'vitest';
import {readFileSync} from 'node:fs';
import path from 'node:path';
import {render,screen} from '@testing-library/react';
import {Brand} from '../src/components/brand';
const css=readFileSync(path.resolve(process.cwd(),'src/app/globals.css'),'utf8');
function token(name:string){const value=css.match(new RegExp('--'+name+':\\s*(#[0-9a-fA-F]{6})'))?.[1];if(!value)throw Error('Missing color token: '+name);return value;}
function luminance(hex:string){const [r,g,b]=[1,3,5].map(i=>parseInt(hex.slice(i,i+2),16)/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4);return .2126*r+.7152*g+.0722*b;}
function contrast(a:string,b:string){const x=luminance(a),y=luminance(b);return (Math.max(x,y)+.05)/(Math.min(x,y)+.05);}
it('brand text/action colors meet WCAG AA contrast on their intended surfaces',()=>{
 for(const [text,background] of [['foreground','background'],['muted-foreground','background'],['muted-foreground','muted'],['primary','background'],['primary-foreground','primary']])expect(contrast(token(text),token(background)),text+' on '+background).toBeGreaterThanOrEqual(4.5);
 expect(contrast(token('ring'),token('background'))).toBeGreaterThanOrEqual(3);
});
it('uses our own scalable mark without changing accessible home navigation',()=>{
 const {container}=render(<Brand/>);expect(screen.getByRole('link',{name:'TransformX home'})).toHaveAttribute('href','/');
 const mark=container.querySelector('img')!;expect(mark).toHaveAttribute('src','/transformx-mark.svg');expect(mark).toHaveAttribute('alt','');expect(mark).toHaveAttribute('width','40');expect(mark).toHaveAttribute('height','40');
});
