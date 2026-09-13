import { describe, expect, it } from 'vitest';
import { appendHistory, directions, normalize, totals, trustedSignals } from './telemetry';

export const fixture = {
  schema_version:'1.0',sequence:1,policy:'adaptive',
  traffic:{ simulation_time:10, pedestrians_waiting:2, pedestrian_oldest_wait_seconds:4,
    approaches:directions.map((direction,i)=>({direction,counts:{cars:i+1,buses:0,trucks:0,motorcycles:0,bicycles:0,pedestrians:0},queue_length:i+1,traffic_score:i+1,mean_wait_seconds:10*(i+1),oldest_wait_seconds:40,arrival_rate_per_minute:5,congestion:'low'})),
  },
  signals:{phase:'north_south',stage:'green',elapsed_seconds:2},
  lamps:{north:'green',south:'green',east:'red',west:'red',pedestrian:'red'},
  decision:{phase:'north_south',green_seconds:20,reason:'adaptive'},green_target_seconds:20,emergency_phase:null,
  kpi:{arrived:10,departed:0,remaining:10,total_wait_seconds:300,mean_wait_per_arrival_seconds:30,mean_completed_wait_seconds:0,throughput_per_minute:0,max_queue:10},
};
describe('telemetry boundary',()=>{
  it('keeps occupancy distinct from unique arrivals and weights waiting by queue',()=>{
    const snapshot=normalize(fixture);expect(totals(snapshot)).toMatchObject({queue:10,wait:30,vehicles:10});expect(snapshot.pedestrians).toBe(2);expect(snapshot.unique).toBeNull();
  });
  it('normalizes vision units, counts and null startup traffic',()=>{
    const row={car_count:3,bus_count:1,truck_count:0,motorcycle_count:0,bicycle_count:2,pedestrian_count:1,queue_count:2,traffic_score:5,average_wait_seconds:4,max_wait_seconds:6,arrival_rate:10,congestion:'moderate',estimated_queue_length:125,queue_length_unit:'px',queue_confidence:.8};
    const vision={...fixture,schema_version:'2.0-vision',mode:'vision',tracks:[],pipeline:null,traffic:{timestamp:10,source_id:'camera',directions:Object.fromEntries(directions.map(d=>[d,row])),unique_vehicle_arrivals:9,unique_pedestrian_arrivals:2}};
    const snapshot=normalize(vision);expect(snapshot.unique).toBe(11);expect(snapshot.lanes[0].lengthUnit).toBe('px');expect(snapshot.lanes[0].counts).toEqual([3,1,0,0,2,1]);expect(snapshot.kpi).toBeNull();
    expect(normalize({...vision,traffic:null}).hasTraffic).toBe(false);
  });
  it('rejects unsupported schemas, malformed lanes and non-finite metrics',()=>{
    expect(()=>normalize({...fixture,schema_version:'3'})).toThrow();
    expect(()=>normalize({...fixture,sequence:NaN})).toThrow();
    expect(()=>normalize({...fixture,traffic:{...fixture.traffic,approaches:[]}})).toThrow();
    expect(()=>normalize({...fixture,lamps:{}})).toThrow();
  });
  it('never trusts old, disconnected, failed or unready signal commands',()=>{
    const snapshot=normalize(fixture);
    expect(trustedSignals(true,true,1000,1500,3,snapshot)).toBe(true);
    expect(trustedSignals(false,true,1000,1500,3,snapshot)).toBe(false);
    expect(trustedSignals(true,false,1000,1500,3,snapshot)).toBe(false);
    expect(trustedSignals(true,true,1000,4000,3,snapshot)).toBe(false);
    expect(trustedSignals(true,true,1000,1500,3,{...snapshot,failure:'vision_ended'})).toBe(false);
  });
  it('bounds chart memory, samples by source clock, resets on source restart',()=>{
    const snapshot=normalize(fixture);let history=appendHistory([],snapshot);
    expect(appendHistory(history,{...snapshot,time:10.5})).toBe(history);
    for(let time=11;time<250;time++) history=appendHistory(history,{...snapshot,time});
    expect(history).toHaveLength(90);expect(appendHistory(history,{...snapshot,time:0})).toHaveLength(1);
  });
});
