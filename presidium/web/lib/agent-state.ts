export type AgentState='IDLE'|'LISTENING'|'TRANSCRIBING'|'THINKING'|'EXECUTING'|'SPEAKING'|'SUCCESS'|'ERROR'|'PAUSED'|'CANCELLED';
const transitions:Record<AgentState,readonly AgentState[]>={
 IDLE:['LISTENING','THINKING','EXECUTING','SUCCESS','SPEAKING'],
 LISTENING:['TRANSCRIBING','IDLE'],
 TRANSCRIBING:['IDLE','LISTENING','THINKING'],
 THINKING:['EXECUTING','SUCCESS'],
 EXECUTING:['PAUSED','SUCCESS','THINKING'],
 SPEAKING:['SUCCESS','IDLE','LISTENING','THINKING','EXECUTING'],
 SUCCESS:['IDLE','LISTENING','THINKING','EXECUTING','SPEAKING'],
 ERROR:['IDLE','LISTENING','THINKING','EXECUTING','SPEAKING'],
 PAUSED:['EXECUTING','SUCCESS','IDLE','LISTENING'],
 CANCELLED:['IDLE','LISTENING','THINKING','EXECUTING','SPEAKING','SUCCESS'],
};
/** Shared UI/runtime state machine. Errors and explicit cancellation can interrupt any state. */
export class AgentStateMachine{
 state:AgentState='IDLE';
 transition(next:AgentState){if(next===this.state||next==='ERROR'||next==='CANCELLED'||next==='PAUSED'||transitions[this.state].includes(next)){this.state=next;return true}return false}
}
