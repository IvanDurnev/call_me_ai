const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function render(serverDraft, localDraft) {
  const source = fs.readFileSync('static/activity.js', 'utf8');
  const fn = source.slice(source.indexOf('  function renderReview('), source.indexOf('  function renderControls('));
  const children = [];
  const element = (tag, text) => ({tag, text, append(){}, addEventListener(){}});
  const sandbox = {
    state: {draft:serverDraft, players:[{name:'Алиса'},{name:'Борис'},{name:'Мария'}],presenter:0,
      settings:{mode:'teams'},open_round:false,active_side:0,sides:[{members:[0,2]}]},
    reviewDraft:localDraft, busy:false,
    select:(name,options,value)=>({...element('select'),name,value}),
    button:(text,action)=>({...element('button',text),action}),
    el:element,label:(text,input)=>({text,input}),sideName:()=> 'Синие',renderControls(){},
  };
  vm.runInNewContext(fn+';renderReview({append(e){ output.push(e); }});', {...sandbox,output:children});
  return children.find(e=>e.action==='apply');
}

test('matching server result enables confirmation despite JSON key ordering',()=> {
  assert.equal(render({guesser:2,outcome:'success'},{outcome:'success',guesser:2}).disabled,false);
});
test('changing result after preview requires a new calculation',()=> {
  assert.equal(render({guesser:2,outcome:'success'},{outcome:'failure',guesser:null}).disabled,true);
  assert.equal(render(null,{outcome:'failure',guesser:null}).disabled,true);
});
