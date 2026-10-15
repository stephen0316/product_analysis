import React, { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { ArrowDownToLine, ArrowLeft, ArrowRight, Check, ChevronRight, CircleAlert, Copy, FileSpreadsheet, FileText, FolderOpen, GitCompare, LayoutDashboard, Menu, Plus, RefreshCw, Rocket, Save, ShieldCheck, Sparkles, SlidersHorizontal, Trash2, UploadCloud, X } from 'lucide-react'
import { SystemSelect } from './SystemSelect'
import { modelDraftSignature } from './modelDraft'
import './style.css'
import './overrides.css'
import './swiss.css'
import './ui-updates.css'

type Metric = {key:string;label:string;role:string|null;status?:string;rule_summary?:string}
type CalculationBasis = {dimension_key:string;dimension_label:string;rule_summary:string;formula:string;inputs:{key:string;label:string;required?:boolean}[];display_inputs?:{key:string;label:string;required?:boolean}[];input_groups:{label:string;keys:string[]}[];display_groups?:{label:string;keys:string[]}[]}
type Mapping = {sheet:string|null;header_row:number;product_col:string;fields:Record<string,string|null>;units?:Record<string,string>;status?:'ready'|'needs_confirmation';suggested_targets?:string[];detected_metrics?:string[];mapping_version?:number;input_labels?:Record<string,string>;calculation_basis?:CalculationBasis[];provided_summary?:string[]}
type Sheet = {name:string;rows:number;columns:number;preview:string[][]}
type InputFile = {id:string;name:string;role:string;targets:string[];sha256:string;mapping:Mapping;inspection:Sheet[];uploaded_at:string}
type UploadOutcome = {index:number;name:string;status:'uploaded'|'duplicate'|'failed';message:string;retryable:boolean;file_id?:string;existing_name?:string;code?:string}
type UploadAttempt = UploadOutcome & {file:File}
type Issue = {level:string;code:string;message:string;field?:string;product?:string}
type Validation = {status:'passed'|'failed';validated_at:string;issues:Issue[];conflicts:Result['conflicts'];error_count:number;warning_count:number}
type Step = {label:string;display_label?:string;format?:string;unit?:string;value:number;rank?:number;score?:number;formula?:string;source?:{file_id:string;cell:string;source_name:string;value:number;unit?:string}}
type IndicatorScore = {key:string;label:string;weight:number;weighted_score:number|null;ratio:number|null;metric_keys:string[];components:{metric:string;label:string;raw_score:number|null;first_score:number|null;ratio:number|null}[]}
type Trace = {indicator_label?:string;contribution?:IndicatorScore;product:string;metric:string;score:number;rank:number;average?:number;steps:Step[]}
type Result = {indicator_scores:Record<string,IndicatorScore[]>;metrics:Metric[];scores:Record<string,Record<string,number>>;traces:Trace[];issues:Issue[];conflicts:{field:string;product:string;candidates:{file_id:string;value:number;cell:string}[]}[];covered_points:number;weight_total:number;indicator_count:number;calculated_indicator_count:number;status_note:string;result_status:'partial'|'trial'|'complete';official_total_available:boolean;simulated_metrics?:string[];model_summary?:Simulation}
type ResultTableColumn = {label:string;role:'index'|'text'|'value'|'rank'|'score';unit?:string;format:'integer'|'text'|'number'|'percent'|'score'|'ratio';trace_metric:string|null}
type ResultTable = {key:string;label:string;title:string;kind:'total'|'dimension';columns:ResultTableColumn[];rows:{product:string;values:(number|string|null)[]}[]}
type ParsedRuleStep = {order:number;operation:string;description:string;formula:string}
type RequiredInput = {name:string;concept:string;period:string;required:boolean}
type ParsedRule = {execution_plan?:Record<string,unknown>;normalized_summary:string;ranking_direction:string;time_window:string;required_inputs?:RequiredInput[];tie_method:string;steps:ParsedRuleStep[];parameters:{first_score:number|null;rank_decrement:number|null;cap_percent:number|null;growth_denominator_uses_absolute_value:boolean|null};assumptions:string[];warnings:string[];confidence:number}
type RuleParseResult = {rule:ParsedRule;provider:'gemini'|'local';model:string;parsed_at:string;execution_contract?:string;calculation_type?:string}
type RuleParserStatus = {configured:boolean;provider:string;model:string}
type DimensionOptions = Record<string,unknown>&{parsed_rule?:ParsedRule;rule_parser?:Omit<RuleParseResult,'rule'>&{source_summary:string}}
type ModelCategory = {key:string;label:string;export_sort_indicator_key?:string}
type ModelDimension = {key:string;category_key:string;calculation_type?:string;group?:string;label:string;enabled:boolean;weight:number;aggregation:'average_scaled';metric_keys:string[];rule_summary:string;options?:DimensionOptions}
type CalculationType = {id:string;label:string;algorithm:string;rule_summary:string;inputs:string[];parameters:string[];output:string;steps:string[]}
type ModelReadiness = {errors:string[];indicators:{key:string;executable:boolean;errors:string[];calculation_type?:string;normalized_summary?:string}[]}
type ModelDefinition = {categories:ModelCategory[];ranking:{method:'competition';first_score:number;decrement:number};dimensions:ModelDimension[]}
type ModelEvent = {id:string;event_type:string;created_at:string;detail:{summary?:string;reason?:string;clone_from?:string}}
type EvaluationModel = {id:string;name:string;version:string;status:'draft'|'published'|'archived';definition:ModelDefinition;change_reason:string;created_at:string;updated_at:string;published_at?:string|null;events?:ModelEvent[]}
type SimulationRow = {product:string;total:number|null;partial_total:number;rank:number|null;baseline_total:number|null;baseline_rank:number|null;score_delta:number|null;rank_delta:number|null;complete:boolean;missing_metrics:string[]}
type Simulation = {rows:SimulationRow[];complete_products:number;weight_total:number;ready:boolean}
type WeightGoal = {target_product:string;direction:'up'|'down';positions:number;normalized_goal:string;assumptions:string[];warnings:string[];confidence:number;baseline_rank:number;requested_rank:number}
type WeightChange = {key:string;label:string;from:number;to:number;delta:number}
type WeightCandidateRow = {product:string;baseline_total:number;baseline_rank:number;total:number;rank:number;position_change:number}
type WeightCandidate = {id:string;label:string;achieved:boolean;target_rank_before:number;target_rank_after:number;requested_rank:number;weights:Record<string,number>;weight_changes:WeightChange[];changed_dimensions:number;max_other_rank_change:number;total_rank_movement:number;explanation:string;rows:WeightCandidateRow[]}
type WeightOptimization = {goal:WeightGoal;constraints:{weight_total:number;max_weight_change:number;max_other_rank_change:number;step:number};feasible:boolean;message:string;candidates:WeightCandidate[]}
type WeightOptimizationResponse = {interpretation:{goal:WeightGoal;provider:'gemini';model:string;parsed_at:string};optimization:WeightOptimization}
type Run = {id:string;year:number;data_year:number;name:string;created_at:string;updated_at:string;model_id?:string|null;model?:EvaluationModel|null;files:InputFile[];source_choices:Record<string,string>;notes:{analysis?:string;recommendations?:string;review?:string};report?:{generated_at:string}|null;validation:Validation|null;result:Result|null}
type RunProgress = {status:'awaiting_upload'|'awaiting_validation'|'awaiting_calculation'|'results_ready'|'report_ready';label:string;next_view:'upload'|'validation'|'overview'|'report';action:string;file_count:number}
type RunSummary = Pick<Run,'id'|'year'|'data_year'|'name'|'updated_at'|'model_id'>&{progress?:RunProgress}
type Config = {products:string[];metrics:Metric[];roles:Record<string,string>;rule_version:string;scope:string;rule_parser:RuleParserStatus;calculation_types:CalculationType[]}
type View = 'models'|'batches'|'upload'|'validation'|'overview'|'report'
type ScoreView = 'table'|'radar'
type ConfirmAction = {title:string;subject:string;warning:string;confirmLabel:string;onConfirm:()=>Promise<void>}
type LeaveGuard = (proceed:()=>void)=>void

async function api<T>(path:string, method='GET', body?:unknown):Promise<T> {
  const response = await fetch('/api'+path,{method,body:body instanceof FormData?body:body===undefined?undefined:JSON.stringify(body),headers:body instanceof FormData||body===undefined?undefined:{'Content-Type':'application/json'}})
  if(!response.ok){const error=await response.json().catch(()=>({detail:response.statusText}));throw new Error(error.detail||'请求失败')}
  return response.json()
}
const fmt=(value:number|undefined,digits=2)=>value===undefined?'—':new Intl.NumberFormat('zh-CN',{maximumFractionDigits:digits,minimumFractionDigits:digits}).format(value)
const fmtRaw=(value:number)=>new Intl.NumberFormat('zh-CN',{maximumFractionDigits:8}).format(value)
const fmtPercent=(value:number)=>new Intl.NumberFormat('zh-CN',{style:'percent',minimumFractionDigits:2,maximumFractionDigits:2}).format(value)
const fmtRadarPercent=(value:number)=>new Intl.NumberFormat('zh-CN',{style:'percent',maximumFractionDigits:2}).format(value)
const fmtStep=(step:Step)=>step.format==='percent'?fmtPercent(step.value):`${fmt(step.value,step.format==='score'?1:2)}${step.unit?' '+step.unit:''}`
const numberValue=(value:string)=>value===''?Number.NaN:Number(value)
const displayNumber=(value:number)=>Number.isFinite(value)?value:''
const fmtTableValue=(value:number|string|null,column:ResultTableColumn)=>{
  if(value===null||value===undefined)return '待完成'
  if(typeof value==='string')return value
  if(column.format==='percent')return new Intl.NumberFormat('zh-CN',{style:'percent',minimumFractionDigits:2,maximumFractionDigits:2}).format(value)
  if(column.format==='score')return fmt(value,1)
  if(column.format==='integer')return new Intl.NumberFormat('zh-CN',{maximumFractionDigits:0}).format(value)
  return `${fmt(value,2)}${column.unit?' '+column.unit:column.format==='ratio'?' 倍':''}`
}
const basename=(file:InputFile|undefined)=>file?.name||'未知文件'
const mappingReady=(mapping:Mapping)=>Boolean(mapping.sheet&&mapping.product_col&&Object.values(mapping.fields).some(Boolean))
const CALCULATION_MIN_DURATION=5000
const CALCULATION_STAGES=[
  {label:'读取已确认的数据来源',detail:'加载本批次的 Excel 原始数据与字段映射'},
  {label:'核验计算规则与口径',detail:'确认模型版本、评分参数与来源选择'},
  {label:'计算指标得分及排名',detail:'执行各评分项计算、并列处理与赋分'},
  {label:'汇总评分结果',detail:'生成评分明细、总览与可追溯计算链'},
]
const wait=(milliseconds:number)=>new Promise<void>(resolve=>window.setTimeout(resolve,milliseconds))
const DEFAULT_EVALUATION_YEAR=new Date().getFullYear()-1

const RADAR_RADIUS=39*1.2

function radarPoint(index:number,total:number,radius:number){
  const angle=(Math.PI*2*index/total)-Math.PI/2
  return {x:50+Math.cos(angle)*radius,y:50+Math.sin(angle)*radius}
}

function RadarProductCard({product,indicators,traces,onTrace}:{product:string;indicators:IndicatorScore[];traces:Trace[];onTrace:(trace:Trace|null)=>void}){
  const axes=indicators.filter(item=>item.weight>0),total=axes.length
  const calculated=axes.filter(item=>item.ratio!==null&&Number.isFinite(item.ratio))
  const points=axes.map((metric,index)=>({metric,index,outer:radarPoint(index,total,RADAR_RADIUS),point:metric.ratio===null?null:radarPoint(index,total,RADAR_RADIUS*Math.max(0,Math.min(metric.ratio,1)))}))
  const allCalculated=calculated.length===total
  const polygon=allCalculated?points.map(item=>`${item.point!.x},${item.point!.y}`).join(' '):''
  return <section className="panel product-card radar-card" aria-label={`${product}评分雷达图`}><div className="product-title"><h2>{product}</h2><span>{calculated.length}/{total} 已计算</span></div><div className="radar-stage">{total>=3?<svg className="radar-chart" viewBox="-28 -10 156 125" role="img" aria-label={`${product}各指标满分占比雷达图`}><g className="radar-grid">{[.25,.5,.75,1].map(ratio=><polygon key={ratio} points={axes.map((_,index)=>{const point=radarPoint(index,total,RADAR_RADIUS*ratio);return `${point.x},${point.y}`}).join(' ')}/>)}</g><g className="radar-axis">{points.map(item=><line key={item.metric.key} x1="50" y1="50" x2={item.outer.x} y2={item.outer.y}/>)}</g>{polygon&&<polygon className="radar-shape" points={polygon}/>}<g className="radar-partial">{!allCalculated&&points.map(item=>item.point&&<line key={item.metric.key} x1="50" y1="50" x2={item.point.x} y2={item.point.y}/>)}</g><g className="radar-points">{points.map(item=>item.point&&<circle key={item.metric.key} cx={item.point.x} cy={item.point.y} r="1.7"><title>{item.metric.label}：{fmtPercent(item.metric.ratio!)}，{fmt(item.metric.weighted_score??undefined,1)}／{fmt(item.metric.weight,1)} 分</title></circle>)}</g><g className="radar-labels">{points.map(item=>{const label=radarPoint(item.index,total,55);return <text key={item.metric.key} x={label.x} y={label.y} textAnchor={label.x<43?'end':label.x>57?'start':'middle'} dominantBaseline={label.y<45?'auto':'hanging'}>{item.metric.label.split(/[／/]/).flatMap(part=>part.match(/.{1,6}/gu)||[]).map((part,index)=><tspan key={index} x={label.x} dy={index?5:0}>{part}</tspan>)}</text>})}</g><g className="radar-scale">{[.25,.5,.75,1].map(ratio=><text key={ratio} x="51" y={50-RADAR_RADIUS*ratio}>{ratio*100}%</text>)}</g></svg>:<p>至少 3 项非零权重指标才能绘制雷达图，请查看指标明细。</p>}</div><div className="radar-detail-overlay"><div className="radar-overlay-head"><strong>{product}</strong><span>得分占该指标满分比例</span></div><div className="metric-rows">{indicators.map(metric=>{const item=traces.find(trace=>metric.metric_keys.includes(trace.metric));return <button key={metric.key} onClick={()=>onTrace(item||null)} disabled={!item}><span>{metric.label}</span><strong>{metric.weight===0?'不计权重':metric.ratio===null?'待完成':fmtRadarPercent(metric.ratio)}<small>（{fmt(metric.weighted_score??undefined,1)} / {fmt(metric.weight,1)} 分）</small></strong>{item&&<ChevronRight size={15}/>}</button>})}</div></div></section>
}

function TraceDrawer({trace,run,onClose}:{trace:Trace;run:Run|null;onClose:()=>void}){
  const contribution=trace.contribution
  return <div className="overlay" onMouseDown={onClose}><aside className="drawer trace-drawer" onMouseDown={e=>e.stopPropagation()}><div className="drawer-head"><div><div className="eyebrow">CALCULATION TRACE</div><h2>{trace.product} · {trace.indicator_label||trace.metric}</h2><p>最终第 {trace.rank} 名 · 原始赋分 {fmt(trace.score,1)} 分{contribution&&` · 总分贡献 ${fmt(contribution.weighted_score??undefined,1)} 分`}</p></div><button onClick={onClose} aria-label="关闭"><X/></button></div><div className="trace-steps">{trace.steps.map((step,i)=><div className="trace-step" key={i}><span className="step-no">{String(i+1).padStart(2,'0')}</span><div><strong>{step.display_label||step.label}</strong><div className="step-value">{fmtStep(step)} {step.rank!==undefined&&<span>第 {step.rank} 名</span>} {step.score!==undefined&&<span>原始赋分 {fmt(step.score,1)} 分</span>}</div>{step.formula&&<small>公式：{step.formula}</small>}{step.source&&<small>来源：{basename(run?.files.find(f=>f.id===step.source?.file_id))} · {step.source.cell} · 原名 {step.source.source_name}</small>}</div></div>)}{contribution&&<section className="trace-contribution"><h3>计入总分的贡献</h3>{contribution.components.map(component=><p key={component.metric}>{contribution.components.length>1&&`${component.label}：`}原始赋分 {fmt(component.raw_score??undefined,1)} ÷ 基础满分 {fmt(component.first_score??undefined,1)}{component.ratio!==null&&` = ${fmtPercent(component.ratio)}`}</p>)}<p>指标满分（权重）：{fmt(contribution.weight,1)} 分{contribution.components.length>1&&'；各子项满分占比取平均后加权。'}</p><strong>{contribution.components.every(c=>c.ratio!==null)?`${contribution.components.length>1?'平均满分占比': '满分占比'} ${fmtPercent(contribution.components.reduce((sum,c)=>sum+(c.ratio||0),0)/contribution.components.length)} × ${fmt(contribution.weight,1)} 分 = ${fmt(contribution.weighted_score??undefined,1)} 分`:'缺少原始赋分，暂不能解释完整贡献。'}</strong><small>总表显示加权贡献分；雷达图显示贡献分 ÷ 指标满分。{contribution.weight===0?'此指标权重为 0，不计入总分，也不绘制雷达轴。':''}</small></section>}</div><div className="drawer-foot">并列同分，后续名次跳位；按批次绑定模型的规则计算。</div></aside></div>
}

function ResultTablePanel({table,result,onTrace}:{table:ResultTable;result:Result;onTrace:(trace:Trace|null)=>void}){
  const total=table.kind==='total'
  const columnClass=(index:number)=>total?(index===table.columns.length-2?'result-total-column':index===table.columns.length-1?'result-rank-column':''):''
  const minWidth=total?`calc(var(--result-index-width) + var(--result-product-width) + var(--result-total-width) + var(--result-rank-width) + ${(table.columns.length-4)*132}px)`:Math.max(760,table.columns.length*132)
  return <section className="panel table-panel result-table-panel">
    <div className="panel-head"><div><h2>{table.title}</h2><p>{total?'按总分排名展示；产品线、总分与排名固定，点击指标得分可查看计算依据。':'表头、排序与数据均和导出文件中的同维度 Sheet 保持一致。'}</p></div><span className="count-badge">{table.rows.length} 条产品线</span></div>
    <div className="table-scroll" tabIndex={0} role="region" aria-label={total?'评分总表，可横向滚动':'评分维度表，可横向滚动'}>
      <table className={'score-table export-result-table'+(total?' results-total-table':'')} style={{minWidth,width:total?minWidth:undefined}}>
        {total&&<colgroup>{table.columns.map((_,index)=><col key={index} style={{width:index===0?'var(--result-index-width)':index===1?'var(--result-product-width)':index===table.columns.length-2?'var(--result-total-width)':index===table.columns.length-1?'var(--result-rank-width)':132}}/>)}</colgroup>}
        <thead><tr>{table.columns.map((column,index)=><th scope="col" className={columnClass(index)} key={`${column.label}-${index}`}>{column.label}</th>)}</tr></thead>
        <tbody>{table.rows.map(row=><tr key={row.product}>{row.values.map((value,index)=>{
          const column=table.columns[index],trace=column.trace_metric?result.traces.find(item=>item.product===row.product&&item.metric===column.trace_metric):undefined
          return <td key={`${column.label}-${index}`} className={[columnClass(index),value===null?'blocked':''].filter(Boolean).join(' ')}>{index===1?<strong>{String(value)}</strong>:trace&&column.role==='score'?<button className="score-link" onClick={()=>onTrace(trace)}>{fmtTableValue(value,column)}</button>:<span className={value===null?'pending':''}>{fmtTableValue(value,column)}</span>}</td>
        })}</tr>)}</tbody>
      </table>
    </div>
  </section>
}

function ResultStatusSummary({result}:{result:Result}){
  const status={partial:'分项结果',trial:'试算结果',complete:'完整结果'}[result.result_status]
  return <section className={'result-status-summary '+result.result_status} aria-label="结果状态">
    <div className="result-status-line"><strong className="result-status-label">{result.result_status==='complete'?<Check size={16}/>:<CircleAlert size={16}/>} {status}</strong>
      <dl><div><dt>已计算指标</dt><dd>{result.calculated_indicator_count} / {result.indicator_count} 项</dd></div><div><dt>覆盖权重</dt><dd>{fmtRaw(result.covered_points)} / {fmtRaw(result.weight_total)} 分</dd></div></dl>
    </div><p>{result.status_note}</p>
  </section>
}

function App(){
  const [config,setConfig]=useState<Config|null>(null),[runs,setRuns]=useState<RunSummary[]>([]),[models,setModels]=useState<EvaluationModel[]>([]),[run,setRun]=useState<Run|null>(null)
  const [view,setView]=useState<View>('batches'),[navOpen,setNavOpen]=useState(false),[error,setError]=useState(''),[busy,setBusy]=useState(false),[calculationStage,setCalculationStage]=useState<number|null>(null),[activeFile,setActiveFile]=useState<string|null>(null),[purposeFile,setPurposeFile]=useState<string|null>(null)
  const [trace,setTrace]=useState<Trace|null>(null),[scoreView,setScoreView]=useState<ScoreView>('table'),[scoreSection,setScoreSection]=useState('total'),[resultTables,setResultTables]=useState<ResultTable[]>([]),[issueFilter,setIssueFilter]=useState<'all'|'error'|'warning'>('all')
  const [newYear,setNewYear]=useState(DEFAULT_EVALUATION_YEAR),[newName,setNewName]=useState(`${DEFAULT_EVALUATION_YEAR} 年重点产品后评估`),[newModelId,setNewModelId]=useState(''),[notes,setNotes]=useState({analysis:'',recommendations:'',review:''}),[confirmAction,setConfirmAction]=useState<ConfirmAction|null>(null)
  const batchListScroll=useRef(0)
  const modelLeaveGuard=useRef<LeaveGuard|null>(null)
  const [uploadAttempts,setUploadAttempts]=useState<UploadAttempt[]>([])
  useEffect(()=>{Promise.all([api<Config>('/config'),api<RunSummary[]>('/runs'),api<EvaluationModel[]>('/models')]).then(([c,r,m])=>{setConfig(c);setRuns(r);setModels(m);setNewModelId(m.find(item=>item.status==='published')?.id||'')}).catch(e=>setError(e.message))},[])
  useLayoutEffect(()=>{window.scrollTo(0,view==='batches'?batchListScroll.current:0)},[view,run?.id])
  function enterRun(value:Run,tables:ResultTable[]){
    setUploadAttempts([])
    batchListScroll.current=window.scrollY
    setRun(value);setNotes({analysis:value.notes.analysis||'',recommendations:value.notes.recommendations||'',review:value.notes.review||''})
    setActiveFile(null);setPurposeFile(null);setTrace(null);setIssueFilter('all');setScoreSection('total');setScoreView('table');setResultTables(tables);setNavOpen(false)
    setView((value as Run&{progress?:RunProgress}).progress?.next_view||(value.report?'report':value.result?'overview':value.files.length?'validation':'upload'))
  }
  async function openRun(id:string){
    if(busy)return
    setBusy(true);setError('')
    try{const value=await api<Run>('/runs/'+id);const tables=value.result?await api<ResultTable[]>(`/runs/${id}/result-tables`):[];enterRun(value,tables)}
    catch(e){setError((e as Error).message)}finally{setBusy(false)}
  }
  const canCreate=Number.isInteger(newYear)&&newYear>=2020&&newYear<=2100&&Boolean(newName.trim())&&models.some(model=>model.id===newModelId&&model.status==='published')
  async function create(event:React.FormEvent){
    event.preventDefault()
    if(busy||!canCreate)return
    setBusy(true);setError('')
    try{const value=await api<Run>('/runs','POST',{year:newYear,name:newName.trim(),model_id:newModelId});setRuns(current=>[value,...current]);enterRun(value,[])}
    catch(e){setError((e as Error).message)}finally{setBusy(false)}
  }
  async function returnToBatches(){
    if(busy)return
    setView('batches');setRun(null);setResultTables([]);setActiveFile(null);setPurposeFile(null);setTrace(null);setNavOpen(false);setError('');setBusy(true)
    try{setRuns(await api<RunSummary[]>('/runs'))}catch(e){setError((e as Error).message)}finally{setBusy(false)}
  }
  function navigate(id:View){
    if(id===view)return
    const proceed=()=>{if(view==='batches')batchListScroll.current=window.scrollY;if(id==='batches')void returnToBatches();else{setView(id);setNavOpen(false)}}
    if(view==='models'&&modelLeaveGuard.current)modelLeaveGuard.current(proceed)
    else proceed()
  }
  function requestDeleteRun(item:RunSummary){setConfirmAction({title:'删除评估批次',subject:`${item.year} · ${item.name}`,warning:'批次记录、评分结果和已上传文件都会永久删除，且无法恢复。',confirmLabel:'确认删除批次',onConfirm:async()=>{setBusy(true);try{await api(`/runs/${item.id}`,'DELETE');setRuns(await api<RunSummary[]>('/runs'));if(run?.id===item.id){setRun(null);setView('batches')}}finally{setBusy(false)}}})}
  async function upload(files:File[],replacedFiles:File[]=files){
    if(!run||!files.length||busy)return
    setBusy(true);setError('')
    const runId=run.id
    const record=(outcomes:UploadOutcome[])=>setUploadAttempts(current=>[
      ...current.filter(item=>!replacedFiles.includes(item.file)),
      ...outcomes.map(item=>({...item,file:files[item.index]})),
    ])
    try{
      const body=new FormData();files.forEach(file=>body.append('files',file))
      const value=await api<Run&{upload_results:UploadOutcome[]}>(`/runs/${runId}/files`,'POST',body)
      setRun(value);record(value.upload_results)
      if(!value.result)setResultTables([])
    }catch{
      // A lost response may follow successful persistence. Refresh the visible
      // records; server-side content matching makes retry safe in either case.
      try{const latest=await api<Run>(`/runs/${runId}`);setRun(latest);if(!latest.result)setResultTables([])}catch{/* Keep the last known list. */}
      record(files.map((file,index)=>({index,name:file.name,status:'failed',message:'未能确认上传结果，请重试；已保存的相同内容会自动跳过',retryable:true})))
    }finally{setBusy(false)}
  }
  function requestDeleteFile(file:InputFile){if(!run)return;setConfirmAction({title:'删除已上传文件',subject:file.name,warning:'系统将删除本批次保存的文件副本、字段映射和相关来源选择；你电脑中的原始 Excel 不会受到影响。',confirmLabel:'确认删除文件',onConfirm:async()=>{setBusy(true);try{setRun(await api<Run>(`/runs/${run.id}/files/${file.id}`,'DELETE'));setActiveFile(current=>current===file.id?null:current);setPurposeFile(current=>current===file.id?null:current)}finally{setBusy(false)}}})}
  async function saveMapping(file:InputFile,mapping:Mapping){if(!run)return;setBusy(true);try{setRun(await api<Run>(`/runs/${run.id}/files/${file.id}/mapping`,'PUT',mapping));setActiveFile(null)}catch(e){setError((e as Error).message)}finally{setBusy(false)}}
  async function saveTargets(file:InputFile,targets:string[]){if(!run)return;setBusy(true);try{setRun(await api<Run>(`/runs/${run.id}/files/${file.id}/targets`,'PUT',{targets}));setPurposeFile(null)}catch(e){setError((e as Error).message)}finally{setBusy(false)}}
  async function validate(){if(!run)return;setBusy(true);setError('');try{setRun(await api<Run>(`/runs/${run.id}/validate`,'POST'));setView('validation')}catch(e){setError((e as Error).message)}finally{setBusy(false)}}
  async function calculate(){
    if(!run)return
    const startedAt=performance.now()
    const stageTimers=CALCULATION_STAGES.slice(1).map((_,index)=>window.setTimeout(()=>setCalculationStage(index+1),1000*(index+1)))
    setBusy(true);setError('');setCalculationStage(0);setView('overview')
    try{
      const result=await api<Result>(`/runs/${run.id}/calculate`,'POST')
      await wait(Math.max(0,CALCULATION_MIN_DURATION-(performance.now()-startedAt)))
      setRun({...run,result,report:null});setScoreSection('total');setScoreView('table');setResultTables(await api<ResultTable[]>(`/runs/${run.id}/result-tables`))
    }catch(e){setError((e as Error).message);setView('validation')
    }finally{stageTimers.forEach(timer=>window.clearTimeout(timer));setCalculationStage(null);setBusy(false)}
  }
  async function chooseSource(field:string,fileId:string){if(!run)return;setBusy(true);try{const choices={...run.source_choices,[field]:fileId};setRun(await api<Run>(`/runs/${run.id}/source-choices`,'PUT',{choices}))}catch(e){setError((e as Error).message)}finally{setBusy(false)}}
  async function generateReport(){if(!run||busy)return;if(!run.result){setError('请先计算评分结果，再生成报告');return}setBusy(true);setError('');try{setRun(await api<Run>(`/runs/${run.id}/report`,'POST',notes))}catch(e){setError((e as Error).message)}finally{setBusy(false)}}
  const active=run?.files.find(f=>f.id===activeFile)
  const purpose=run?.files.find(f=>f.id===purposeFile)
  const dimensions=run?.model?.definition.dimensions.filter(item=>item.enabled)!||[]
  const targetLabel=(key:string)=>dimensions.find(item=>item.key===key)?.label||config?.metrics.find(item=>item.key===key)?.label||key
  const result=run?.result
  const reportNotesChanged=Boolean(run)&&['analysis','recommendations','review'].some(key=>(notes[key as keyof typeof notes]||'')!==(run?.notes[key as keyof typeof notes]||''))
  const validation=run?.validation||null,validationPassed=validation?.status==='passed'
  const errors=validation?.error_count||0,warnings=validation?.warning_count||0
  const visibleIssues=validation?.issues.filter(i=>issueFilter==='all'||i.level===issueFilter)||[]
  const products=config?.products||[]
  const activeResultTable=resultTables.find(table=>table.key===scoreSection)||resultTables[0]

  const conflictsByField=validation?.conflicts.reduce<Record<string,Result['conflicts']>>((acc,item)=>{(acc[item.field]??=[]).push(item);return acc},{})||{}
  const inWorkspace=Boolean(run)&&view!=='batches'&&view!=='models'
  const workflowNav:[View,string,React.ReactNode][]=[['upload','数据上传',<UploadCloud/>],['validation','数据校验',<ShieldCheck/>],['overview','评估结果',<LayoutDashboard/>],['report','报告审阅与导出',<FileText/>]]
  const managementNav:[View,string,React.ReactNode][]=[['batches','评估批次',<FolderOpen/>],['models','模型中心',<SlidersHorizontal/>]]
  const nav=inWorkspace?workflowNav:managementNav
  const viewLabel=nav.find(n=>n[0]===view)?.[1]
  return <div className={'app-shell '+(inWorkspace?'batch-workspace ':'management-shell ')+(navOpen?'nav-open':'')}>
    <button className="mobile-scrim" aria-label="关闭导航" onClick={()=>setNavOpen(false)}/>
    <aside className="sidebar" id="workflow-nav">
      <div className="brand"><div className="brand-mark">PE</div><div><strong>产品后评估</strong><small>本地评估工作台</small></div></div>
      <button className="side-close" aria-label="关闭导航" onClick={()=>setNavOpen(false)}><X size={18}/></button>
      <div className="side-label">{inWorkspace?'评估流程':'系统管理'}{inWorkspace&&<span>01—04</span>}</div>
      <nav aria-label={inWorkspace?'评估流程':'系统管理'}>{nav.map(([id,label,icon],i)=><button key={id} className={'nav-item '+(view===id?'active':'')} onClick={()=>navigate(id)} disabled={busy} aria-current={view===id?(inWorkspace?'step':'page'):undefined}><span className="nav-icon">{icon}</span><span>{label}</span>{inWorkspace&&<em>{String(i+1).padStart(2,'0')}</em>}</button>)}</nav>
      <div className="side-bottom"><span className="local-dot"/>仅在本机运行<br/><small>文件及评估记录不上传云端</small></div>
    </aside>
    <main className="main">
      <header className="topbar">
        <button className="mobile-nav-toggle" aria-label="打开导航" aria-expanded={navOpen} aria-controls="workflow-nav" onClick={()=>setNavOpen(true)}><Menu size={18}/><span>{inWorkspace?'流程':'菜单'}</span></button>
        {inWorkspace&&<button className="workspace-back" onClick={returnToBatches} disabled={busy}><ArrowLeft size={16}/><span>返回批次列表</span></button>}
        {!inWorkspace&&<div className="crumb">产品后评估 <ChevronRight size={14}/> {viewLabel}</div>}
        {inWorkspace&&run&&<div className="top-right batch-context" aria-label="当前评估批次"><span className="batch-context-name" title={run.name}>{run.name}</span><span className="run-chip">评估年度 {run.year}</span><span className="rule-chip" title={run.model?.name}>模型 {run.model?.version||'历史规则'}</span></div>}
      </header>
      <div className="page"><div className="page-head"><div><div className="eyebrow">POST-EVALUATION / WORKSPACE</div><h1>{viewLabel}</h1><p>{view==='models'?'管理模型版本、模拟排名影响，并在发布后锁定规则。':view==='batches'?'创建或打开年度评估批次，当前版本先覆盖 13 条重点产品。':run?.name||'请选择评估批次'}</p></div>{run&&view==='validation'&&<button className="btn btn-dark" onClick={validate} disabled={busy||!run.files.length}><RefreshCw size={16}/> {validation?'重新校验':'开始数据校验'}</button>}</div>
      {error&&<div className="error-banner"><CircleAlert size={17}/>{error}<button onClick={()=>setError('')} aria-label="关闭"><X size={16}/></button></div>}
      {view==='models'&&<ModelCenter registerLeaveGuard={guard=>{modelLeaveGuard.current=guard}} models={models} runs={runs} calculationTypes={config?.calculation_types||[]} onModelsChange={next=>{setModels(next);if(!next.some(item=>item.id===newModelId&&item.status==='published'))setNewModelId(next.find(item=>item.status==='published')?.id||'')}} requestConfirm={setConfirmAction}/>}
      {view==='batches'&&<>
        <section className="panel create-panel"><div className="panel-head"><div><h2>创建评估批次</h2><p>评估年度默认选择上一自然年度；批次创建后会锁定所选模型版本。</p></div><Plus size={18}/></div>
          <form onSubmit={create}><div className="form-grid batch-form-grid">
            <label>评估年度<input type="number" min="2020" max="2100" step="1" required value={displayNumber(newYear)} disabled={busy} onChange={e=>setNewYear(numberValue(e.target.value))}/></label>
            <label>批次名称<input required maxLength={80} value={newName} disabled={busy} onChange={e=>setNewName(e.target.value)}/></label>
            <label>已发布模型<SystemSelect label="已发布模型" value={newModelId} onChange={e=>setNewModelId(e.target.value)} disabled={busy}>{models.filter(item=>item.status==='published').map(item=><option key={item.id} value={item.id}>{item.version} · {item.name}</option>)}</SystemSelect></label>
          </div><button className="btn btn-dark" type="submit" disabled={busy||!canCreate}>创建批次 <ArrowRight size={16}/></button></form>
        </section>
        <section className="panel run-list" aria-busy={busy}><div className="panel-head"><h2>已有批次</h2><span>{runs.length} 个批次</span></div>{runs.length?runs.map(item=><div key={item.id} className="run-row-wrap"><button className="run-row run-row-main" onClick={()=>openRun(item.id)} disabled={busy}><span className="run-year">{item.year}</span><span><strong>{item.name}</strong><small>数据年度 {item.data_year} · 最近更新 {item.updated_at.slice(0,16).replace('T',' ')} · {models.find(model=>model.id===item.model_id)?.version||'历史规则'}</small></span><span className="run-progress"><strong>{item.progress?.status==='results_ready'||item.progress?.status==='report_ready'?'已完成':'待完成'}</strong></span></button><button className="delete-icon" onClick={()=>requestDeleteRun(item)} disabled={busy} title="删除评估批次" aria-label={`删除批次 ${item.name}`}><Trash2 size={16}/></button></div>):<div className="empty">{config?'尚无评估批次。填写上方信息创建批次开始评估。':'正在加载评估批次…'}</div>}</section></>}
  {run&&view==='upload'&&<>
    <div className="notice"><strong>批量上传后自动匹配评分指标</strong><span>系统会自动识别每份文件的用途，根据模型规则确定计算需要的数据，缺失或有歧义时才需要确认。</span></div>
    {!run.files.length&&!uploadAttempts.length?<section className="panel upload-first"><div className="panel-head"><div><h2>上传本地表</h2><p>可一次选择多份 Excel。系统会自动将识别到的数据关联到当前模型的评分指标。</p></div></div><label className={'dropzone '+(busy?'disabled':'')}><UploadCloud size={30}/><strong>{busy?'正在识别工作簿…':'点击选择 Excel 工作簿'}</strong><span>支持批量选择 .xlsx · 单文件上限 30 MB</span><input type="file" accept=".xlsx" multiple disabled={busy} onChange={e=>{const files=Array.from(e.target.files||[]);if(files.length)upload(files);e.target.value=''}}/></label></section>:
      <section className="panel match-results upload-file-list" aria-label="上传文件列表" aria-live="polite">
        <div className="panel-head"><div><h2>上传文件</h2><p>已上传 {run.files.length} 份{uploadAttempts.some(item=>item.status==='failed')&&` · 失败 ${uploadAttempts.filter(item=>item.status==='failed').length} 份`} · 支持 .xlsx，单文件上限 30 MB</p></div><label className={'btn btn-dark upload-more '+(busy?'disabled':'')}><UploadCloud size={16}/>{busy?'正在识别…':'继续上传文件'}<input type="file" accept=".xlsx" multiple disabled={busy} onChange={e=>{const files=Array.from(e.target.files||[]);if(files.length)upload(files);e.target.value=''}}/></label></div>
        {run.files.map(file=>{const ready=mappingReady(file.mapping),labels=file.targets.map(targetLabel).join('、'),suggestions=(file.mapping.suggested_targets||[]).filter(key=>!file.targets.includes(key)).map(targetLabel).join('、'),provided=(file.mapping.provided_summary||[]).join('、'),formal=Boolean(file.targets.length),needsMapping=formal&&!ready;const purpose=labels?`关联指标：${labels}`:suggestions?`系统建议：${suggestions}（待确认）`:'未识别到可用评分指标';return <div className={'file-row source-result '+(formal&&ready?'ready':'attention')} key={file.id}><FileSpreadsheet size={20}/><span><strong>{file.name}</strong>{uploadAttempts.some(item=>item.status==='duplicate'&&item.file_id===file.id)&&<small className="upload-duplicate-note">{uploadAttempts.filter(item=>item.status==='duplicate'&&item.file_id===file.id).map(item=>`“${item.name}”与此文件内容相同，已跳过`).join('；')}</small>}<small>{purpose} · {formal?(ready?`提供：${provided||'已识别的原始数据'}`:'计算所需数据尚未匹配完整'):'不参与本次评分'} · {file.uploaded_at.slice(0,16).replace('T',' ')}</small></span><div className="source-actions">{formal&&ready?<><span className="source-status ready"><Check size={15}/>可用于计算</span><button className="text-action" onClick={()=>setActiveFile(file.id)}>查看依据</button></>:needsMapping?<button className="btn btn-light source-confirm" onClick={()=>setActiveFile(file.id)}>确认依据 <ArrowRight size={14}/></button>:<span className="source-status">待确认用途</span>}<button className="text-action" onClick={()=>setPurposeFile(file.id)}>调整用途</button><button className="delete-file" onClick={()=>requestDeleteFile(file)} disabled={busy} title="删除文件" aria-label={`删除文件 ${file.name}`}><Trash2 size={15}/></button></div></div>})}
        {uploadAttempts.filter(item=>item.status==='failed').map((item,index)=><div className="file-row source-result upload-failed" key={`failed-${index}`}><FileSpreadsheet size={20}/><span><strong>{item.name}</strong><small>{item.message}</small></span><div className="source-actions"><span className="source-status"><CircleAlert size={15}/>上传失败</span>{item.retryable?<button className="text-action" disabled={busy} onClick={()=>upload([item.file])}>重试上传</button>:<label className={'text-action upload-reselect '+(busy?'disabled':'')}>选择修正文件<input type="file" accept=".xlsx" disabled={busy} onChange={e=>{const replacement=e.target.files?.[0];if(replacement)upload([replacement],[item.file]);e.target.value=''}}/></label>}</div></div>)}
      </section>}
    <div className="next-row"><button className="btn btn-dark" onClick={validate} disabled={busy||!run.files.length}><ShieldCheck size={16}/> 数据校验 <ArrowRight size={16}/></button><div className="validation-state">{validation?<>{validationPassed?<Check size={16}/>:<CircleAlert size={16}/>} {validationPassed?`已于 ${validation.validated_at.slice(0,16).replace('T',' ')} 校验通过`:`校验未通过：${errors} 个问题待处理`}</> : <><CircleAlert size={16}/> 文件用途或映射变更后，请统一执行数据校验</>}</div></div>
  </>}
  {run&&view==='validation'&&<><div className="stat-grid"><Stat label="错误 / 待解决" value={errors} detail="缺失、重复、来源冲突或零基数" tone="error"/><Stat label="复核提示" value={warnings} detail="负基数按绝对值分母计算" tone="warn"/><Stat label="已上传来源" value={run.files.length} detail="本批次本地 Excel 文件"/><Stat label="校验状态" value={validationPassed?'通过':validation?.status==='failed'?'未通过':'待校验'} detail={validationPassed?'可进入计算':'调整用途、映射或来源后需重新校验'}/></div>{!validation?<section className="panel centered"><ShieldCheck size={32}/><h2>尚未运行数据校验</h2><p>请先完成文件用途与字段映射调整；系统将统一核对完整性、重复、来源冲突和计算口径。</p><button className="btn btn-dark" onClick={validate} disabled={!run.files.length||busy}>开始数据校验</button></section>:<><section className="panel"><div className="panel-head"><div><h2>来源冲突</h2><p>同一字段不同文件数值不一致时，按字段选择本批次采用来源；选择后需要重新校验。</p></div></div>{validation.conflicts.length?Object.entries(conflictsByField).map(([field,items])=><div className="conflict-row" key={field}><div><strong>{field}</strong><small>影响 {items.length} 条产品线</small></div><SystemSelect label={`${field}采用来源`} value={run.source_choices[field]||''} onChange={e=>chooseSource(field,e.target.value)}><option value="">选择来源文件</option>{run.files.filter(f=>items.some(i=>i.candidates.some(c=>c.file_id===f.id))).map(f=><option key={f.id} value={f.id}>{f.name}</option>)}</SystemSelect></div>):<div className="success-line"><Check size={16}/> 当前没有未解决的来源冲突</div>}</section><section className="panel issues-panel"><div className="panel-head"><div><h2>校验清单</h2><p>错误会阻止计算；提示保留原公式与结果，供负责人复核。</p></div><div className="segmented">{(['all','error','warning'] as const).map(x=><button className={issueFilter===x?'active':''} key={x} onClick={()=>setIssueFilter(x)}>{x==='all'?'全部':x==='error'?'错误':'提示'}</button>)}</div></div>{visibleIssues.length?<div className="issue-list">{visibleIssues.map((issue,i)=><div className="issue-row" key={i}><span className={'issue-dot '+issue.level}/><span><strong>{issue.message}</strong><small>{issue.code} {issue.field&&`· ${issue.field}`}</small></span></div>)}</div>:<div className="empty">没有该类校验事项</div>}</section><div className="next-row"><button className="btn btn-light" onClick={validate} disabled={busy}><RefreshCw size={16}/> 重新校验</button>{validationPassed?<button className="btn btn-dark" onClick={calculate} disabled={busy}>开始计算 <ArrowRight size={16}/></button>:<span className="validation-blocked"><CircleAlert size={16}/> 请处理全部错误后重新校验</span>}</div></>}</>}
  {run&&view==='overview'&&<>{calculationStage!==null?<CalculationProgress stage={calculationStage}/>:result?<><ResultStatusSummary result={result}/><div className="result-section-tabs" role="tablist" aria-label="评分结果维度">{resultTables.map(table=><button key={table.key} className={activeResultTable?.key===table.key?'active':''} role="tab" aria-selected={activeResultTable?.key===table.key} onClick={()=>setScoreSection(table.key)}>{table.label}</button>)}</div>{activeResultTable?.kind==='total'&&<div className="score-view-toolbar"><div className="segmented" role="tablist" aria-label="总分展示方式"><button className={scoreView==='table'?'active':''} role="tab" aria-selected={scoreView==='table'} onClick={()=>setScoreView('table')}>表格</button><button className={scoreView==='radar'?'active':''} role="tab" aria-selected={scoreView==='radar'} onClick={()=>setScoreView('radar')}>雷达图</button></div></div>}{activeResultTable?(activeResultTable.kind==='dimension'||scoreView==='table'?<ResultTablePanel table={activeResultTable} result={result} onTrace={setTrace}/>:<><div className="detail-toolbar"><span>共 {products.length} 条产品线</span><span>各轴：指标得分 ÷ 该指标满分 · 0–100%；零权重不绘制，超出范围的数值见明细。</span></div><div className="detail-grid radar-grid-list">{products.map(product=><RadarProductCard key={product} product={product} indicators={result.indicator_scores?.[product]||[]} traces={result.traces.filter(trace=>trace.product===product)} onTrace={setTrace}/>)}</div></>):<section className="panel centered"><LayoutDashboard size={32}/><h2>正在准备结果表</h2><p>页面表格将与导出文件保持一致。</p></section>}</>:<section className="panel centered"><LayoutDashboard size={32}/><h2>还没有评分结果</h2><p>上传数据并确认映射后即可计算。</p><button className="btn btn-dark" onClick={()=>setView('upload')}>前往上传</button></section>}</>}
  {run&&view==='report'&&<><div className="report-layout"><section className="panel"><div className="panel-head"><div><h2>报告审阅</h2><p>以下三项均为选填。点击生成报告后，将评分结果与审阅内容汇总为后评估报告。</p></div><FileText size={19}/></div><label className="note-label">经营原因分析<textarea value={notes.analysis} onChange={e=>setNotes({...notes,analysis:e.target.value})} placeholder="记录已核实的业务原因，不填写未经确认的判断。"/></label><label className="note-label">产品处置建议<textarea value={notes.recommendations} onChange={e=>setNotes({...notes,recommendations:e.target.value})} placeholder="由负责人审阅后填写。"/></label><label className="note-label">负责人审阅意见<textarea value={notes.review} onChange={e=>setNotes({...notes,review:e.target.value})} placeholder="记录确认人、日期及需复核事项。"/></label><button className="btn btn-dark" onClick={generateReport}>{busy?'生成中…':'生成报告'}</button>{run.report&&reportNotesChanged&&<p role="status">审阅内容已修改，请重新生成报告。</p>}</section><section className="panel export-panel"><div className="panel-head"><div><h2>导出交付物</h2><p>{result?.model_summary?.ready?'已生成完整试算结果，并标注能力评测为模拟值。':'阶段性结果会明确标注未形成总分。'}</p></div><ArrowDownToLine size={20}/></div><div className="export-item"><FileSpreadsheet size={23}/><div><strong>评分总表 .xlsx</strong><small>沿用附件样式，按当前模型动态生成一维度一 sheet 及总表</small></div><a className={'btn btn-light '+(!result?'disabled':'')} href={result?`/api/runs/${run.id}/export.xlsx`:undefined}>导出</a></div>{run.report&&!reportNotesChanged&&<div className="export-item"><FileText size={23}/><div><strong>后评估报告 .docx</strong><small>测算结果、口径、校验与人工审阅区</small></div><a className={'btn btn-light '+(!result?'disabled':'')} href={`/api/runs/${run.id}/report.docx`}>导出</a></div>}<div className="report-warning"><CircleAlert size={17}/><span>{result?.model_summary?.ready?'能力评测当前使用系统模拟值，总分与排名仅用于跑通流程，不作为正式业务结论。':'缺失指标不计零分，不会生成未经核实的总分与结论。'}</span></div></section></div></>}
  </div></main>
  {active&&<MappingDrawer file={active} evaluationYear={run!.year} fields={active.mapping.fields} onClose={()=>setActiveFile(null)} onSave={mapping=>saveMapping(active,mapping)}/>}
  {purpose&&<FilePurposeDrawer file={purpose} dimensions={dimensions} onClose={()=>setPurposeFile(null)} onSave={targets=>saveTargets(purpose,targets)}/>}
  {trace&&<TraceDrawer trace={trace} run={run} onClose={()=>setTrace(null)}/>}
  {confirmAction&&<ConfirmDialog action={confirmAction} onClose={()=>setConfirmAction(null)}/>}
  </div>
}

function Stat({label,value,detail,tone}:{label:string;value:string|number;detail:string;tone?:string}){return <div className={'stat '+(tone||'')}><span>{label}</span><strong>{value}</strong><small>{detail}</small></div>}

function CalculationProgress({stage}:{stage:number}){
  return <section className="calculation-progress" role="status" aria-live="polite" aria-label="正在计算评分结果"><div className="calculation-progress-head"><span>CALCULATION IN PROGRESS</span><strong>正在生成评分结果</strong><p>系统正按已校验的数据和模型规则完成本次测算。</p></div><ol>{CALCULATION_STAGES.map((item,index)=><li key={item.label} className={index<stage?'complete':index===stage?'active':''}><span>{index<stage?<Check size={15}/>:String(index+1).padStart(2,'0')}</span><div><strong>{item.label}</strong><small>{item.detail}</small></div></li>)}</ol><div className="calculation-progress-foot"><span>分析步骤 {Math.min(stage+1,CALCULATION_STAGES.length)} / {CALCULATION_STAGES.length}</span><span>正在处理中…</span></div></section>
}

function useModalScrollLock(){
  useEffect(()=>{
    const inDialog=(target:EventTarget|null)=>target instanceof Element&&Boolean(target.closest('.confirm-dialog, .drawer'))
    const stopBackgroundScroll=(event:WheelEvent|TouchEvent)=>{if(!inDialog(event.target))event.preventDefault()}
    const stopScrollKey=(event:KeyboardEvent)=>{
      if(![' ','ArrowUp','ArrowDown','PageUp','PageDown','Home','End'].includes(event.key))return
      const target=event.target
      if(target instanceof HTMLInputElement||target instanceof HTMLTextAreaElement||target instanceof HTMLSelectElement)return
      event.preventDefault()
    }
    window.addEventListener('wheel',stopBackgroundScroll,{passive:false})
    window.addEventListener('touchmove',stopBackgroundScroll,{passive:false})
    window.addEventListener('keydown',stopScrollKey)
    return()=>{window.removeEventListener('wheel',stopBackgroundScroll);window.removeEventListener('touchmove',stopBackgroundScroll);window.removeEventListener('keydown',stopScrollKey)}
  },[])
}

function ConfirmDialog({action,onClose}:{action:ConfirmAction;onClose:()=>void}){
  const dialogRef=useRef<HTMLDivElement>(null),cancelRef=useRef<HTMLButtonElement>(null),busyRef=useRef(false),closeRef=useRef(onClose)
  const [busy,setBusy]=useState(false),[error,setError]=useState('')
  useModalScrollLock()
  closeRef.current=onClose
  useEffect(()=>{
    const previousFocus=document.activeElement instanceof HTMLElement?document.activeElement:null
    cancelRef.current?.focus()
    function onKeyDown(event:KeyboardEvent){
      if(event.key==='Escape'&&!busyRef.current){event.preventDefault();closeRef.current()}
      if(event.key!=='Tab')return
      const buttons=Array.from(dialogRef.current?.querySelectorAll<HTMLButtonElement>('button:not(:disabled)')||[])
      if(!buttons.length)return
      const first=buttons[0],last=buttons[buttons.length-1]
      if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus()}
      else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus()}
    }
    document.addEventListener('keydown',onKeyDown)
    return()=>{document.removeEventListener('keydown',onKeyDown);previousFocus?.focus()}
  },[])
  async function confirm(){
    if(busyRef.current)return
    busyRef.current=true;setBusy(true);setError('')
    try{await action.onConfirm();onClose()}
    catch(e){setError((e as Error).message||'操作失败，请重试。')}
    finally{busyRef.current=false;setBusy(false)}
  }
  return <div className="confirm-overlay" onMouseDown={event=>{if(event.target===event.currentTarget&&!busy)onClose()}}><div className="confirm-dialog" ref={dialogRef} role="alertdialog" aria-modal="true" aria-labelledby="confirm-title" aria-describedby="confirm-warning"><div className="confirm-head"><span className="confirm-kicker">请确认操作</span><button className="confirm-close" type="button" onClick={onClose} disabled={busy} aria-label="关闭确认弹窗"><X size={18}/></button></div><h2 id="confirm-title">{action.title}</h2><p className="confirm-subject">{action.subject}</p><div className="confirm-warning" id="confirm-warning"><CircleAlert size={18}/><p>{action.warning}</p></div>{error&&<p className="confirm-error" role="alert">{error}</p>}<div className="confirm-actions"><button className="btn btn-light" type="button" ref={cancelRef} onClick={onClose} disabled={busy}>取消</button><button className="btn confirm-danger" type="button" onClick={confirm} disabled={busy}>{busy?'正在删除…':action.confirmLabel}</button></div></div></div>
}

