import {it,expect,vi} from 'vitest';
import {renderHook,waitFor,act,render,screen,within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {ApiClient,ApiError,type Account} from '../src/lib/api';
import {useLandingSession} from '../src/hooks/use-landing-session';
import {Landing} from '../src/components/landing';
const account:Account={name:'test@example.test',role:'admin',csrf:'test-csrf'};
it('reads the existing session instead of assuming a guest',async()=>{
 vi.spyOn(ApiClient.prototype,'me').mockResolvedValue(account);
 const {result}=renderHook(()=>useLandingSession());
 await waitFor(()=>expect(result.current.session).toBe('signed-in'));
});
it('treats HTTP 401 as signed out',async()=>{
 vi.spyOn(ApiClient.prototype,'me').mockRejectedValue(new ApiError('SIGN_IN_REQUIRED',401));
 const {result}=renderHook(()=>useLandingSession());
 await waitFor(()=>expect(result.current.session).toBe('guest'));
});
it('does not label a network outage as a signed-out session',async()=>{
 const me=vi.spyOn(ApiClient.prototype,'me').mockRejectedValue(new ApiError('NETWORK_ERROR',0));
 const {result}=renderHook(()=>useLandingSession());
 await waitFor(()=>expect(me).toHaveBeenCalledTimes(1));expect(result.current.session).toBe('checking');
});
it('refreshes after sign-in in another tab and sign-out when returning from navigation',async()=>{
 const me=vi.spyOn(ApiClient.prototype,'me').mockRejectedValueOnce(new ApiError('SIGN_IN_REQUIRED',401)).mockResolvedValueOnce(account).mockRejectedValueOnce(new ApiError('SIGN_IN_REQUIRED',401));
 const {result}=renderHook(()=>useLandingSession());
 await waitFor(()=>expect(result.current.session).toBe('guest'));
 act(()=>window.dispatchEvent(new Event('focus')));
 await waitFor(()=>expect(result.current.session).toBe('signed-in'));
 act(()=>window.dispatchEvent(new Event('pageshow')));
 await waitFor(()=>expect(result.current.session).toBe('guest'));expect(me).toHaveBeenCalledTimes(3);
});
it('ignores an older signed-in response after a newer signed-out check',async()=>{
 let finish!:(value:Account)=>void;
 vi.spyOn(ApiClient.prototype,'me').mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve;})).mockRejectedValueOnce(new ApiError('SIGN_IN_REQUIRED',401));
 const {result}=renderHook(()=>useLandingSession());
 act(()=>window.dispatchEvent(new Event('focus')));
 await waitFor(()=>expect(result.current.session).toBe('guest'));
 await act(async()=>finish(account));expect(result.current.session).toBe('guest');
});
it('aborts its check and removes event listeners on unmount',async()=>{
 const me=vi.spyOn(ApiClient.prototype,'me').mockResolvedValue(account);
 const {unmount}=renderHook(()=>useLandingSession());
 await waitFor(()=>expect(me).toHaveBeenCalledTimes(1));
 const signal=me.mock.calls[0][0]!;unmount();expect(signal.aborted).toBe(true);
 act(()=>window.dispatchEvent(new Event('focus')));expect(me).toHaveBeenCalledTimes(1);
});
it('shows workspace links on desktop and mobile when authenticated',async()=>{
 vi.spyOn(ApiClient.prototype,'me').mockResolvedValue(account);
 const u=userEvent.setup(),{container}=render(<Landing/>);
 expect(await screen.findByRole('link',{name:'Open workspace'})).toHaveAttribute('href','/workspace');
 expect(within(container.querySelector('.tx-nav-actions') as HTMLElement).getByRole('link',{name:'Your workspace'})).toHaveAttribute('href','/workspace');
 expect(screen.queryByRole('link',{name:'Sign in'})).not.toBeInTheDocument();
 await u.click(screen.getByRole('button',{name:'Open menu'}));
 expect(within(screen.getByRole('navigation',{name:'Mobile navigation'})).getByRole('link',{name:'Open your workspace'})).toHaveAttribute('href','/workspace');
});
it('shows Sign in only after the session API confirms the visitor is a guest',async()=>{
 vi.spyOn(ApiClient.prototype,'me').mockRejectedValue(new ApiError('SIGN_IN_REQUIRED',401));
 render(<Landing/>);expect(await screen.findByRole('link',{name:'Sign in'})).toHaveAttribute('href','/workspace');
});
it('signs out through the existing API and updates desktop/mobile navigation',async()=>{
 vi.spyOn(ApiClient.prototype,'me').mockResolvedValue(account);
 const logout=vi.spyOn(ApiClient.prototype,'logout').mockResolvedValue();
 const u=userEvent.setup();render(<Landing/>);
 await u.click(await screen.findByRole('button',{name:'Sign out'}));
 expect(logout).toHaveBeenCalledTimes(1);
 expect(await screen.findByRole('link',{name:'Sign in'})).toBeInTheDocument();
 expect(screen.queryByRole('button',{name:'Sign out'})).not.toBeInTheDocument();
 await u.click(screen.getByRole('button',{name:'Open menu'}));
 expect(within(screen.getByRole('navigation',{name:'Mobile navigation'})).getByRole('link',{name:'Sign in / Get started'})).toBeInTheDocument();
});
it('keeps the session visible with an actionable error when sign out fails',async()=>{
 vi.spyOn(ApiClient.prototype,'me').mockResolvedValue(account);
 vi.spyOn(ApiClient.prototype,'logout').mockRejectedValue(new ApiError('NETWORK_ERROR',0));
 const u=userEvent.setup();render(<Landing/>);
 await u.click(await screen.findByRole('button',{name:'Sign out'}));
 expect(await screen.findByRole('alert')).toHaveTextContent('try again');
 expect(screen.getByRole('button',{name:'Sign out'})).toBeEnabled();
 expect(screen.queryByRole('link',{name:'Sign in'})).not.toBeInTheDocument();
});
it('prevents duplicate logout requests and ignores an in-flight session check after logout',async()=>{
 let finishMe!:(value:Account)=>void,finishLogout!:()=>void;
 vi.spyOn(ApiClient.prototype,'me').mockResolvedValueOnce(account).mockImplementationOnce(()=>new Promise(resolve=>{finishMe=resolve;}));
 const logout=vi.spyOn(ApiClient.prototype,'logout').mockImplementation(()=>new Promise(resolve=>{finishLogout=resolve;}));
 const {result}=renderHook(()=>useLandingSession());
 await waitFor(()=>expect(result.current.session).toBe('signed-in'));
 act(()=>window.dispatchEvent(new Event('focus')));
 act(()=>{void result.current.signOut();void result.current.signOut();});
 expect(logout).toHaveBeenCalledTimes(1);expect(result.current.signingOut).toBe(true);
 await act(async()=>finishLogout());expect(result.current.session).toBe('guest');
 await act(async()=>finishMe(account));expect(result.current.session).toBe('guest');
});
