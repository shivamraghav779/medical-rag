var ClinicalRagChatbot=(function(u){"use strict";var z=Object.defineProperty;var B=(u,f,m)=>f in u?z(u,f,{enumerable:!0,configurable:!0,writable:!0,value:m}):u[f]=m;var l=(u,f,m)=>B(u,typeof f!="symbol"?f+"":f,m);function f(s){return(s||"").replace(/[\u2018\u2019]/g,"'").replace(/[\u201C\u201D]/g,'"').replace(/\u2026/g,"...").replace(/[^\x09\x20-\x7E]/g,"").trim()}function m(s){const t=f(s.apiKey).replace(/^Bearer\s+/i,"").trim();if(!t)throw new Error("Missing data-api-key. Use a full JWT from login (ASCII only).");if(/[^\x20-\x7E]/.test(t))throw new Error("data-api-key contains invalid characters. Paste the raw JWT token.");return{Authorization:`Bearer ${t}`,"Content-Type":"application/json"}}async function S(s,e,t,n,i){const r={query:e};t&&(r.conversation_id=t),s.specialty&&(r.specialty=s.specialty);const a=await fetch(`${s.apiUrl.replace(/\/$/,"")}/api/chat`,{method:"POST",headers:m(s),body:JSON.stringify(r),signal:i});if(!a.ok||!a.body)throw new Error("Chat request failed");const o=a.body.getReader(),d=new TextDecoder;let c="",p=t;for(;;){const{done:g,value:w}=await o.read();if(g)break;c+=d.decode(w,{stream:!0});const v=c.split(`
`);c=v.pop()??"";for(const P of v){const k=P.trim();if(!k.startsWith("data:"))continue;const C=k.slice(5).trim();if(C)try{const b=JSON.parse(C);b.type==="conversation"&&typeof b.conversation_id=="string"&&(p=b.conversation_id),n(b)}catch{}}}return p}async function E(s,e){const t=await fetch(`${s.apiUrl.replace(/\/$/,"")}/api/handoff/request`,{method:"POST",headers:m(s),body:JSON.stringify({conversation_id:e,reason:"patient_request"})});if(!t.ok)throw new Error("Handoff request failed");return t.json()}function h(){return crypto.randomUUID()}function N(){return{id:h(),role:"assistant",content:"",agentSteps:[]}}function _(s){const e=document.createElement("div");e.id="clinical-rag-chatbot-host",e.style.all="initial",e.style.position="fixed",e.style.zIndex="2147483000",e.style.bottom="20px",e.style[s.position==="bottom-left"?"left":"right"]="20px";const t=e.attachShadow({mode:"open"}),n=document.createElement("style");n.textContent=T(s.primaryColor),t.appendChild(n);const i=document.createElement("div");i.className="cr-root";const r=document.createElement("button");r.className="cr-fab",r.type="button",r.setAttribute("aria-label","Open chat"),r.innerHTML=$();const a=document.createElement("div");a.className="cr-panel",a.hidden=!0,a.innerHTML=`
    <div class="cr-header">
      <div>
        <div class="cr-title"></div>
        <div class="cr-status"></div>
      </div>
      <button type="button" class="cr-close" aria-label="Close">${q()}</button>
    </div>
    <div class="cr-thread"></div>
    <div class="cr-composer">
      <textarea rows="1" placeholder="Ask a clinical question..."></textarea>
      <button type="button" class="cr-send" aria-label="Send">${A()}</button>
    </div>
  `,i.appendChild(a),i.appendChild(r),t.appendChild(i),document.body.appendChild(e);const o=a.querySelector(".cr-title");o.textContent=s.clinicName;const d=a.querySelector(".cr-status"),c=a.querySelector(".cr-thread"),p=a.querySelector("textarea"),g=a.querySelector(".cr-send");return a.querySelector(".cr-close").addEventListener("click",()=>{r.click()}),{host:e,shadow:t,button:r,panel:a,thread:c,input:p,sendBtn:g,statusEl:d,headerTitle:o}}function T(s){return`
    :host { all: initial; }
    * { box-sizing: border-box; font-family: Inter, system-ui, sans-serif; }
    .cr-root { position: relative; }
    .cr-fab {
      width: 56px; height: 56px; border-radius: 50%; border: none; cursor: pointer;
      background: ${s}; color: #fff; box-shadow: 0 8px 24px rgba(0,0,0,.25);
      display: grid; place-items: center;
    }
    .cr-panel {
      position: absolute; bottom: 72px; right: 0; width: 380px; height: 560px;
      background: #fff; border-radius: 18px; overflow: hidden;
      box-shadow: 0 16px 48px rgba(15,23,42,.28);
      display: flex; flex-direction: column;
      transform-origin: bottom right;
      animation: cr-slide .2s ease-out;
    }
    @keyframes cr-slide { from { opacity: 0; transform: translateY(12px) scale(.98);} to { opacity:1; transform:none;} }
    .cr-header {
      background: ${s}; color: #fff; padding: 14px 16px;
      display: flex; justify-content: space-between; align-items: flex-start;
    }
    .cr-title { font-weight: 700; font-size: 15px; }
    .cr-status { font-size: 12px; opacity: .9; margin-top: 4px; min-height: 16px; }
    .cr-close { background: transparent; border: none; color: #fff; cursor: pointer; }
    .cr-thread { flex: 1; overflow: auto; padding: 14px; background: #f8fafc; }
    .cr-msg { margin-bottom: 12px; max-width: 92%; }
    .cr-msg.user { margin-left: auto; }
    .cr-bubble {
      padding: 10px 12px; border-radius: 14px; font-size: 13.5px; line-height: 1.45;
      white-space: pre-wrap; word-break: break-word;
    }
    .cr-sender {
      font-size: 10px; font-weight: 700; letter-spacing: .04em; text-transform: uppercase;
      opacity: .7; margin-bottom: 4px;
    }
    .cr-msg.user .cr-bubble { background: ${s}; color: #fff; border-bottom-right-radius: 4px; }
    .cr-msg.assistant .cr-bubble, .cr-msg.agent .cr-bubble, .cr-msg.system .cr-bubble {
      background: #fff; color: #0f172a; border: 1px solid #e2e8f0; border-bottom-left-radius: 4px;
    }
    /* Human-agent messages get their own tinted background + accent border,
       not just the bot bubble with a thin colored edge — a patient should
       tell "this is a person" apart from "this is the bot" at a glance
       (UX_AUDIT.md: widget message bubbles). Deliberately not ${s}
       (the clinic's own brand color, already used for the patient's own
       messages) and not green (reserved for success/positive elsewhere) —
       a dedicated violet accent, same role as the main app's "agent" token. */
    .cr-msg.agent .cr-bubble { background: #f5f3ff; border-color: #c4b5fd; }
    .cr-msg.agent .cr-sender { color: #6d28d9; opacity: 1; }
    .cr-event {
      display: flex; align-items: center; gap: 10px;
      max-width: 100%; margin: 14px 0;
    }
    .cr-event-line { flex: 1; height: 1px; background: #cbd5e1; }
    .cr-event-label {
      flex-shrink: 0; font-size: 10px; font-weight: 600; letter-spacing: .04em;
      text-transform: uppercase; color: #64748b; text-align: center;
    }
    .cr-steps { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 6px; }
    /* Neutral slate, not violet — that accent is reserved for "a human
       agent is here" (see .cr-msg.agent above); these chips are the bot's
       own retrieval pipeline and shouldn't borrow the human signal. */
    .cr-chip {
      font-size: 11px; padding: 2px 8px; border-radius: 999px; background: #f1f5f9; color: #475569;
    }
    .cr-faith {
      display: inline-block; margin-top: 6px; font-size: 11px; font-weight: 600;
      padding: 2px 8px; border-radius: 999px;
    }
    .cr-faith--pass { background: #f0fdf4; color: #15803d; }
    .cr-faith--warn { background: #fffbeb; color: #b45309; }
    .cr-faith--fail { background: #fef2f2; color: #b91c1c; }
    .cr-cites { margin-top: 6px; display: flex; flex-wrap: wrap; gap: 4px; }
    .cr-cite {
      font-size: 11px; border: 1px solid #cbd5e1; border-radius: 8px; padding: 2px 6px;
      background: #fff; color: #334155; cursor: pointer;
    }
    .cr-handoff {
      margin-top: 6px; border: none; background: transparent; color: ${s};
      font-size: 12px; cursor: pointer; text-decoration: underline; padding: 0;
    }
    .cr-composer {
      display: flex; gap: 8px; padding: 10px; border-top: 1px solid #e2e8f0; background: #fff;
    }
    .cr-composer textarea {
      flex: 1; resize: none; border: 1px solid #cbd5e1; border-radius: 12px;
      padding: 10px 12px; font-size: 13px; max-height: 90px; outline: none;
    }
    .cr-send {
      width: 40px; height: 40px; border: none; border-radius: 12px; background: ${s};
      color: #fff; cursor: pointer; display: grid; place-items: center;
    }
    .cr-send:disabled { opacity: .5; cursor: default; }
  `}function $(){return'<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>'}function q(){return'<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6 6 18M6 6l12 12"/></svg>'}function A(){return'<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m22 2-7 20-4-9-9-4Z"/><path d="M22 2 11 13"/></svg>'}class I{constructor(e,t,n){l(this,"ws",null);l(this,"closed",!1);this.url=e,this.onMessage=t,this.onClose=n}connect(){this.ws=new WebSocket(this.url),this.ws.onmessage=e=>{try{const t=JSON.parse(String(e.data));this.onMessage(t)}catch{}},this.ws.onclose=()=>{var e;this.ws=null,this.closed||(e=this.onClose)==null||e.call(this)}}get ready(){return!!this.ws&&this.ws.readyState===WebSocket.OPEN}send(e){this.ws&&this.ws.readyState===WebSocket.OPEN&&this.ws.send(JSON.stringify(e))}close(){var e;this.closed=!0,(e=this.ws)==null||e.close(),this.ws=null}}function M(s,e,t){return`${s.replace(/^http/,"ws").replace(/\/$/,"")}/ws/chat/${e}?token=${encodeURIComponent(t)}`}function U(s,e,t){return s==="QUEUED"?e!=null?`Waiting for a specialist · position ${e+1}`:"Waiting for a specialist…":s==="HUMAN_ACTIVE"?t?`Connected with ${t}`:"Connected to a specialist":s==="RESOLVED"?"Conversation resolved — assistant is back":"Clinical assistant online"}class x{constructor(e){l(this,"open",!1);l(this,"messages",[]);l(this,"conversationId",null);l(this,"streaming",!1);l(this,"state","BOT_ACTIVE");l(this,"queuePosition",0);l(this,"agentName",null);l(this,"socket",null);l(this,"reconnectTimer",null);l(this,"dom");this.config=e,this.dom=_(e),this.bind(),this.render()}bind(){this.dom.button.addEventListener("click",()=>{this.open=!this.open,this.dom.panel.hidden=!this.open,this.dom.button.innerHTML=this.open?'<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6 6 18M6 6l12 12"/></svg>':'<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>'}),this.dom.sendBtn.addEventListener("click",()=>void this.send()),this.dom.input.addEventListener("keydown",e=>{e.key==="Enter"&&!e.shiftKey&&(e.preventDefault(),this.send())}),window.addEventListener("message",e=>{const t=e.data;(t==null?void 0:t.type)==="clinical-rag-widget-config"&&t.config&&(t.config.clinicName&&(this.config.clinicName=t.config.clinicName,this.dom.headerTitle.textContent=t.config.clinicName),t.config.specialty&&(this.config.specialty=t.config.specialty),t.config.primaryColor&&(this.config.primaryColor=t.config.primaryColor))})}async send(){var n,i;const e=this.dom.input.value.trim();if(!e||this.streaming)return;if(this.dom.input.value="",this.state==="HUMAN_ACTIVE"){this.messages.push({id:h(),role:"user",content:e}),(n=this.socket)!=null&&n.ready||this.connectPatientSocket(),(i=this.socket)==null||i.send({type:"message",content:e}),this.render();return}this.messages.push({id:h(),role:"user",content:e});const t=N();this.messages.push(t),this.streaming=!0,this.render();try{this.conversationId=await S(this.config,e,this.conversationId??"",r=>this.onStreamEvent(t,r))}catch(r){t.content=r instanceof Error?r.message:"Something went wrong."}finally{this.streaming=!1,this.render()}}onStreamEvent(e,t){const n=String(t.type||"");if(n==="conversation"&&typeof t.conversation_id=="string"&&(this.conversationId=t.conversation_id),n==="token"&&(e.content+=String(t.content||"")),n==="agent_status"){e.agentSteps=e.agentSteps||[];const i=String(t.agent||""),r=String(t.status||""),a=e.agentSteps.find(o=>o.agent===i);a?a.status=r:e.agentSteps.push({agent:i,status:r,output:t.output})}n==="citations"&&Array.isArray(t.chunks)&&(e.citations=t.chunks.map(i=>({doc_name:String(i.doc_name||"doc"),page_number:Number(i.page_number||0),text:i.text?String(i.text):void 0}))),n==="faithfulness"&&(e.faithfulness={score:Number(t.score||0),verdict:String(t.verdict||"PASS")}),n==="handoff_initiated"&&(this.state="QUEUED",this.queuePosition=Number(t.queue_position||0),this.messages.push({id:h(),role:"event",content:"Connecting with a human specialist"}),this.connectPatientSocket()),n==="queue_position"&&(this.queuePosition=Number(t.position||0),this.state=t.state||this.state),this.render()}async handoff(){if(this.conversationId)try{const e=await E(this.config,this.conversationId);this.state=e.state||"QUEUED",this.queuePosition=e.queue_position,this.messages.push({id:h(),role:"event",content:"Connecting with a human specialist"}),this.connectPatientSocket(),this.render()}catch(e){this.messages.push({id:h(),role:"event",content:e instanceof Error?e.message:"Handoff failed"}),this.render()}}connectPatientSocket(){var t;if(!this.conversationId||(t=this.socket)!=null&&t.ready)return;this.socket&&(this.socket.close(),this.socket=null),this.reconnectTimer&&(clearTimeout(this.reconnectTimer),this.reconnectTimer=null);const e=M(this.config.apiUrl,this.conversationId,this.config.apiKey);this.socket=new I(e,n=>this.onSocketMessage(n),()=>{this.socket=null,(this.state==="QUEUED"||this.state==="HUMAN_ACTIVE")&&(this.reconnectTimer=setTimeout(()=>this.connectPatientSocket(),800))}),this.socket.connect()}onSocketMessage(e){var n;const t=String(e.type||"");if(t==="state_resume"&&(this.state=e.state||this.state,typeof e.agent_name=="string"&&(this.agentName=e.agent_name),typeof e.queue_position=="number"&&(this.queuePosition=e.queue_position),this.messages.length===0&&Array.isArray(e.messages)))for(const i of e.messages){const r=String(i.role||""),a=String(i.content||"");a&&(r==="user"?this.messages.push({id:h(),role:"user",content:a}):r==="assistant"&&this.messages.push({id:h(),role:"agent",senderName:this.agentName||"Specialist",content:a}))}if(t==="queue_position"&&(this.queuePosition=Number(e.position||0),this.state=e.state||this.state),t==="agent_connected"){this.state="HUMAN_ACTIVE";const i=String(e.agent_name||"a specialist");this.agentName=i,this.messages.some(a=>a.role==="event"&&a.content.toLowerCase().startsWith("connected with"))||this.messages.push({id:h(),role:"event",content:`Connected with ${i}`})}if(t==="agent_message"){const i=String(e.agent_name||this.agentName||"Specialist");this.agentName=i,this.messages.push({id:h(),role:"agent",senderName:i,content:String(e.content||"")})}(t==="conversation_resolved"||t==="agent_disconnected")&&(this.agentName=null,this.state="BOT_ACTIVE",this.messages.push({id:h(),role:"event",content:t==="conversation_resolved"?"Conversation ended — assistant is back":"Specialist disconnected — assistant is back"}),(n=this.socket)==null||n.close(),this.socket=null),this.render()}render(){var e,t;this.dom.statusEl.textContent=U(this.state,this.queuePosition,this.agentName),this.dom.sendBtn.disabled=this.streaming,this.dom.thread.innerHTML="";for(const n of this.messages){if(n.role==="event"||n.role==="system"){const o=document.createElement("div");o.className="cr-event",o.setAttribute("role","separator");const d=document.createElement("div");d.className="cr-event-line";const c=document.createElement("span");c.className="cr-event-label",c.textContent=n.content;const p=document.createElement("div");p.className="cr-event-line",o.append(d,c,p),this.dom.thread.appendChild(o);continue}const i=document.createElement("div");i.className=`cr-msg ${n.role==="user"?"user":n.role}`;const r=document.createElement("div");if(r.className="cr-bubble",n.role==="agent"||n.role==="assistant"){const o=document.createElement("div");o.className="cr-sender",o.textContent=n.role==="agent"?n.senderName||this.agentName||"Specialist":"Assistant",r.appendChild(o)}const a=document.createElement("div");if(a.textContent=n.content||(this.streaming&&n.role==="assistant"?"…":""),r.appendChild(a),i.appendChild(r),(e=n.agentSteps)!=null&&e.length){const o=document.createElement("div");o.className="cr-steps";for(const d of n.agentSteps){const c=document.createElement("span");c.className="cr-chip",c.textContent=`${d.agent}: ${d.status}`,o.appendChild(c)}i.appendChild(o)}if((t=n.citations)!=null&&t.length){const o=document.createElement("div");o.className="cr-cites";for(const d of n.citations){const c=document.createElement("details");c.className="cr-cite";const p=document.createElement("summary");if(p.textContent=`${d.doc_name} p.${d.page_number}`,c.appendChild(p),d.text){const g=document.createElement("div");g.textContent=d.text.slice(0,240),c.appendChild(g)}o.appendChild(c)}i.appendChild(o)}if(n.faithfulness){const o=document.createElement("span");o.className=`cr-faith cr-faith--${n.faithfulness.verdict.toLowerCase()}`,o.textContent=`Faithfulness ${Math.round(n.faithfulness.score*100)}% · ${n.faithfulness.verdict}`,o.title="How closely this answer matches the retrieved documents",i.appendChild(o)}if(n.role==="assistant"&&n.content&&this.state==="BOT_ACTIVE"){const o=document.createElement("button");o.type="button",o.className="cr-handoff",o.textContent="Connect to human",o.addEventListener("click",()=>void this.handoff()),i.appendChild(o)}this.dom.thread.appendChild(i)}this.dom.thread.scrollTop=this.dom.thread.scrollHeight}}function H(s){const e=s.dataset.apiKey||s.getAttribute("data-api-key")||"";return{apiUrl:(s.dataset.apiUrl||window.location.origin).trim(),apiKey:e.replace(/^\uFEFF/,"").replace(/[\u200B-\u200D\uFEFF]/g,"").replace(/\u2026/g,"").trim(),specialty:s.dataset.specialty||void 0,clinicName:s.dataset.clinicName||"Clinical Assistant",primaryColor:s.dataset.primaryColor||"#7c3aed",position:s.dataset.position==="bottom-left"?"bottom-left":"bottom-right"}}function y(){const s=document.currentScript instanceof HTMLScriptElement?document.currentScript:document.querySelector("script[data-api-url]");if(!s)return;const e=H(s);new x(e)}return document.readyState==="loading"?document.addEventListener("DOMContentLoaded",y):y(),u.ClinicalChatWidget=x,Object.defineProperty(u,Symbol.toStringTag,{value:"Module"}),u})({});