function RemoveDimensionIcon(){
  return <svg width="20" height="20" viewBox="0 0 48 48" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true"><path d="M24 44C35.0457 44 44 35.0457 44 24C44 12.9543 35.0457 4 24 4C12.9543 4 4 12.9543 4 24C4 35.0457 12.9543 44 24 44Z" fill="#e63737" stroke="#e63737" strokeWidth="4" strokeLinejoin="round"/><path d="M16 24L32 24" stroke="#FFF" strokeWidth="4" strokeLinecap="round" strokeLinejoin="round"/></svg>
}

function ModelCenter({models,runs,calculationTypes,onModelsChange,requestConfirm,registerLeaveGuard}:{models:EvaluationModel[];runs:RunSummary[];calculationTypes:CalculationType[];onModelsChange:(models:EvaluationModel[])=>void;requestConfirm:(action:ConfirmAction)=>void;registerLeaveGuard:(guard:LeaveGuard|null)=>void}){
  const [selectedId,setSelectedId]=useState(models[0]?.id||''),[model,setModel]=useState<EvaluationModel|null>(null),[busy,setBusy]=useState(false),[message,setMessage]=useState('')
  const [managingCategories,setManagingCategories]=useState(false)
  const [activePanel,setActivePanel]=useState<'simulation'|'optimizer'|'audit'|null>(null)
  const [runId,setRunId]=useState(runs[0]?.id||''),[baselineId,setBaselineId]=useState(''),[simulation,setSimulation]=useState<Simulation|null>(null)
  const [optimizerRunId,setOptimizerRunId]=useState(runs[0]?.id||''),[objective,setObjective]=useState(''),[maxWeightChange,setMaxWeightChange]=useState(5),[maxOtherRankChange,setMaxOtherRankChange]=useState(3)
  const [optimization,setOptimization]=useState<WeightOptimizationResponse|null>(null),[selectedCandidateId,setSelectedCandidateId]=useState('')
  const [optimizerStep,setOptimizerStep]=useState<1|2>(1)
  const [parsingKeys,setParsingKeys]=useState<Record<string,boolean>>({}),[parserErrors,setParserErrors]=useState<Record<string,string>>({})
  const [editingIndicatorKey,setEditingIndicatorKey]=useState<string|null>(null)
  const [pendingRuleKeys,setPendingRuleKeys]=useState<Record<string,boolean>>({})
  const ruleRequests=useRef<Record<string,number>>({})
  const activeModelId=useRef(selectedId);activeModelId.current=selectedId
  const [readiness,setReadiness]=useState<ModelReadiness|null>(null)
  const [validatedModel,setValidatedModel]=useState<{id:string;definition:ModelDefinition}|null>(null)
  const [readinessError,setReadinessError]=useState('')
  const [savedDraft,setSavedDraft]=useState<{id:string;signature:string}|null>(null)
  const [pendingLeave,setPendingLeave]=useState<(()=>void)|null>(null)
  const [saveError,setSaveError]=useState('')
  const dirty=Boolean(model?.status==='draft'&&savedDraft?.id===model.id&&savedDraft.signature!==modelDraftSignature(model))
  const canSaveDraft=!Object.values(parsingKeys).some(Boolean)&&!Object.values(pendingRuleKeys).some(Boolean)
  const applyLoadedModel=(value:EvaluationModel)=>{setModel(value);setSavedDraft({id:value.id,signature:modelDraftSignature(value)});setSaveError('')}
  function protectLeave(proceed:()=>void){if(busy)return;if(dirty){setSaveError('');setPendingLeave(()=>proceed)}else proceed()}
  useEffect(()=>{registerLeaveGuard(protectLeave);return()=>registerLeaveGuard(null)})
  useEffect(()=>{
    if(!dirty)return
    const warn=(event:BeforeUnloadEvent)=>{event.preventDefault();event.returnValue=''}
    window.addEventListener('beforeunload',warn)
    return()=>window.removeEventListener('beforeunload',warn)
  },[dirty])
  useEffect(()=>{
    if(!model||editingIndicatorKey)return
    setReadinessError('')
    let current=true
    const timer=window.setTimeout(()=>api<ModelReadiness>('/models/validate','POST',{definition:model.definition}).then(value=>{if(current){setReadiness(value);setValidatedModel({id:model.id,definition:model.definition})}}).catch(e=>{if(current)setReadinessError((e as Error).message)}),250)
    return()=>{current=false;window.clearTimeout(timer)}
  },[model?.id,model?.definition,editingIndicatorKey])
  useEffect(()=>{if(!selectedId&&models[0])setSelectedId(models[0].id)},[models,selectedId])
  useEffect(()=>{
    if(!selectedId)return
    let current=true
    setBusy(true);setModel(null);setSavedDraft(null)
    api<EvaluationModel>('/models/'+selectedId).then(value=>{if(current){applyLoadedModel(value);setSimulation(null);setOptimization(null);setSelectedCandidateId('');setOptimizerStep(1);setParserErrors({})}}).catch(e=>{if(current)setMessage((e as Error).message)}).finally(()=>{if(current)setBusy(false)})
    return()=>{current=false}
  },[selectedId])
  useEffect(()=>{ruleRequests.current={};setParsingKeys({});setParserErrors({});setPendingRuleKeys({});setEditingIndicatorKey(null)},[model?.id])
  useEffect(()=>()=>{ruleRequests.current={}},[])
  useEffect(()=>{if(!message)return;const timer=window.setTimeout(()=>setMessage(''),2400);return()=>window.clearTimeout(timer)},[message])
  useEffect(()=>{if(model?.id===baselineId)setBaselineId('')},[model?.id,baselineId])
  const refresh=async(preferred?:string)=>{
    const next=await api<EvaluationModel[]>('/models');onModelsChange(next)
    const nextId=preferred&&next.some(item=>item.id===preferred)?preferred:next[0]?.id||''
    if(nextId!==selectedId){setModel(null);setSavedDraft(null);setSelectedId(nextId)}
    else if(nextId)applyLoadedModel(await api<EvaluationModel>('/models/'+nextId))
    else setModel(null)
    setSimulation(null);setOptimization(null);setSelectedCandidateId('');setOptimizerStep(1);setParserErrors({})
  }
  const editDefinition=(updater:(definition:ModelDefinition)=>ModelDefinition)=>{if(model?.status==='draft'&&!busy)setModel({...model,definition:updater(structuredClone(model.definition))})}
  const editDimension=(index:number,patch:Partial<ModelDimension>)=>editDefinition(definition=>({...definition,dimensions:definition.dimensions.map((item,i)=>i===index?{...item,...patch}:item)}))
  const weight=model?.definition.dimensions.reduce((sum,item)=>sum+(item.enabled?Number(item.weight||0):0),0)||0
  const rulesReady=validatedModel?.definition===model?.definition&&!!readiness&&!readiness.errors.length&&!Object.values(pendingRuleKeys).some(Boolean)
  const statusText={published:'已发布',draft:'草稿',archived:'已归档'}
  const modelReferenced=!!model&&runs.some(item=>item.model_id===model.id)
  const publishedCount=models.filter(item=>item.status==='published').length
  const isLastPublished=!!model&&model.status==='published'&&publishedCount<=1
  const canDelete=!!model&&!modelReferenced&&!isLastPublished
  const selectedCandidate=optimization?.optimization.candidates.find(item=>item.id===selectedCandidateId)||optimization?.optimization.candidates[0]||null
  async function clone(){if(!model)return;setBusy(true);setMessage('');try{const source=await api<EvaluationModel>('/models/'+model.id);const created=await api<EvaluationModel>('/models','POST',{name:`${source.name}（副本）`,version:`${source.version}-DRAFT`,clone_from:source.id});await refresh(created.id)}catch(e){setMessage((e as Error).message)}finally{setBusy(false)}}
  async function save():Promise<boolean>{
    if(!model||model.status!=='draft'||busy)return false
    if(!canSaveDraft){setSaveError('规则正在检查，请完成后再保存。');return false}
    setBusy(true);setMessage('');setSaveError('')
    try{
      const value=await api<EvaluationModel>(`/models/${model.id}`,'PUT',{name:model.name,version:model.version,definition:model.definition})
      applyLoadedModel(value);onModelsChange(models.map(item=>item.id===value.id?value:item));setMessage('草稿已保存，尚未发布')
      return true
    }catch(e){setSaveError((e as Error).message||'保存失败，请重试。');return false}
    finally{setBusy(false)}
  }
  async function saveAndLeave(){const proceed=pendingLeave;if(!proceed)return;if(await save()){setPendingLeave(null);proceed()}}
  async function openAudit(){
    if(!model)return
    setActivePanel('audit')
    try{const value=await api<EvaluationModel>('/models/'+model.id);setModel(current=>current?.id===value.id?{...current,events:value.events}:current)}
    catch(e){setMessage((e as Error).message)}
  }
  async function publish(){if(!model)return;setBusy(true);setMessage('');try{await api(`/models/${model.id}/publish`,'POST',{name:model.name,version:model.version,definition:model.definition});await refresh(model.id);setMessage('当前修改已保存并发布；其他已发布模型仍可继续使用')}catch(e){setMessage((e as Error).message)}finally{setBusy(false)}}
  function deleteModel(){if(!model||!canDelete)return;const target=model;requestConfirm({title:target.status==='published'?'删除已发布模型':'删除模型草稿',subject:`${target.version} · ${target.name}`,warning:target.status==='published'?'该模型尚未被任何评估批次引用。删除后，模型配置和全部变更记录将永久移除且无法恢复。':'模型配置和变更记录都会永久删除，且无法恢复。',confirmLabel:'确认删除模型',onConfirm:async()=>{setBusy(true);setMessage('');try{await api(`/models/${target.id}`,'DELETE');await refresh();setMessage('模型已删除')}finally{setBusy(false)}}})}
  async function simulate(){if(!model||!runId)return;setBusy(true);setMessage('');try{const value=await api<{simulation:Simulation}>(`/models/${model.id}/simulate`,'POST',{run_id:runId,baseline_model_id:baselineId||undefined});setSimulation(value.simulation)}catch(e){setMessage((e as Error).message)}finally{setBusy(false)}}
  async function optimize(){
    if(!model||!optimizerRunId||!objective.trim())return
    setBusy(true);setMessage('');setOptimization(null);setSelectedCandidateId('')
    try{
      const value=await api<WeightOptimizationResponse>(`/models/${model.id}/optimize-weights`,'POST',{run_id:optimizerRunId,objective:objective.trim(),max_weight_change:maxWeightChange,max_other_rank_change:maxOtherRankChange})
      setOptimization(value);setSelectedCandidateId(value.optimization.candidates[0]?.id||'');setOptimizerStep(2)
    }catch(e){setMessage((e as Error).message)}finally{setBusy(false)}
  }
  async function saveOptimizationCandidate(candidate:WeightCandidate){
    if(!model||!candidate.achieved)return
    setBusy(true);setMessage('')
    try{
      const suffix=Date.now().toString().slice(-6)
      const name=`${model.name}（智能调优）`.slice(0,100)
      const version=`${model.version}-AI-${suffix}`.slice(0,40)
      const created=await api<EvaluationModel>('/models','POST',{name,version,clone_from:model.id})
      const definition=structuredClone(model.definition)
      definition.dimensions=definition.dimensions.map(item=>candidate.weights[item.key]===undefined?item:{...item,weight:candidate.weights[item.key]})
      await api(`/models/${created.id}`,'PUT',{name,version,definition})
      setActivePanel(null)
      await refresh(created.id)
      setMessage(`已按“${candidate.label}”创建新草稿，尚未发布`)
    }catch(e){setMessage((e as Error).message)}finally{setBusy(false)}
  }
  async function parseDimensionRule(key:string,label:string,summary:string){
    if(!summary.trim()||!model)return
    const modelId=model.id,dimension=model.definition.dimensions.find(item=>item.key===key)
    const request=(ruleRequests.current[key]||0)+1;ruleRequests.current[key]=request
    const isCurrent=()=>activeModelId.current===modelId&&ruleRequests.current[key]===request
    const previousType=dimension?.calculation_type||(dimension?.metric_keys.length===1?dimension.metric_keys[0]:undefined)
    const calculationType=calculationTypes.some(item=>item.id===previousType)?previousType:undefined
    setParsingKeys(current=>({...current,[key]:true}));setParserErrors(current=>({...current,[key]:''}))
    try{
      const value=await api<RuleParseResult>('/rules/parse','POST',{dimension_label:label,summary,calculation_type:calculationType,ranking:model.definition.ranking})
      if(!isCurrent())return
      setModel(current=>{
        if(!current||current.id!==modelId)return current
        const dimensions=current.definition.dimensions.map(dimension=>dimension.key===key&&dimension.rule_summary===summary&&dimension.label===label?{...dimension,calculation_type:value.calculation_type,options:{...(dimension.options||{}),parsed_rule:value.rule,rule_parser:{provider:value.provider,model:value.model,parsed_at:value.parsed_at,source_summary:summary,execution_contract:value.execution_contract}}}:dimension)
        return {...current,definition:{...current.definition,dimensions}}
      })
    }catch(e){if(isCurrent())setParserErrors(current=>({...current,[key]:(e as Error).message}))}
    finally{if(isCurrent()){setParsingKeys(current=>({...current,[key]:false}));setPendingRuleKeys(current=>({...current,[key]:false}))}}
  }
  function beginIndicatorEdit(key:string){ruleRequests.current[key]=(ruleRequests.current[key]||0)+1;setEditingIndicatorKey(key);setParsingKeys(current=>({...current,[key]:false}))}
  function finishIndicatorEdit(index:number){setEditingIndicatorKey(null);if(!model)return;const dimension=model.definition.dimensions[index];if(!pendingRuleKeys[dimension.key]&&!parserErrors[dimension.key])return;if(dimension.rule_summary.trim())void parseDimensionRule(dimension.key,dimension.label,dimension.rule_summary);else{setPendingRuleKeys(current=>({...current,[dimension.key]:false}));setParserErrors(current=>({...current,[dimension.key]:''}))}}
  function updateRuleSummary(index:number,summary:string){if(!model)return;const dimension=model.definition.dimensions[index];const options={...(dimension.options||{})};delete options.parsed_rule;delete options.rule_parser;editDimension(index,{rule_summary:summary,options});setPendingRuleKeys(current=>({...current,[dimension.key]:true}))}
  function updateIndicatorLabel(index:number,label:string){if(!model)return;const dimension=model.definition.dimensions[index];editDimension(index,{label});if(!dimension.calculation_type&&!calculationTypes.some(item=>item.id===dimension.metric_keys[0])&&dimension.rule_summary.trim())setPendingRuleKeys(current=>({...current,[dimension.key]:true}))}
  function addIndicator(){if(!model)return;const index=model.definition.dimensions.length+1,key=`custom_${Date.now()}_${index}`,categoryKey=model.definition.categories[0]?.key||'';editDefinition(definition=>({...definition,dimensions:[...definition.dimensions,{key,category_key:categoryKey,label:'新指标',enabled:true,weight:0,aggregation:'average_scaled',metric_keys:[key],rule_summary:''}]}))}
  function deleteIndicator(index:number){if(!model||model.status!=='draft')return;if(model.definition.dimensions.length<=1){setMessage('模型至少需要保留一个指标。');return}const target=model.definition.dimensions[index];requestConfirm({title:'删除评分指标',subject:target.label,warning:'该指标将从当前草稿中移除。保存草稿后生效；维度和历史批次不会受影响。',confirmLabel:'确认删除指标',onConfirm:async()=>{editDefinition(definition=>({...definition,dimensions:definition.dimensions.filter((_,i)=>i!==index)}));setMessage(`已移除指标“${target.label}”，请保存草稿。`)}})}
  return <div className="model-layout"><aside className="panel model-list"><div className="panel-head"><div><h2>模型版本</h2><p>复制已发布版本后再调整。</p></div><button className="icon-btn" onClick={()=>protectLeave(()=>void clone())} disabled={!model||busy} title="复制为草稿"><Copy size={17}/></button></div>{models.map(item=><button className={'model-row '+(selectedId===item.id?'active':'')} key={item.id} disabled={busy} onClick={()=>{if(item.id!==selectedId)protectLeave(()=>setSelectedId(item.id))}}><span><strong>{item.name}</strong><small>{item.version}</small></span><em className={'status-badge '+item.status}>{statusText[item.status]}</em></button>)}</aside>
  <div className="model-workspace">{model&&<><section className="panel model-editor">
    <div className="panel-head"><div><h2>规则配置</h2><p>指标按顺序独立配置，并通过“归属维度”建立分类关系。</p></div><div className="model-head-actions">{model.status==='draft'&&<span className={'draft-save-state '+(dirty?'unsaved':'saved')} role="status">{busy?'处理中…':dirty?'有未保存修改':'已保存'}</span>}<span className={'status-badge '+model.status}>{statusText[model.status]}</span><button className="delete-icon" onClick={deleteModel} disabled={busy||!canDelete} title={modelReferenced?'该模型已被评估批次引用':isLastPublished?'至少需要保留一个已发布模型':'删除模型'} aria-label="删除模型"><Trash2 size={16}/></button></div></div>
    <div className="model-meta-grid"><label>模型名称<input value={model.name} disabled={model.status!=='draft'||busy} onChange={e=>setModel({...model,name:e.target.value})}/></label><label>版本号<input value={model.version} disabled={model.status!=='draft'||busy} onChange={e=>setModel({...model,version:e.target.value})}/></label><label>首名分<input type="number" step="0.5" value={displayNumber(model.definition.ranking.first_score)} disabled={model.status!=='draft'||busy} onChange={e=>editDefinition(d=>({...d,ranking:{...d.ranking,first_score:numberValue(e.target.value)}}))}/></label><label>每名递减<input type="number" step="0.5" value={displayNumber(model.definition.ranking.decrement)} disabled={model.status!=='draft'||busy} onChange={e=>editDefinition(d=>({...d,ranking:{...d.ranking,decrement:numberValue(e.target.value)}}))}/></label></div>
    <div className={'model-summary '+(weight===100?'weight-ok':'weight-error')}><strong>指标权重 {fmt(weight,0)} / 100</strong><span>{model.definition.categories.length} 个可维护维度 · 每项指标必须选择归属维度</span></div>
    <div className="dimension-list">{model.definition.dimensions.map((dimension,index)=>{const parsedRule=dimension.options?.parsed_rule,isParsing=Boolean(parsingKeys[dimension.key]),isEditing=editingIndicatorKey===dimension.key,execution=validatedModel?.id===model.id?readiness?.indicators.find(item=>item.key===dimension.key):undefined,typeId=execution?.calculation_type??dimension.calculation_type??(dimension.metric_keys.length===1?dimension.metric_keys[0]:''),type=calculationTypes.find(item=>item.id===typeId);return <div className={'dimension-row'+(model.status!=='draft'?' read-only':'')} key={dimension.key}>
      <div className="dimension-index-control">{model.status==='draft'&&<button className="dimension-remove-icon" type="button" onClick={()=>deleteIndicator(index)} disabled={model.definition.dimensions.length<=1} aria-label={`删除指标 ${dimension.label}`} title="删除指标"><RemoveDimensionIcon/></button>}<span>{String(index+1).padStart(2,'0')}</span></div>
      <div className="dimension-main">
        <div className="dimension-fields"><label>指标名称<input value={dimension.label} disabled={model.status!=='draft'||busy} onFocus={()=>beginIndicatorEdit(dimension.key)} onChange={e=>updateIndicatorLabel(index,e.target.value)} onBlur={()=>finishIndicatorEdit(index)}/></label><label>归属维度<SystemSelect label={`${dimension.label}归属维度`} value={dimension.category_key} disabled={model.status!=='draft'||busy} onChange={e=>{if(e.target.value==='__manage_categories__')setManagingCategories(true);else editDimension(index,{category_key:e.target.value})}}>{model.definition.categories.map(item=><option key={item.key} value={item.key}>{item.label}</option>)}{model.status==='draft'&&<option value="__manage_categories__">修改维度…</option>}</SystemSelect></label><label>权重<input type="number" min="0" max="100" value={displayNumber(dimension.weight)} disabled={model.status!=='draft'||busy} onChange={e=>editDimension(index,{weight:numberValue(e.target.value)})}/></label></div>
        <div className="rule-summary-block"><label className="rule-summary-field">规则摘要<textarea aria-label={`${dimension.label}规则摘要`} placeholder="填写业务规则，离开输入框后自动检查。" value={dimension.rule_summary} disabled={model.status!=='draft'||busy} onFocus={()=>beginIndicatorEdit(dimension.key)} onChange={e=>updateRuleSummary(index,e.target.value)} onBlur={()=>finishIndicatorEdit(index)}/></label>
          {model.status==='draft'&&isParsing&&<div className="rule-auto-state checking"><span/>正在检查规则…</div>}
          {model.status==='draft'&&!isEditing&&!isParsing&&!parsedRule&&!execution?.executable&&!parserErrors[dimension.key]&&dimension.rule_summary.trim()&&<div className="rule-auto-state pending"><span/>离开输入框后自动检查</div>}
          {parserErrors[dimension.key]&&!execution?.executable&&<p className="rule-parser-error"><CircleAlert size={15}/>{parserErrors[dimension.key]}</p>}

          {!isParsing&&execution&&!parserErrors[dimension.key]&&<div className={'parsed-rule-summary '+(!execution.executable?'needs-review':'')}><Check size={15}/><span><em>{isEditing&&pendingRuleKeys[dimension.key]?'编辑中':execution.executable?'规则已识别':'有业务口径待确认'}</em><strong>{execution.executable?(execution.normalized_summary||dimension.rule_summary):execution.errors.join('；')}</strong>{execution.executable&&type&&<small>{type.inputs.length?`计算需要：${type.inputs.map(item=>item.replace(/^202[45]年/,item.startsWith('2024')?'上一年度':'评估年度')).join('、')}`:'按评估年度固定生成模拟值；结果会明确标记为试算。'}</small>}</span></div>}
        </div>
      </div>
    </div>})}</div>{model.status==='draft'&&<button className="btn btn-light add-dimension" onClick={addIndicator} disabled={busy}><Plus size={16}/> 添加指标</button>}
    {model.status==='draft'&&<>{!rulesReady&&<p className="publish-readiness"><CircleAlert size={15}/>{Object.values(pendingRuleKeys).some(Boolean)&&editingIndicatorKey?'离开输入框后自动检查修改后的规则。':readinessError||readiness?.errors.join('；')||'正在检查模型的可执行范围…'}</p>}<div className="model-toolbar"><div className="model-submit-actions"><button className="btn btn-light" onClick={save} disabled={busy||Object.values(parsingKeys).some(Boolean)||Object.values(pendingRuleKeys).some(Boolean)}><Save size={16}/> 保存草稿</button><button className="btn btn-dark" onClick={publish} disabled={busy||weight!==100||Object.values(parsingKeys).some(Boolean)||!rulesReady}><Rocket size={16}/> 发布并锁定</button></div></div></>}
    {saveError&&!pendingLeave&&<p className="confirm-error" role="alert">保存失败：{saveError}。修改仍保留在当前草稿中。</p>}
  </section>
  <div className="model-entry-grid">
    <button type="button" className="model-entry-card" onClick={()=>setActivePanel('simulation')}><span><strong>影响模拟</strong><small>在历史批次数据上预演新模型，并与基准版本比较名次变化。</small></span><ArrowRight size={18}/></button>
    <button type="button" className="model-entry-card" onClick={()=>{setOptimizerStep(1);setActivePanel('optimizer')}}><span><strong>智能调优</strong><small>输入排名目标，由 AI 理解意图并用实际评分数据搜索权重方案。</small></span><Sparkles size={18}/></button>
    <button type="button" className="model-entry-card" onClick={()=>void openAudit()}><span><strong>变更记录</strong><small>系统自动概括每次模型变更并记录时间。</small></span><ArrowRight size={18}/></button>
  </div>
  {message&&<div className="model-message" role="status"><span>{message}</span><button type="button" onClick={()=>setMessage('')} aria-label="关闭提示"><X size={16}/></button></div>}
  {managingCategories&&<DimensionManager categories={model.definition.categories} indicators={model.definition.dimensions} onClose={()=>setManagingCategories(false)} onChange={(categories,dimensions)=>editDefinition(definition=>({...definition,categories,dimensions}))}/>}
  {activePanel==='simulation'&&<ModelCenterDialog title="影响模拟" description="在历史批次数据上预演新模型，并与基准版本比较名次变化。" onClose={()=>setActivePanel(null)}><div className="simulation-controls"><label>测试批次<SystemSelect label="测试批次" value={runId} onChange={e=>setRunId(e.target.value)}><option value="">选择批次</option>{runs.map(item=><option key={item.id} value={item.id}>{item.year} · {item.name}</option>)}</SystemSelect></label><label>对比基准<SystemSelect label="对比基准" value={baselineId} onChange={e=>setBaselineId(e.target.value)}><option value="">不对比</option>{models.filter(item=>item.id!==model.id).map(item=><option key={item.id} value={item.id}>{item.version} · {statusText[item.status]}</option>)}</SystemSelect></label><button className="btn btn-dark" onClick={simulate} disabled={busy||!runId}><GitCompare size={16}/> 运行模拟</button></div>{simulation&&<><div className="simulation-summary"><span>{simulation.ready?'数据完整，可比较正式排名':'当前为部分数据预演；缺失项不会按零分处理'}</span><strong>{simulation.complete_products}/13 条完整</strong></div><div className="table-scroll"><table className="simulation-table"><thead><tr><th>产品线</th><th>当前得分</th><th>当前排名</th><th>基准得分</th><th>名次变化</th><th>缺失指标</th></tr></thead><tbody>{simulation.rows.map(row=><tr key={row.product}><td><strong>{row.product}</strong></td><td>{row.total===null?`${fmt(row.partial_total,1)}（部分）`:fmt(row.total,1)}</td><td>{row.rank??'—'}</td><td>{row.baseline_total===null?'—':fmt(row.baseline_total,1)}</td><td>{row.rank_delta===null?'—':row.rank_delta>0?`↑ ${row.rank_delta}`:row.rank_delta<0?`↓ ${Math.abs(row.rank_delta)}`:'—'}</td><td>{row.missing_metrics.join("、")||"—"}</td></tr>)}</tbody></table></div></>}</ModelCenterDialog>}
  {activePanel==='optimizer'&&<ModelCenterDialog title="智能调优" description="输入调优目标，AI 负责分析理解，结合本地算法计算和验证排名影响。" onClose={()=>setActivePanel(null)} headerAside={<div className="optimizer-steps" aria-label="智能调优步骤">
      <div className={optimizerStep===1?'active':'complete'}><span>01</span><strong>输入目标</strong></div>
      <i aria-hidden="true"/>
      <div className={optimizerStep===2?'active':''}><span>02</span><strong>选择方案</strong></div>
    </div>} footer={optimizerStep===2&&selectedCandidate?<div className="optimizer-dialog-actions"><button className="btn btn-light" type="button" onClick={()=>setOptimizerStep(1)}><ArrowLeft size={16}/> 返回上一步</button><button className="btn btn-dark" onClick={()=>saveOptimizationCandidate(selectedCandidate)} disabled={busy||!selectedCandidate.achieved}><Save size={16}/> 保存为新草稿</button></div>:undefined}>
    {(optimizerStep===1||!optimization)&&<div className="optimizer-step-panel">
      <div className="optimizer-form">
        <label>测试批次<SystemSelect label="测试批次" value={optimizerRunId} onChange={e=>setOptimizerRunId(e.target.value)}><option value="">选择已完成评分的批次</option>{runs.map(item=><option key={item.id} value={item.id}>{item.year} · {item.name}</option>)}</SystemSelect></label>
        <label className="optimizer-objective">目标<textarea value={objective} onChange={e=>setObjective(e.target.value)} placeholder="请输入想要实现的结果"/></label>
        <label>单项权重最大调整<input type="number" min="1" max="20" step="1" value={displayNumber(maxWeightChange)} onChange={e=>setMaxWeightChange(numberValue(e.target.value))}/></label>
        <label>其他产品最大名次波动<input type="number" min="0" max="12" step="1" value={displayNumber(maxOtherRankChange)} onChange={e=>setMaxOtherRankChange(numberValue(e.target.value))}/></label>
      </div>
      <div className="optimizer-input-footer"><div className="optimizer-note"><ShieldCheck size={17}/><span><strong>统一规则，不定向改分</strong> 所有产品使用同一套新权重重新计算；不会修改当前模型，也不会自动发布。</span></div><button className="btn btn-dark" onClick={optimize} disabled={busy||!optimizerRunId||!objective.trim()}><Sparkles size={16}/>{busy?'正在分析…':'分析调优方案'}</button></div>
    </div>}
    {optimizerStep===2&&optimization&&<div className="optimizer-results-step">
      <div className={'optimizer-goal-summary '+(optimization.optimization.feasible?'feasible':'limited')}><span><em>AI 已识别</em><strong>{optimization.interpretation.goal.normalized_goal}</strong><small>当前第 {optimization.optimization.goal.baseline_rank} 名 → 目标第 {optimization.optimization.goal.requested_rank} 名 <span className="optimizer-goal-message">· {optimization.optimization.message}</span></small></span></div>
      <div className="optimizer-candidates">{optimization.optimization.candidates.map(candidate=><button type="button" key={candidate.id} className={'optimizer-candidate '+(selectedCandidate?.id===candidate.id?'selected':'')} onClick={()=>setSelectedCandidateId(candidate.id)}><span><strong>{candidate.label}</strong><small>{candidate.changed_dimensions} 项权重变化 · 其他产品最大波动 {candidate.max_other_rank_change} 位</small></span><em className={candidate.achieved?'achieved':'attempt'}>{candidate.achieved?'达到目标':'接近目标'}</em></button>)}</div>
      {selectedCandidate&&<div className="optimizer-result">
        <div className="optimizer-result-head"><div><h3>{selectedCandidate.label}</h3><p>{selectedCandidate.explanation}</p></div></div>
        <div className="weight-change-list">{selectedCandidate.weight_changes.map(item=><div key={item.key}><span>{item.label}</span><strong>{fmt(item.from,0)} → {fmt(item.to,0)}</strong><em className={item.delta>0?'up':'down'}>{item.delta>0?'+':''}{fmt(item.delta,0)}</em></div>)}</div>
        <div className="table-scroll"><table className="simulation-table optimizer-ranking-table"><thead><tr><th>产品线</th><th>原总分</th><th>原排名</th><th>新总分</th><th>新排名</th><th>名次变化</th></tr></thead><tbody>{selectedCandidate.rows.map(row=><tr key={row.product} className={row.product===optimization.optimization.goal.target_product?'target-product':''}><td><strong>{row.product}</strong></td><td>{fmt(row.baseline_total,1)}</td><td>{row.baseline_rank}</td><td>{fmt(row.total,1)}</td><td>{row.rank}</td><td>{row.position_change>0?`后移 ${row.position_change} 位`:row.position_change<0?`前移 ${Math.abs(row.position_change)} 位`:'—'}</td></tr>)}</tbody></table></div>
      </div>}
    </div>}
  </ModelCenterDialog>}
  {activePanel==='audit'&&<ModelCenterDialog title="变更记录" description="系统自动概括每次模型变更并记录时间。" onClose={()=>setActivePanel(null)}>{model.events?.length?<div className="audit-list">{model.events.map(event=><div key={event.id}><strong>{event.detail.summary||event.detail.reason||'—'}</strong><small>{event.created_at.slice(0,19).replace('T',' ')}</small></div>)}</div>:<div className="empty">暂无记录</div>}</ModelCenterDialog>}
  </>}</div>{pendingLeave&&<DraftLeaveDialog busy={busy} canSave={canSaveDraft} error={saveError} onCancel={()=>{setPendingLeave(null);setSaveError('')}} onDiscard={()=>{if(!busy){const proceed=pendingLeave;setPendingLeave(null);proceed()}}} onSave={saveAndLeave}/>}</div>
}

