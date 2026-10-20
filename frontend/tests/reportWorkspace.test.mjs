import {test} from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import {createRequire} from 'node:module'
import React from 'react'
import {renderToStaticMarkup} from 'react-dom/server'
import ts from 'typescript'

const source=readFileSync(new URL('../src/ReportWorkspace.tsx',import.meta.url),'utf8')
const javascript=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText
const componentModule={exports:{}}
new Function('require','module','exports',javascript)(createRequire(import.meta.url),componentModule,componentModule.exports)
const {ReportWorkspace}=componentModule.exports
const props={runId:'test-run',trigger:0,hasResult:true,configured:true,statusNote:'试算结果',notes:{analysis:'',recommendations:'',review:''},onComplete:async()=>{}}

test('entering report starts idle with a manual action and no Word deliverable',()=>{
  const html=renderToStaticMarkup(React.createElement(ReportWorkspace,props))
  assert.ok(html.includes('AI 生成报告'))
  assert.ok(html.includes('评分总表 .xlsx'))
  assert.ok(!html.includes('后评估报告 .docx'))
  assert.ok(!html.includes('正在生成 AI 报告'))
  assert.ok(!html.includes('待生成</button>'))
})

test('a successfully saved report exposes its preview and download',()=>{
  const html=renderToStaticMarkup(React.createElement(ReportWorkspace,{...props,generatedAt:'2026-10-20T12:00:00+08:00'}))
  assert.ok(html.includes('后评估报告 .docx'))
  assert.ok(html.includes('/api/runs/test-run/report.docx'))
  assert.ok(html.includes('/api/runs/test-run/report-preview'))
  assert.ok(!html.includes('正在生成 AI 报告'))
})

test('a previous explicit request does not make a fresh visit look like generation',()=>{
  const html=renderToStaticMarkup(React.createElement(ReportWorkspace,{...props,trigger:3}))
  assert.ok(html.includes('AI 生成报告'))
  assert.ok(!html.includes('正在生成 AI 报告'))
  assert.ok(!html.includes('后评估报告 .docx'))
})

test('the score-page keeps its explicit AI generation entry',()=>{
  const app=readFileSync(new URL('../src/main.tsx',import.meta.url),'utf8')
  assert.match(app,/onClick=\{generateReport\}[^\n]*AI 生成报告/)
  assert.ok(app.includes('async function generateReport()'))
  assert.ok(app.includes('/ai-report'))
  assert.ok(!app.includes('前往报告'))
})
