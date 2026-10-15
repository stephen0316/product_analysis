// Recognition timestamps and generated explanations are not user edits.
function configuration(value:unknown):unknown{
  if(Array.isArray(value))return value.map(configuration)
  if(value&&typeof value==='object'){
    return Object.fromEntries(Object.entries(value).sort(([a],[b])=>a.localeCompare(b)).flatMap(([key,item])=>{
      if(key==='parsed_rule'||key==='rule_parser')return []
      const fields=value as Record<string,unknown>
      if(key==='calculation_type'&&Array.isArray(fields.metric_keys)&&fields.metric_keys.length===1&&fields.metric_keys[0]===item)return []
      const normalized=configuration(item)
      if(key==='options'&&normalized&&typeof normalized==='object'&&!Object.keys(normalized).length)return []
      return [[key,normalized]]
    }))
  }
  return value
}

export function modelDraftSignature(model:{name:string;version:string;definition:unknown}):string{
  return JSON.stringify({name:model.name,version:model.version,definition:configuration(model.definition)})
}
