import {api} from './client.js';
export const datasets={list:()=>api('/datasets'),get:id=>api('/datasets/'+id),create:data=>api('/datasets',{method:'POST',body:JSON.stringify(data)})};
export const jobs={list:()=>api('/jobs'),get:id=>api('/jobs/'+id),create:data=>api('/jobs',{method:'POST',body:JSON.stringify(data)}),approve:id=>api('/jobs/'+id+'/approve',{method:'POST'})};
export const reports={list:()=>api('/reports'),create:data=>api('/reports',{method:'POST',body:JSON.stringify(data)}),toggle:id=>api('/reports/'+id+'/toggle',{method:'POST'}),deliveries:()=>api('/deliveries')};
export const trials={
  list:()=>api('/trials'),
  get:id=>api('/trials/'+id),
  create:data=>api('/trials',{method:'POST',body:JSON.stringify(data)}),
  lock:id=>api('/trials/'+id+'/lock',{method:'POST'}),
  terminate:id=>api('/trials/'+id+'/terminate',{method:'POST'}),
  seal:(id,revision)=>api('/trials/'+id+'/seal',{method:'POST',body:JSON.stringify({revision})}),
  unblind:id=>api('/trials/'+id+'/unblind',{method:'POST'}),
  retryReview:id=>api('/trials/'+id+'/review/retry',{method:'POST'}),
  approveReview:id=>api('/trials/'+id+'/review/approve',{method:'POST'}),
  queue:()=>api('/trials/queue'),
  inspect:data=>api('/trials/inspections',{method:'POST',body:JSON.stringify(data)}),
};
