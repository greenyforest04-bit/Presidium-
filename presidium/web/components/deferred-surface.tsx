'use client';
import {Component,Suspense,type ReactNode} from 'react';
class SurfaceBoundary extends Component<{children:ReactNode;label:string},{failed:boolean}>{state={failed:false};static getDerivedStateFromError(){return {failed:true}}render(){return this.state.failed?<div className="people-error" role="alert">Не удалось открыть {this.props.label}. <button className="secondary-btn" onClick={()=>location.reload()}>Перезагрузить</button></div>:this.props.children}}
export function DeferredSurface({children,label}:{children:ReactNode;label:string}){return <SurfaceBoundary label={label}><Suspense fallback={<p className="account-note" role="status">Открываем {label}…</p>}>{children}</Suspense></SurfaceBoundary>}
