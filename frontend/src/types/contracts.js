/** @typedef {{id:number,name:string,source:string,rows:number,checksum:string}} Dataset */
/** @typedef {{id:number,name:string,dataset_id:number,status:string,result:Object,approved:boolean}} AnalysisJob */
/** @typedef {{id:number,name:string,job_id:number,destination:string,next_run:number,enabled:boolean}} ReportSchedule */
/** @typedef {{id:number,name:string,line:string,status:string,version:number,seal_version:number|null,blinded:boolean}} BlindTrial */
/** @typedef {{blind_code:string,fired:number,passed:number,revision:number}} BlindInspection */
/** @typedef {{status:string,attempts:number,approved:boolean,error:string}} BlindReview */
export {};
