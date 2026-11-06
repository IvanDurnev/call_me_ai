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