function DraftLeaveDialog({busy,canSave,error,onCancel,onDiscard,onSave}:{busy:boolean;canSave:boolean;error:string;onCancel:()=>void;onDiscard:()=>void;onSave:()=>Promise<void>}){
  const ref=useRef<HTMLDivElement>(null),latest=useRef({busy,onCancel});latest.current={busy,onCancel}
  useModalScrollLock()
  useEffect(()=>{
    const previous=document.activeElement instanceof HTMLElement?document.activeElement:null
    ref.current?.querySelector<HTMLButtonElement>('button')?.focus()
    function keydown(event:KeyboardEvent){
      if(event.key==='Escape'){event.preventDefault();if(!latest.current.busy)latest.current.onCancel()}
      if(event.key==='Tab'){
        const buttons=Array.from(ref.current?.querySelectorAll<HTMLButtonElement>('button:not(:disabled)')||[])
        if(!buttons.length){event.preventDefault();return}
        if(event.shiftKey&&document.activeElement===buttons[0]){event.preventDefault();buttons.at(-1)?.focus()}
        else if(!event.shiftKey&&document.activeElement===buttons.at(-1)){event.preventDefault();buttons[0].focus()}
      }
    }
    document.addEventListener('keydown',keydown)
    return()=>{document.removeEventListener('keydown',keydown);if(previous?.isConnected)previous.focus()}
  },[])
  return <div className="confirm-overlay"><div className="confirm-dialog draft-leave-dialog" ref={ref} role="alertdialog" aria-modal="true" aria-labelledby="draft-leave-title"><h2 id="draft-leave-title">草稿尚未保存</h2><p className="confirm-subject">离开前是否保存当前修改？</p>{!canSave&&<p>规则正在检查，完成后可保存并离开。</p>}{error&&<p className="confirm-error" role="alert">{error}</p>}<div className="draft-leave-actions"><button className="btn btn-light" onClick={onCancel} disabled={busy}>取消离开</button><button className="btn btn-light" onClick={onDiscard} disabled={busy}>放弃修改并离开</button><button className="btn btn-dark" onClick={()=>void onSave()} disabled={busy||!canSave}>{busy?'正在保存…':'保存后离开'}</button></div></div></div>
}

