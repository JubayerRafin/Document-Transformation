import {it,expect,vi} from 'vitest';
import {render,screen} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {Landing} from '../src/components/landing';
import {ApiClient,ApiError} from '../src/lib/api';
function setup(){vi.spyOn(ApiClient.prototype,'me').mockRejectedValue(new ApiError('SIGN_IN_REQUIRED',401));vi.spyOn(window,'matchMedia').mockImplementation(query=>({matches:true,media:query,onchange:null,addListener:vi.fn(),removeListener:vi.fn(),addEventListener:vi.fn(),removeEventListener:vi.fn(),dispatchEvent:vi.fn(()=>true)}));render(<Landing/>);return userEvent.setup();}
it('switches the fictional product illustration without uploading a document',async()=>{
 const u=setup(),fetchSpy=vi.spyOn(globalThis,'fetch');
 expect(screen.getByRole('button',{name:'Translate'})).toHaveAttribute('aria-pressed','true');
 await u.click(screen.getByRole('button',{name:'Redact'}));expect(screen.getByLabelText('Example name redacted')).toBeInTheDocument();expect(screen.getByLabelText('Example email redacted')).toBeInTheDocument();
 await u.click(screen.getByRole('button',{name:'Review'}));expect(screen.getByText('Check text & layout')).toBeInTheDocument();expect(screen.queryByLabelText('Example name redacted')).not.toBeInTheDocument();
 expect(fetchSpy).not.toHaveBeenCalled();expect(screen.getByText(/No file is uploaded or processed here/)).toBeInTheDocument();
});
it('provides an explicit decorative motion pause and resume control',async()=>{const u=setup();await u.click(screen.getByRole('button',{name:'Pause decorative motion'}));expect(screen.getByRole('button',{name:'Resume decorative motion'})).toHaveAttribute('aria-pressed','true');await u.click(screen.getByRole('button',{name:'Resume decorative motion'}));expect(screen.getByRole('button',{name:'Pause decorative motion'})).toHaveAttribute('aria-pressed','false');});
it('retains working navigation to the existing workspace and Studio',async()=>{const u=setup();expect(screen.getByRole('link',{name:'Transform your document'})).toHaveAttribute('href','/workspace');expect(screen.getByRole('link',{name:'Explore Studio'})).toHaveAttribute('href','/studio');await u.click(screen.getByRole('button',{name:'Open menu'}));expect(screen.getByRole('navigation',{name:'Mobile navigation'})).toBeInTheDocument();await u.click(screen.getByRole('button',{name:'Close menu'}));expect(screen.queryByRole('navigation',{name:'Mobile navigation'})).not.toBeInTheDocument();});
