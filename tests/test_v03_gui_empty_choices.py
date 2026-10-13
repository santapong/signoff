"""Execute operator scripts with native option-value semantics and empty choices."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r"""
const calls=[],
writes=[],
notices=[],
elements=new Map();

class Element {
 constructor(tag='div',
text=''){this.tagName=tag.toUpperCase();
this.textContent=text;
this.children=[];
this.handlers={};
this.disabled=false;
this.dataset={};
this.options=[];
this.selected=false;
this.elements={soak_session_id:{value:''}}}
 get value(){if(this._value!==undefined)return this._value;
if(this.tagName==='OPTION')return this.textContent;
if(this.tagName==='SELECT')return this.children.find(x=>x.selected)?.value
  ??this.children[0]?.value??'';
return ''}
 set value(value){this._value=String(value)}
 get selectedOptions(){return this.children.filter(x=>x.selected)}
 append(...children){this.children.push(...children);
this.options=this.children}
 replaceChildren(...children){this.children=[...children];
this.options=this.children;
delete this._value}
 querySelector(selector){return this.submitButton||(this.submitButton=new Element('button'))}
 addEventListener(name,
fn){this.handlers[name]=fn}
}
const selectIds=new Set(['dataset-choice',
'adjudicated-reference',
'adjudication-annotations']);

const formIds=new Set(['dataset-create',
'dataset-assignments',
'freeze-dataset',
'reference-save',
'adjudication-form']);

const document={getElementById(id){if(!elements.has(id)){const e=new Element(
 selectIds.has(id)?'select':formIds.has(id)?'form':'div');
e.id=id;
elements.set(id,
e)}return elements.get(id)},
querySelectorAll(){return[]},
addEventListener(){}};

document.getElementById('reference-data').textContent=JSON.stringify({session_id:'session-one'});

const Signoff={node:(tag,
text='',
cls='')=>{const e=new Element(tag,
text);
e.className=cls;
return e},
sourceLabel:()=> 'Not declared',
time:ms=>String(ms),
scoreTable:()=>new Element('div'),
busy(button,
active){button.disabled=active},
async request(url){calls.push(url);
if(url==='/evidence/qualification/datasets')return{datasets:[],
candidates:[]};
if(url==='/evidence/qualification/receipts')return{receipts:[]};
if(url.endsWith('/references'))return{revisions:[]};
if(url.endsWith('/adjudications'))return{adjudications:[]};
if(url.includes('/source/index?'))return{source_hash:'source-hash',
index_hash:'index-hash',
total_frames:1,
frames:[{frame:0,
ms:0,
clock_basis:'capture_sidecar'}]};
throw Error('Unexpected initial request: '+url)}};

const notice=(text,
kind)=>notices.push({text,
kind:kind||''});

const api=async(url,
body,
method)=>{writes.push({url,
body,
method});
return{}};

class FormData {
 constructor(form){this.form=form}
 get(key){
 if(key==='reference_revision')return document.getElementById('adjudicated-reference').value;
return ''}getAll(){return[]}}
const confirm=()=>false;

"""


def run_script(name: str, assertions: str) -> dict:
    script = (ROOT / "apps/api/static" / name).read_text()
    program = HARNESS + script + "\nawait new Promise(setImmediate);\n" + assertions
    result = subprocess.run(
        ["node", "--input-type=module", "-"],
        input=program,
        text=True,
        check=True,
        capture_output=True,
        timeout=10,
    )
    return json.loads(result.stdout)


def test_empty_qualification_does_not_request_placeholder_identifier_or_mutate():
    result = run_script(
        "qualification.js",
        r"""
const assignment=document.getElementById('dataset-assignments'),
freeze=document.getElementById('freeze-dataset'),
evaluate=document.getElementById('evaluate-dataset');

const initial={value:document.getElementById('dataset-choice').value,
save:assignment.submitButton.disabled,
freeze:freeze.submitButton.disabled,
evaluate:evaluate.disabled};

await assignment.handlers.submit({preventDefault(){},
target:assignment});

await freeze.handlers.submit({preventDefault(){},
target:freeze});

await evaluate.onclick();

console.log(JSON.stringify({initial,
calls,
writes,
notices}));

""",
    )
    assert result["initial"] == {"value": "", "save": True, "freeze": True, "evaluate": True}
    assert result["calls"] == [
        "/evidence/qualification/datasets",
        "/evidence/qualification/receipts",
    ]
    assert result["writes"] == []
    assert not any(item["kind"] == "error" for item in result["notices"])
    assert any("choose a dataset" in item["text"].lower() for item in result["notices"])


def test_empty_reference_adjudication_keeps_placeholder_empty_and_blocks_submit():
    result = run_script(
        "reference.js",
        r"""
const form=document.getElementById('adjudication-form');

const initial={value:document.getElementById('adjudicated-reference').value,
disabled:form.submitButton.disabled};

await form.handlers.submit({preventDefault(){},
target:form});

console.log(JSON.stringify({initial,
calls,
writes,
notices}));

""",
    )
    assert result["initial"] == {"value": "", "disabled": True}
    assert result["writes"] == []
    assert not any(item["kind"] == "error" for item in result["notices"])
    assert any("two independent annotation revisions" in item["text"] for item in result["notices"])
    assert all("analysis" not in path and "review" not in path for path in result["calls"])
