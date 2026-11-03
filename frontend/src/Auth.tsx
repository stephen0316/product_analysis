import { createContext, useContext, useEffect, useState, useRef, type ReactNode, type InputHTMLAttributes, useId } from 'react'
import { ChevronDown, Eye, EyeOff, X } from 'lucide-react'
import { useModal } from './modal'
import './auth.css'

function PasswordInput(props:Omit<InputHTMLAttributes<HTMLInputElement>,'type'>){
  const [visible,setVisible]=useState(false)
  const generatedId=useId(),id=props.id||generatedId
  const action=(visible?'隐藏':'显示')+(props['aria-label']||'密码')
  return <span className="password-input"><input {...props} id={id} type={visible?'text':'password'}/><button className="password-toggle" type="button" aria-label={action} aria-controls={id} aria-pressed={visible} title={action} disabled={props.disabled} onClick={()=>setVisible(value=>!value)}>{visible?<EyeOff size={18} aria-hidden="true"/>:<Eye size={18} aria-hidden="true"/>}</button></span>
}

type Identity={username:string;must_change:boolean;csrf:string}
let csrf=''
const originalFetch=window.fetch.bind(window)
// Covers both main workspace and report requests. Session secrets stay in an
// HttpOnly cookie; only the per-session CSRF nonce is available to JavaScript.
window.fetch=async(input,init)=>{
  const url=new URL(input instanceof Request?input.url:input.toString(),location.href)
  const internal=url.origin===location.origin&&url.pathname.startsWith('/api/')
  const headers=new Headers(init?.headers||(input instanceof Request?input.headers:undefined))
  if(internal&&csrf)headers.set('X-CSRF-Token',csrf)
  const response=await originalFetch(input,{...init,headers})
  if(internal&&response.status===401&&!['/api/auth/login','/api/auth/me'].includes(url.pathname))window.dispatchEvent(new Event('session-expired'))
  return response
}
async function request<T>(path:string,method='GET',body?:unknown):Promise<T>{
  const response=await fetch('/api/auth'+path,{method,headers:body===undefined?undefined:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)})
  const value=await response.json()
  if(!response.ok)throw new Error(typeof value.detail==='string'?value.detail:'请求失败，请重试')
  return value
}
const AuthContext=createContext<{identity:Identity;logout:()=>Promise<void>}|null>(null)

export function AuthBoundary({children}:{children:ReactNode}){
  const [identity,setIdentity]=useState<Identity|null>(null),[loading,setLoading]=useState(true),[error,setError]=useState('')
  const [username,setUsername]=useState(''),[password,setPassword]=useState(''),[nextPassword,setNextPassword]=useState(''),[confirmation,setConfirmation]=useState(''),[busy,setBusy]=useState(false)
  useEffect(()=>{
    let current=true
    request<Identity>('/me').then(value=>{if(current){csrf=value.csrf;setIdentity(value)}}).catch(()=>{}).finally(()=>{if(current)setLoading(false)})
    const expired=()=>{csrf='';setIdentity(null);setPassword('');setError('登录已失效，请重新登录')}
    window.addEventListener('session-expired',expired)
    return()=>{current=false;window.removeEventListener('session-expired',expired)}
  },[])
  async function logout(){await request('/logout','POST');csrf='';setIdentity(null);setPassword('')}
  async function submit(event:React.FormEvent){
    event.preventDefault();setBusy(true);setError('')
    try{
      if(identity?.must_change){
        if(nextPassword!==confirmation)throw new Error('两次输入的新密码不一致')
        await request('/password','POST',{current_password:password,new_password:nextPassword})
        csrf='';setIdentity(null);setPassword('');setNextPassword('');setConfirmation('');setError('密码已修改，请使用新密码登录')
      }else{
        const value=await request<Identity>('/login','POST',{username,password});csrf=value.csrf;setIdentity(value)
        if(!value.must_change)setPassword('')
      }
    }catch(e){setError((e as Error).message)}finally{setBusy(false)}
  }
  if(loading)return <main className="auth-page"><p>正在检查登录状态…</p></main>
  if(!identity||identity.must_change)return <main className="auth-page"><form className="auth-form" onSubmit={submit}><span className="auth-brand">PE · 产品后评估</span><h1>{identity?'修改初始密码':'登录工作台'}</h1><p>{identity?'首次登录请设置自己的密码。':'仅限获授权的评估团队使用。'}</p>{!identity&&<label>账号<input autoComplete="username" value={username} onChange={e=>setUsername(e.target.value)} required autoFocus/></label>}<label>{identity?'初始密码':'密码'}<PasswordInput aria-label={identity?'初始密码':'密码'} autoComplete="current-password" value={password} onChange={e=>setPassword(e.target.value)} required maxLength={128}/></label>{identity&&<><label>新密码<PasswordInput aria-label="新密码" autoComplete="new-password" minLength={12} maxLength={128} value={nextPassword} onChange={e=>setNextPassword(e.target.value)} required/></label><label>确认新密码<PasswordInput aria-label="确认新密码" autoComplete="new-password" minLength={12} maxLength={128} value={confirmation} onChange={e=>setConfirmation(e.target.value)} required/></label><small>密码须为 12–128 个字符。</small></>}{error&&<p role="alert" className="auth-error">{error}</p>}<button className="btn btn-dark" disabled={busy}>{busy?'正在处理…':identity?'修改密码并重新登录':'登录'}</button></form></main>
  return <AuthContext.Provider value={{identity,logout}}>{children}</AuthContext.Provider>
}

