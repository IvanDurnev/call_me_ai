const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('static/activity.js','utf8');
const fn=source.slice(source.indexOf('  function canGuess('),source.indexOf('  function renderSecret('));
function enabled({phase='performing',remaining=1,own=true,recovery=false,busy=false}={}){
 return vm.runInNewContext(fn+';canGuess();',{state:{phase,deadline:160},mine:()=>own,seconds:()=>remaining,recovery,busy});
}
test('guessed remains available to active team until deadline',()=>{
 assert.equal(enabled(),true);
 assert.equal(enabled({remaining:60}),true);
 assert.equal(enabled({remaining:0}),false);
});
test('guessed unavailable outside attempt, on other phones or while sending',()=>{
 for(const change of [{phase:'prepare'},{phase:'board'},{own:false},{recovery:true},{busy:true}])assert.equal(enabled(change),false);
});

test('drawing maps normalized points to the full portrait canvas height',()=>{
 const strokeFn=source.slice(source.indexOf('  function strokePath('),source.indexOf('  function renderSketch('));
 const calls=[];
 const ctx={beginPath(){},moveTo(x,y){calls.push(['move',x,y]);},lineTo(x,y){calls.push(['line',x,y]);},stroke(){},arc(x,y){calls.push(['dot',x,y]);},fill(){}};
 const sandbox={ctx,canvas:{width:1000,height:1200},stroke:{tool:'pen',width:7,points:[[.1,.2],[.8,.9]]}};
 vm.runInNewContext(strokeFn+';strokePath(stroke);',sandbox);
 assert.deepEqual(calls,[['move',100,240],['line',800,1080]]);
});

test('television drawing layer exists only for the active drawing attempt',()=>{
 const viewFn=source.slice(source.indexOf('  function drawingView('),source.indexOf('  function render(force'));
 const view=(phase,method,host=true,own=false,recovery=false)=>vm.runInNewContext(viewFn+';drawingView();',{state:{phase},category:()=>({method}),host,mine:()=>own,recovery});
 assert.equal(view('performing','draw'),'table');
 for(const phase of ['lobby','handoff','choose_task','prepare','board','finished'])assert.equal(view(phase,'draw'),null);
 assert.equal(view('performing','mime'),null);
 assert.equal(view('performing','explain'),null);
 assert.equal(view('performing','draw',false,true),'phone');
 assert.equal(view('performing','draw',false,false),null);
 assert.equal(view('performing','draw',false,true,true),null);
});