function ModelCenterDialog({title,description,onClose,headerAside,footer,children}:{title:string;description:string;onClose:()=>void;headerAside?:React.ReactNode;footer?:React.ReactNode;children:React.ReactNode}){
  useModalScrollLock()
  const dialogClass=title==='变更记录'?'model-audit-dialog':title==='智能调优'?'model-optimizer-dialog':''
  return <div className="confirm-overlay model-panel-overlay" onMouseDown={event=>{if(event.target===event.currentTarget)onClose()}}><section className={'confirm-dialog model-panel-dialog '+dialogClass} role="dialog" aria-modal="true" aria-labelledby="model-panel-title"><div className="confirm-head"><span className="confirm-kicker">模型中心</span><button className="confirm-close" type="button" onClick={onClose} aria-label={`关闭${title}`}><X size={18}/></button></div><div className="model-panel-title-row"><div><h2 id="model-panel-title">{title}</h2><p className="confirm-subject">{description}</p></div>{headerAside}</div><div className="model-panel-content">{children}</div>{footer&&<footer className="model-panel-footer">{footer}</footer>}</section></div>
}

function DimensionManager({categories,indicators,onClose,onChange}:{categories:ModelCategory[];indicators:ModelDimension[];onClose:()=>void;onChange:(categories:ModelCategory[],indicators:ModelDimension[])=>void}){
  const [draftCategories,setDraftCategories]=useState(()=>structuredClone(categories))
  const [draftIndicators,setDraftIndicators]=useState(()=>structuredClone(indicators))
  const [removingKey,setRemovingKey]=useState<string|null>(null)
  const [migrationTarget,setMigrationTarget]=useState('')
  const [managerMessage,setManagerMessage]=useState('')
  useModalScrollLock()
  const normalizedLabels=draftCategories.map(item=>item.label.trim())
  const canSave=draftCategories.length>0&&normalizedLabels.every(Boolean)&&new Set(normalizedLabels).size===normalizedLabels.length&&draftIndicators.length>0
  const removingCategory=draftCategories.find(item=>item.key===removingKey)
  const removingIndicators=removingCategory?draftIndicators.filter(item=>item.category_key===removingCategory.key):[]
  const migrationOptions=draftCategories.filter(item=>item.key!==removingKey)
  function rename(index:number,label:string){setDraftCategories(current=>current.map((item,i)=>i===index?{...item,label}:item))}
  function add(){setDraftCategories(current=>[...current,{key:`dimension_${Date.now()}`,label:'新维度'}])}
  function remove(index:number){
    const category=draftCategories[index]
    if(draftCategories.length<=1){setManagerMessage('模型至少需要保留一个维度。');return}
    const used=draftIndicators.some(item=>item.category_key===category.key)
    if(!used){setDraftCategories(current=>current.filter((_,i)=>i!==index));setManagerMessage(`已移除维度“${category.label}”，请保存。`);return}
    const target=draftCategories.find(item=>item.key!==category.key)
    setRemovingKey(category.key);setMigrationTarget(target?.key||'');setManagerMessage('')
  }
  function finishRemoval(mode:'migrate'|'delete'){
    if(!removingCategory)return
    if(mode==='migrate'){
      if(!migrationTarget)return
      setDraftIndicators(current=>current.map(item=>item.category_key===removingCategory.key?{...item,category_key:migrationTarget}:item))
    }else{
      if(removingIndicators.length===draftIndicators.length){setManagerMessage('模型至少需要保留一个指标，请先迁移指标或在外层新增指标。');return}
      const removedKeys=new Set(removingIndicators.map(item=>item.key))
      setDraftIndicators(current=>current.filter(item=>!removedKeys.has(item.key)))
    }
    setDraftCategories(current=>current.filter(item=>item.key!==removingCategory.key))
    setManagerMessage(mode==='migrate'?`已迁移 ${removingIndicators.length} 项指标并移除维度“${removingCategory.label}”，请保存。`:`已移除维度“${removingCategory.label}”及其 ${removingIndicators.length} 项指标，请保存。`)
    setRemovingKey(null);setMigrationTarget('')
  }
  function save(){if(!canSave)return;onChange(draftCategories.map(item=>({...item,label:item.label.trim()})),draftIndicators);onClose()}
  return <div className="confirm-overlay" onMouseDown={event=>{if(event.target===event.currentTarget)onClose()}}><section className="confirm-dialog dimension-manager" role="dialog" aria-modal="true" aria-labelledby="dimension-manager-title"><div className="confirm-head"><span className="confirm-kicker">模型维度</span><button className="confirm-close" type="button" onClick={onClose} aria-label="关闭维度管理"><X size={18}/></button></div><h2 id="dimension-manager-title">修改维度</h2><p className="confirm-subject">这里只修改当前草稿。已发布版本和历史评估结果不会改变。</p><div className="dimension-manager-list">{draftCategories.map((category,index)=>{const categoryIndicators=draftIndicators.filter(item=>item.enabled&&item.category_key===category.key);return <div className="dimension-manager-row" key={category.key}><input aria-label={`维度 ${index+1}`} value={category.label} onChange={e=>rename(index,e.target.value)}/><button type="button" className="dimension-manager-remove" onClick={()=>remove(index)} aria-label={`删除维度 ${category.label}`} title={draftCategories.length<=1?'至少保留一个维度':categoryIndicators.length?`处理 ${categoryIndicators.length} 项所属指标后删除`:'删除维度'}><RemoveDimensionIcon/></button></div>})}</div>{removingCategory&&<div className="dimension-removal-panel"><div><strong>删除“{removingCategory.label}”维度</strong><p>当前草稿中有 {removingIndicators.length} 项指标归属于该维度：{removingIndicators.map(item=>item.label).join('、')}。</p></div><label>迁移至其他维度<SystemSelect label="指标迁移目标维度" value={migrationTarget} onChange={e=>setMigrationTarget(e.target.value)}>{migrationOptions.map(item=><option key={item.key} value={item.key}>{item.label}</option>)}</SystemSelect></label><div className="dimension-removal-actions"><button className="btn btn-dark" type="button" onClick={()=>finishRemoval('migrate')} disabled={!migrationTarget}>迁移指标并删除维度</button><button className="btn btn-light dimension-delete-with-indicators" type="button" onClick={()=>finishRemoval('delete')} disabled={removingIndicators.length===draftIndicators.length}>同时删除维度及 {removingIndicators.length} 项指标</button><button className="btn btn-light" type="button" onClick={()=>{setRemovingKey(null);setMigrationTarget('')}}>取消</button></div><small>所有操作仅作用于当前草稿，保存后生效。</small></div>}{managerMessage&&<p className="dimension-manager-message">{managerMessage}</p>}{normalizedLabels.some((label,index)=>label&&normalizedLabels.indexOf(label)!==index)&&<p className="dimension-manager-message error">维度名称不能重复。</p>}<div className="dimension-manager-actions"><button className="btn btn-light" type="button" onClick={add}><Plus size={16}/> 添加维度</button><div><button className="btn btn-light" type="button" onClick={onClose}>取消</button><button className="btn btn-dark" type="button" onClick={save} disabled={!canSave||Boolean(removingCategory)}>保存</button></div></div></section></div>
}

