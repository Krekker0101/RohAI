import test from 'node:test';
import assert from 'node:assert/strict';
import {displayedSignal} from './signals.js';
test('camera preview requires active control',()=>{
 const state={output_mode:'CAMERA_ONLY',virtual_signal_state:'NS_GREEN',demo_running:true};
 assert.equal(displayedSignal(state).phase,'NS_GREEN');
 assert.equal(displayedSignal({...state,demo_running:false}).phase,null);
});
test('physical preview uses fresh ACK, never the requested phase',()=>{
 const state={output_mode:'PHYSICAL_OUTPUT',approved_state:'EW_GREEN',hardware:{connected:true,last_heartbeat:10,ack:{actual_phase:'ALL_RED'}}};
 assert.equal(displayedSignal(state,10500).phase,'ALL_RED');
 for(const now of [9999,11000,15000])assert.equal(displayedSignal(state,now).phase,null);
 assert.equal(displayedSignal({...state,connection_lost:true},10500).phase,null);
 assert.equal(displayedSignal({...state,hardware:{...state.hardware,connected:false}},10500).phase,null);
});