export function AccountControls(){
  const auth=useContext(AuthContext),[open,setOpen]=useState(false),[menuOpen,setMenuOpen]=useState(false),[error,setError]=useState('')
  const menuRef=useRef<HTMLDivElement>(null),triggerRef=useRef<HTMLButtonElement>(null),menuId=useId()
  useEffect(()=>{
    if(!menuOpen)return
    const outside=(event:PointerEvent)=>{if(event.target instanceof Node&&!menuRef.current?.contains(event.target))setMenuOpen(false)}
    document.addEventListener('pointerdown',outside)
    return()=>document.removeEventListener('pointerdown',outside)
  },[menuOpen])
  function menuKey(event:React.KeyboardEvent){
    if(event.key==='Escape'){event.preventDefault();event.stopPropagation();setMenuOpen(false);triggerRef.current?.focus();return}
    if(!['ArrowDown','ArrowUp','Home','End'].includes(event.key))return
    event.preventDefault();setMenuOpen(true)
    requestAnimationFrame(()=>{
      const items=Array.from(menuRef.current?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]')||[])
      const current=items.indexOf(document.activeElement as HTMLButtonElement)
      const index=event.key==='Home'?0:event.key==='End'?items.length-1:event.key==='ArrowDown'?(current+1)%items.length:(current<0?items.length-1:(current-1+items.length)%items.length)
      items[index]?.focus()
    })
  }
  if(!auth)return null
  return <div className="account-controls"><div className="account-menu" ref={menuRef} onPointerEnter={event=>{if(event.pointerType==='mouse')setMenuOpen(true)}} onPointerLeave={event=>{if(event.pointerType==='mouse')setMenuOpen(false)}} onBlur={event=>{if(!event.currentTarget.contains(event.relatedTarget))setMenuOpen(false)}} onKeyDown={menuKey}><button ref={triggerRef} type="button" className="account-trigger" aria-label={`${auth.identity.username} 账号菜单`} aria-haspopup="menu" aria-expanded={menuOpen} aria-controls={menuId} onClick={()=>setMenuOpen(true)}>{auth.identity.username}<ChevronDown size={14} aria-hidden="true"/></button>{menuOpen&&<div className="account-dropdown" id={menuId} role="menu" aria-label="账号菜单"><button type="button" role="menuitem" onClick={()=>{setMenuOpen(false);setOpen(true)}}>修改密码</button><button type="button" role="menuitem" onClick={()=>{setMenuOpen(false);if(window.confirm('退出登录前请确认已保存当前修改。'))void auth.logout().catch(e=>setError(e.message))}}>退出账号</button></div>}</div>{error&&<span role="alert">{error}</span>}{open&&<ChangePassword onClose={()=>setOpen(false)} current={auth.identity.username}/>}</div>
}
function ChangePassword({onClose,current}:{onClose:()=>void;current:string}){
  const modal=useModal(onClose),[error,setError]=useState(''),[busy,setBusy]=useState(false)
  const [oldPassword,setOldPassword]=useState(''),[newPassword,setNewPassword]=useState(''),[confirmation,setConfirmation]=useState('')
  async function submit(event:React.FormEvent){
    event.preventDefault();setError('')
    if(newPassword!==confirmation){setError('两次输入的新密码不一致');return}
    setBusy(true)
    try{await request('/password','POST',{current_password:oldPassword,new_password:newPassword});location.reload()}
    catch(e){setError((e as Error).message);setBusy(false)}
  }
  return <div className="confirm-overlay account-password-overlay" onMouseDown={event=>{if(event.target===event.currentTarget)onClose()}}><section ref={modal} className="confirm-dialog account-dialog" role="dialog" aria-modal="true" aria-label="修改密码" tabIndex={-1}><div className="confirm-head account-password-head"><h2>修改密码</h2><button className="confirm-close" type="button" onClick={onClose} aria-label="关闭修改密码"><X size={18} aria-hidden="true"/></button></div><p>团队共用账号：<strong>{current}</strong></p><p>修改密码后，所有设备均需使用新密码重新登录。</p>{error&&<p role="alert" className="auth-error">{error}</p>}<form onSubmit={submit}><label>原密码<PasswordInput aria-label="原密码" autoComplete="current-password" value={oldPassword} onChange={e=>setOldPassword(e.target.value)} required/></label><label>新密码<PasswordInput aria-label="新密码" autoComplete="new-password" value={newPassword} onChange={e=>setNewPassword(e.target.value)} minLength={12} maxLength={128} required/></label><label>确认新密码<PasswordInput aria-label="确认新密码" autoComplete="new-password" value={confirmation} onChange={e=>setConfirmation(e.target.value)} minLength={12} maxLength={128} required/></label><small>密码须为 12–128 个字符。</small><footer className="confirm-actions account-password-actions"><button className="btn btn-dark" disabled={busy}>{busy?'正在修改…':'修改密码并重新登录'}</button></footer></form></section></div>
}
