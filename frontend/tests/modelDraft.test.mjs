import {test} from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import ts from 'typescript'

const source=readFileSync(new URL('../src/modelDraft.ts',import.meta.url),'utf8')
const javascript=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText
const {modelDraftSignature}=await import(`data:text/javascript;base64,${Buffer.from(javascript).toString('base64')}`)
const original={name:'模型',version:'V1',definition:{ranking:{first_score:10,decrement:.5},categories:[{key:'a',label:'市场'}],dimensions:[{key:'metric',label:'份额',metric_keys:['market_share'],weight:100,rule_summary:'降序赋分',options:{}}]}}

test('name, weight, rule, category and order changes are real edits; reverting restores clean state',()=>{
  for(const change of [m=>m.name='新模型',m=>m.definition.dimensions[0].weight=90,m=>m.definition.dimensions[0].rule_summary='升序赋分',m=>m.definition.categories[0].label='规模',m=>m.definition.ranking.first_score=20]){
    const edited=structuredClone(original)
    change(edited)
    assert.notEqual(modelDraftSignature(edited),modelDraftSignature(original))
  }
  const reverted=structuredClone(original)
  reverted.name='新模型';reverted.name=original.name
  assert.equal(modelDraftSignature(reverted),modelDraftSignature(original))
  const multi=structuredClone(original);multi.definition.dimensions.push({...multi.definition.dimensions[0],key:'another'})
  const swapped=structuredClone(multi);swapped.definition.dimensions.reverse()
  assert.notEqual(modelDraftSignature(swapped),modelDraftSignature(multi))
})

test('generated parser metadata and an explicit equivalent binding do not dirty unchanged rules',()=>{
  const parsed=structuredClone(original)
  parsed.definition.dimensions[0].calculation_type='market_share'
  parsed.definition.dimensions[0].options={parsed_rule:{normalized_summary:'识别说明'},rule_parser:{parsed_at:'now'}}
  assert.equal(modelDraftSignature(parsed),modelDraftSignature(original))
  parsed.definition.dimensions[0].options.growth_denominator_absolute=false
  assert.notEqual(modelDraftSignature(parsed),modelDraftSignature(original))
})

test('object property order is ignored and comparison does not mutate snapshots',()=>{
  const before=structuredClone(original)
  const reordered={version:original.version,definition:original.definition,name:original.name}
  assert.equal(modelDraftSignature(original),modelDraftSignature(reordered))
  assert.deepEqual(original,before)
})