function FilePurposeDrawer({file,dimensions,onClose,onSave}:{file:InputFile;dimensions:ModelDimension[];onClose:()=>void;onSave:(targets:string[])=>void}){
  const [targets,setTargets]=useState(file.targets)
  useModalScrollLock()
  function toggle(key:string){setTargets(current=>current.includes(key)?current.filter(item=>item!==key):[...current,key])}
  return <div className="overlay" onMouseDown={onClose}><aside className="drawer purpose-drawer" onMouseDown={e=>e.stopPropagation()}><div className="drawer-head"><div><div className="eyebrow">FILE PURPOSE</div><h2>调整文件用途</h2><p>同一评分指标可以关联多份文件。</p></div><button onClick={onClose} aria-label="关闭"><X/></button></div><div className="drawer-body"><div className="source-file-note"><strong>{file.name}</strong><span>当前关联指标</span></div><fieldset className="target-picker"><legend>这份文件用于哪些评分指标？</legend><div>{dimensions.map(dimension=>{const selected=targets.includes(dimension.key);return <button type="button" key={dimension.key} className={'target-option '+(selected?'selected':'')} onClick={()=>toggle(dimension.key)}><span>{dimension.label}</span>{selected?<Check size={15}/>:null}</button>})}</div><small>可与其他文件关联到同一指标；取消所有选择后，这份文件将仅作参考。</small></fieldset></div><div className="drawer-actions"><button className="btn btn-light" onClick={onClose}>取消</button><button className="btn btn-dark" onClick={()=>onSave(targets)}>保存用途</button></div></aside></div>
}

