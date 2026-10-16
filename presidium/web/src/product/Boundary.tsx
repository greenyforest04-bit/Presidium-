import { Component } from 'react'
import type { ReactNode } from 'react'

export default class FeatureBoundary extends Component<{children:ReactNode;onBack:()=>void},{failed:boolean}> {
  state={failed:false}
  static getDerivedStateFromError(){return {failed:true}}
  render(){return this.state.failed?<section className="panel"><p role="alert">Не удалось открыть раздел. Чат продолжает работать.</p><button onClick={this.props.onBack}>Вернуться в чаты</button></section>:this.props.children}
}
