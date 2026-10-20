import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { CircleAlert, FileSpreadsheet, FileText, RefreshCw, Sparkles } from 'lucide-react'

type Job = {status:'idle'|'queued'|'running'|'completed'|'failed';stage:string;completed?:number;total?:number;message?:string}
type Notes = {analysis:string;recommendations:string;review:string}
type Props = {runId:string;trigger:number;generatedAt?:string;hasResult:boolean;configured:boolean;statusNote:string;notes:Notes;onComplete:()=>Promise<void>}
const stages = [
  ['preparing','整理本批次真实数据'], ['dimensions','分析各维度经营表现'],
  ['products','撰写各产品线评价'], ['summary','汇总总体结论'], ['rendering','生成图表与 Word 报告'],
]
async function request<T>(path:string, method='GET', body?:unknown):Promise<T>{
  const response=await fetch('/api'+path,{method,headers:body===undefined?undefined:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)})
  if(!response.ok){const error=await response.json().catch(()=>({detail:'请求失败，请重试'}));throw new Error(error.detail)}
  return response.json()
}

export function ReportWorkspace(props:Props){
  const [job,setJob]=useState<Job>({status:'idle',stage:'idle'})
  const [requestError,setRequestError]=useState(''),[attempt,setAttempt]=useState(0),[refresh,setRefresh]=useState(0)
  const [previewError,setPreviewError]=useState(false)
  const latest=useRef(props);latest.current=props
  const workspace=useRef<HTMLDivElement>(null)
  useLayoutEffect(()=>{
    const topbar=workspace.current?.closest('main')?.querySelector('.topbar')
    if(!topbar)return
    const measure=()=>workspace.current?.style.setProperty('--report-topbar-height',`${topbar.getBoundingClientRect().height}px`)
    measure()
    const observer=new ResizeObserver(measure)
    observer.observe(topbar)
    return ()=>observer.disconnect()
  },[])
  const hasReport=Boolean(props.generatedAt)
  const generating=job.status==='queued'||job.status==='running'
  useEffect(()=>{
    let cancelled=false,timer:number|undefined
    const runId=props.runId
    async function poll(){
      try{
        const state=await request<Job>(`/runs/${runId}/ai-report`)
        if(cancelled)return
        setJob(state);setRequestError('')
        if(state.status==='queued'||state.status==='running')timer=window.setTimeout(poll,1200)
        else if(state.status==='completed'){
          await latest.current.onComplete()
          if(!cancelled){setRefresh(value=>value+1);setPreviewError(false)}
        }
      }catch(error){if(!cancelled){setRequestError((error as Error).message);setJob({status:'failed',stage:'failed'})}}
    }
    async function start(){
      setRequestError('');setJob({status:'queued',stage:'preparing'})
      try{
        await request<Job>(`/runs/${runId}/ai-report`,'POST',latest.current.notes)
        if(!cancelled)await poll()
      }catch(error){if(!cancelled){setRequestError((error as Error).message);setJob({status:'failed',stage:'failed'})}}
    }
    if(attempt)void start();else void poll()
    return ()=>{cancelled=true;if(timer!==undefined)window.clearTimeout(timer)}
  },[props.runId,props.trigger,attempt])
  const stageIndex=Math.max(0,stages.findIndex(([key])=>key===job.stage))
  function regenerate(){setJob({status:'queued',stage:'preparing'});setAttempt(value=>value+1)}
  return <div className="report-workspace" ref={workspace}>
    <section className="panel report-preview-panel" aria-label="报告预览" aria-busy={generating}>
      <div className="panel-head report-preview-head"><div><h2>报告预览</h2><p>{hasReport?'内容与导出的 Word 报告一致，排版以 Word 为准。':'生成总体结论、分维度洞察及分产品评价。'}</p></div>{hasReport&&<button className="btn btn-light" onClick={regenerate} disabled={generating||!props.configured}><RefreshCw size={15}/>重新生成</button>}</div>
      {generating?<div className="report-generation" role="status" aria-live="polite"><div className="report-loading-document" aria-hidden="true"><FileText size={38}/><span/></div><h3>正在生成 AI 报告</h3><p>{stages[stageIndex][1]}{Boolean(job.total)&&`（${job.completed}/${job.total}）`}</p><ol className="report-generation-stages">{stages.map(([key,label],index)=><li key={key} className={index<stageIndex?'done':index===stageIndex?'current':''}><span>{index<stageIndex?'✓':index+1}</span>{label}</li>)}</ol>{hasReport&&<p className="muted">新报告生成成功前，仍可下载上一次报告。</p>}</div>:hasReport?<>
        {(job.status==='failed'||requestError)&&<div className="report-generation-error" role="alert"><CircleAlert size={17}/><div>{requestError||job.message}<p>下方仍为上一次成功生成的报告。</p><button className="text-action" onClick={regenerate}>重试生成</button></div></div>}
        {previewError?<div className="centered"><h3>预览暂时无法加载</h3><p>可先在右侧下载 Word 报告。</p><button className="btn btn-light" onClick={()=>{setPreviewError(false);setRefresh(value=>value+1)}}>重新加载预览</button></div>:<iframe key={props.generatedAt+':'+refresh} className="report-preview-frame" title="后评估报告内容预览" sandbox="" src={`/api/runs/${props.runId}/report-preview?v=${encodeURIComponent(props.generatedAt||'')}&r=${refresh}`} onError={()=>setPreviewError(true)}/>}
      </>:<div className="report-generation report-generation-empty"><FileText size={42}/><h3>{job.status==='failed'?'报告尚未生成':'生成后评估报告'}</h3><p>{requestError||job.message||'AI 将结合评分结果完成分析，下载后可在 Word 中编辑。'}</p><button className="btn btn-dark" onClick={regenerate} disabled={!props.hasResult||!props.configured}><Sparkles size={16}/>{job.status==='failed'?'重试生成':'AI 生成报告'}</button>{!props.configured&&<p className="muted">AI 报告服务尚未配置。</p>}</div>}
    </section>
    <aside className="panel report-deliverables"><div className="panel-head"><div><h2>导出交付物</h2><p>{hasReport?'下载后可在 Word 中修订和审阅。':'评分总表可先行导出。'}</p></div></div>
      <div className="export-item"><FileSpreadsheet size={23}/><div><strong>评分总表 .xlsx</strong><small>按当前模型生成各维度明细及总表</small></div>{props.hasResult?<a className="btn btn-light" href={`/api/runs/${props.runId}/export.xlsx`}>导出</a>:<button className="btn btn-light" disabled>导出</button>}</div>
      {hasReport&&<div className="export-item"><FileText size={23}/><div><strong>后评估报告 .docx</strong><small>总体结论、分维度洞察与分产品评价</small></div><a className="btn btn-light" href={`/api/runs/${props.runId}/report.docx`}>导出</a></div>}
      {hasReport&&<p className="muted report-generated-time">生成时间 {props.generatedAt?.slice(0,16).replace('T',' ')}</p>}
      <div className="report-warning"><CircleAlert size={17}/><span>{props.statusNote}</span></div>
    </aside>
  </div>
}
