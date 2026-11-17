import {it,expect,vi,beforeEach} from 'vitest';
import {render,waitFor,act} from '@testing-library/react';
const scene=vi.hoisted(()=>({create:vi.fn(),pause:vi.fn(),dispose:vi.fn()}));
vi.mock('../src/components/document-scene',()=>({createDocumentScene:scene.create}));
import {HeroDocument} from '../src/components/hero-document';
beforeEach(()=>{vi.clearAllMocks();scene.create.mockReturnValue({setPaused:scene.pause,dispose:scene.dispose});});
function media(matches:boolean){
 let change:()=>void=()=>{};
 const preference={matches,media:'',onchange:null,addListener:vi.fn(),removeListener:vi.fn(),addEventListener:vi.fn((_name:string,fn:()=>void)=>{change=fn;}),removeEventListener:vi.fn(),dispatchEvent:vi.fn(()=>true)};
 vi.spyOn(window,'matchMedia').mockReturnValue(preference);
 return {preference,change:()=>change()};
}
it('keeps the static illustration and does not create WebGL for narrow/reduced-motion media',async()=>{
 media(false);const {container}=render(<HeroDocument paused={false}/>);
 expect(container.querySelector('[data-renderer="static"]')).toBeInTheDocument();
 await Promise.resolve();expect(scene.create).not.toHaveBeenCalled();
});
it('connects pause and disposes the scene when unmounted',async()=>{
 media(true);const {container,rerender,unmount}=render(<HeroDocument paused={false}/>);
 await waitFor(()=>expect(container.querySelector('[data-renderer="webgl"]')).toBeInTheDocument());
 rerender(<HeroDocument paused/>);expect(scene.pause).toHaveBeenLastCalledWith(true);
 unmount();expect(scene.dispose).toHaveBeenCalledTimes(1);
});
it('releases WebGL when the viewport or motion preference changes',async()=>{
 const m=media(true);const {container}=render(<HeroDocument paused={false}/>);
 await waitFor(()=>expect(scene.create).toHaveBeenCalledTimes(1));
 act(()=>{m.preference.matches=false;m.change();});
 expect(scene.dispose).toHaveBeenCalledTimes(1);
 expect(container.querySelector('[data-renderer="static"]')).toBeInTheDocument();
});
it('retains the illustration when WebGL is unavailable',async()=>{
 media(true);scene.create.mockReturnValue(null);const {container}=render(<HeroDocument paused={false}/>);
 await waitFor(()=>expect(scene.create).toHaveBeenCalledTimes(1));
 expect(container.querySelector('[data-renderer="static"] svg')).toBeInTheDocument();
});
