// Keep this presentation rule aligned with post_evaluation/report_text.py.
// Structured evidence_ids are deliberately left untouched.
export function cleanEvaluationText(text:string):string {
  if(!/P\d+D\d+C\d+/.test(text))return text
  const cluster=String.raw`P\d+D\d+C\d+(?:\s*[|,，;；、]\s*P\d+D\d+C\d+)*`
  return text.replace(new RegExp(String.raw`\[\s*${cluster}\s*\]|【\s*${cluster}\s*】|\(\s*${cluster}\s*\)|（\s*${cluster}\s*）|${cluster}`,'g'),'')
    .replace(/[ \t]+([，。；：、！？])/g,'$1').trim()
}
