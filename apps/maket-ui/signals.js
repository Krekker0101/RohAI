const phases=new Set(['ALL_RED','NS_GREEN','NS_YELLOW','EW_GREEN','EW_YELLOW','PED_GREEN']);
export function displayedSignal(state,now=Date.now()){
 if(state?.connection_lost)return {phase:null,source:'unavailable'};
 if(state?.output_mode==='CAMERA_ONLY')return {phase:state.demo_running&&phases.has(state.virtual_signal_state)?state.virtual_signal_state:null,source:'virtual'};
 const hardware=state?.hardware,heartbeat=hardware?.last_heartbeat;
 const age=typeof heartbeat==='number'?now-heartbeat*1000:Infinity;
 const phase=hardware?.ack?.actual_phase;
 return {phase:hardware?.connected&&age>=0&&age<1000&&phases.has(phase)?phase:null,source:'electrical'};
}
