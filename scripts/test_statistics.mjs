// Run Node 24's type stripping against the actual frontend calculation module.
import assert from 'node:assert/strict';
import { latestMetrics, channelSummaries, csvReport, channelsReport } from '../frontend/src/statistics.ts';

const accounts = [{id:'yt',platform:'youtube',name:'YouTube'}, {id:'tt',platform:'tiktok',name:'TikTok'}, {id:'empty',platform:'youtube',name:'=sensitive'}];
const jobs = [{id:'a',account:accounts[0]}, {id:'b',account:accounts[0]}, {id:'c',account:accounts[1]}];
const metrics = [
  {id:'new',job_id:'a',captured:'2026-10-24T18:00:00Z',values:{views:10,likes:0,comments:null},source:'YouTube API'},
  {id:'old',job_id:'a',captured:'2026-10-24T19:00:00+03:00',values:{views:8,likes:2,comments:1},source:'YouTube API'},
  {id:'tt',job_id:'c',captured:'2026-10-24T18:01:00Z',values:{views:100,likes:null,comments:3},source:'CSV'},
  {id:'orphan',job_id:'unknown',captured:'2026-10-24T19:00:00Z',values:{views:9000},source:'CSV'},
];
assert.equal(latestMetrics(metrics).find(m=>m.job_id==='a').id,'new','compare instants, not ISO lexical order or array order');
let rows = channelSummaries(accounts,jobs,metrics);
assert.deepEqual(rows[0].views,{value:10,covered:1},'never sum repeated cumulative snapshots');
assert.equal(rows[0].jobs,2,'unmeasured tasks count toward coverage');
assert.equal(rows[0].measured,1);
assert.deepEqual(rows[0].likes,{value:0,covered:1},'zero is valid');
assert.deepEqual(rows[0].comments,{value:null,covered:0},'missing remains missing despite older value');
assert.equal(rows[1].views.value,100,'platform totals stay separate');
assert.equal(rows[2].views.value,null,'empty channels stay visible without invented zeros');
assert.equal(rows[2].captured,null);
assert.deepEqual(rows[0].sources,['YouTube API']);
rows = channelSummaries(accounts,jobs,[...metrics,{id:'extra',job_id:'b',captured:'2026-10-24T19:00:00Z',values:{views:5,likes:1},source:'CSV'}]);
assert.deepEqual(rows[0].views,{value:15,covered:2});
assert.deepEqual(rows[0].sources,['CSV','YouTube API'],'mixed provenance remains visible');
assert.equal(rows[0].captured,'2026-10-24T19:00:00Z');
assert.deepEqual(channelSummaries([],jobs,metrics),[]);
assert.equal(csvReport([['a,"b',0,null,'=1+1',' \t@formula']]), '\uFEFF"a,""b","0","","\'=1+1","\' \t@formula"\r\n');
assert(channelsReport(rows).includes('"youtube","\'=sensitive"'));
assert(channelsReport(rows).startsWith('\uFEFF"platform","account"'));
console.log('Statistics: aggregation and CSV assertions passed; no remote services used.');