function MappingDrawer({file,evaluationYear,fields,onClose,onSave}:{file:InputFile;evaluationYear:number;fields:Record<string,string|null>;onClose:()=>void;onSave:(mapping:Mapping)=>void}){
  const year = evaluationYear
  useModalScrollLock()
  const [mapping,setMapping]=useState<Mapping>(structuredClone(file.mapping))
  const sheet=file.inspection.find(s=>s.name===mapping.sheet)
  const labels:Record<string,string>={market_share:'市场份额',revenue:'收入',gross_profit:'毛利',gross_margin:'毛利率',operating_profit:'营业利润额',operating_margin:'营业利润率',labor_output:'劳产',yuan_efficiency:'元效',human_efficiency:'人效',self_research:'自研能力',ability:'能力评测',quality:'品质服务'}
  const letters=Array.from({length:Math.min(sheet?.columns||25,40)},(_,i)=>{let n=i+1,s='';while(n){n--;s=String.fromCharCode(65+n%26)+s;n=Math.floor(n/26)}return s})
  const headerValues=sheet?.preview[mapping.header_row-1]||[]
  const optionName=(letter:string,index:number)=>headerValues[index]?`${headerValues[index]}（${letter}列）`:`${letter}列`
  const ready=mappingReady(mapping),dirty=JSON.stringify(mapping)!==JSON.stringify(file.mapping)
  const visibleFields=Object.keys(fields).filter(key=>!ready||Boolean(mapping.fields[key]))
  const fieldName=(key:string)=>mapping.input_labels?.[key]||`${key.split(':')[1]} 年${labels[key.split(':')[0]]||key}`
  return <div className="overlay" onMouseDown={onClose}><aside className="drawer mapping-drawer" onMouseDown={e=>e.stopPropagation()}><div className="drawer-head"><div><div className="eyebrow">CALCULATION BASIS</div><h2>{ready?'计算依据':'确认计算依据'}</h2><p>{ready?'系统已按模型规则识别本文件提供的原始数据。':'只需确认计算所需的原始数据，不需要选择最终得分。'}</p></div><button onClick={onClose} aria-label="关闭"><X/></button></div><div className="drawer-body"><div className="source-file-note"><strong>{file.name}</strong><span>{year} 年评估批次</span></div>{mapping.calculation_basis?.length?<div className="basis-list">{mapping.calculation_basis.map(item=><section key={item.dimension_key}><div><strong>{item.dimension_label}</strong><small>模型中心规则</small></div><p>{item.formula}</p><span>计算需要：{item.input_groups.map(group=>group.label).join('、')}</span></section>)}</div>:null}<div className="mapping-top"><label>数据所在工作表<SystemSelect label="数据所在工作表" value={mapping.sheet||''} onChange={e=>setMapping({...mapping,sheet:e.target.value||null})}><option value="">请选择</option>{file.inspection.map(s=><option key={s.name}>{s.name}</option>)}</SystemSelect></label><label>表头所在行<input type="number" min="1" value={mapping.header_row} onChange={e=>setMapping({...mapping,header_row:Number(e.target.value)})}/></label><label>产品线<SystemSelect label="产品线" value={mapping.product_col} onChange={e=>setMapping({...mapping,product_col:e.target.value})}>{letters.map((l,i)=><option key={l} value={l}>{optionName(l,i)}</option>)}</SystemSelect></label></div><h3>{ready?'本文件提供的数据':'请选择本文件提供的数据'}</h3><div className="field-list">{visibleFields.map(key=><label key={key}><span>{fieldName(key)}</span><SystemSelect label={fieldName(key)} value={mapping.fields[key]||''} onChange={e=>setMapping({...mapping,fields:{...mapping.fields,[key]:e.target.value||null}})}><option value="">本文件不提供</option>{letters.map((l,i)=><option key={l} value={l}>{optionName(l,i)}</option>)}</SystemSelect><input className="unit-input" aria-label={`${key}单位`} placeholder="单位" value={mapping.units?.[key]||''} onChange={e=>setMapping({...mapping,units:{...mapping.units,[key]:e.target.value}})}/></label>)}</div><h3>原表预览</h3>{sheet?<div className="preview-scroll"><table><tbody>{sheet.preview.map((row,i)=><tr key={i}><th>{i+1}</th>{row.map((cell,j)=><td key={j}><small>{letters[j]}</small>{cell}</td>)}</tr>)}</tbody></table></div>:<div className="empty">请先选择数据所在工作表</div>}<p className="mapping-hint">这里保存的是本批次的原始数据来源；计算公式仍由模型中心统一管理，并在评分明细中保留单元格依据。</p></div><div className="drawer-actions"><button className="btn btn-light" onClick={onClose}>{ready?'关闭':'稍后处理'}</button><button className="btn btn-dark" disabled={!mappingReady(mapping)} onClick={()=>dirty?onSave(mapping):onClose()}>{dirty?'保存调整':ready?'完成':'确认采用'}</button></div></aside></div>
}

createRoot(document.getElementById('root')!).render(<React.StrictMode><App/></React.StrictMode>)
