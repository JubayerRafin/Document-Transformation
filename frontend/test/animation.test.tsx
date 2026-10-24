import {it,expect,vi} from 'vitest';
import {render,screen,waitFor} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
const animation=vi.hoisted(()=>({from:vi.fn(),to:vi.fn(),revert:vi.fn(),contextRevert:vi.fn()}));
vi.mock('gsap',()=>({gsap:{registerPlugin:vi.fn(),from:animation.from,to:animation.to,utils:{toArray:()=>[]},context:(fn:()=>void)=>{fn();return {revert:animation.contextRevert};},matchMedia:()=>({add:(_query:string,fn:()=>void)=>fn(),revert:animation.revert})}}));
vi.mock('gsap/ScrollTrigger',()=>({ScrollTrigger:{}}));
import {Landing} from '../src/components/landing';
function preference(reduced:boolean){vi.spyOn(window,'matchMedia').mockImplementation(query=>({matches:reduced,media:query,onchange:null,addListener:vi.fn(),removeListener:vi.fn(),addEventListener:vi.fn(),removeEventListener:vi.fn(),dispatchEvent:vi.fn(()=>true)}));}
it('initializes scroll animation and cleans it up when decorative motion is paused',async()=>{
 preference(false);const u=userEvent.setup();render(<Landing/>);
 await waitFor(()=>expect(animation.from).toHaveBeenCalledWith('.tx-hero-enter',expect.objectContaining({stagger:.1})));
 expect(animation.to).toHaveBeenCalledWith('.tx-scroll-progress',expect.objectContaining({scrollTrigger:expect.objectContaining({end:'max'})}));
 await u.click(screen.getByRole('button',{name:'Pause decorative motion'}));
 expect(animation.revert).toHaveBeenCalled();
 expect(screen.getByRole('button',{name:'Resume decorative motion'}).closest('.tx-marketing')).toHaveClass('tx-motion-paused');
});
it('does not initialize GSAP when reduced motion is requested',async()=>{
 preference(true);render(<Landing/>);await Promise.resolve();
 expect(animation.from).not.toHaveBeenCalled();expect(animation.to).not.toHaveBeenCalled();
 expect(screen.getByRole('heading',{level:1})).toBeVisible();
});
