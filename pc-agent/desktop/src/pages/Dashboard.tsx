import { SensorData } from "../lib/api";
import { useTheme } from "../lib/ThemeContext";
import HeroCard from "../components/HeroCard";
import FanBar from "../components/FanBar";
import Card from "../components/Card";
import FanModeCard from "../components/FanModeCard";
import { useRef, useEffect, useState } from "react";
import { loadSettings, AppSettings } from "./Settings";
import { addAlert } from "../lib/alertStore";
import { sendNotification } from "../lib/notify";

type Props = { data: SensorData; connected: boolean };

const MAX_RPM_FALLBACK: Record<string, number> = { "CPU Fan": 4500, "GPU Fan": 6500 };
function fanPercent(fan: { name: string; rpm: number; percent: number | null }): number {
  // Backend supplies percent based on BIOS-reported max RPM when available.
  if (fan.percent != null) return Math.min(100, Math.round(fan.percent));
  return Math.min(100, Math.round((fan.rpm / (MAX_RPM_FALLBACK[fan.name] || 5000)) * 100));
}

type AlertState = "ok" | "warning" | "critical";
const alertStates: Record<string, AlertState> = {};
const lastNotif: Record<string, number> = {};
const NOTIF_REPEAT = 30000;

function notify(title: string, body: string) {
  sendNotification(title, body);
}

function checkMetric(key: string, value: number | null, warn: number, crit: number, label: string, enabled: boolean) {
  if (!enabled || value === null) return;
  const now = Date.now();
  const prev = alertStates[key] || "ok";
  let next: AlertState = "ok";
  const hysteresis = 3;
  if (value >= crit) next = "critical";
  else if (value >= warn) next = "warning";
  else if (prev !== "ok" && value >= warn - hysteresis) next = prev; // Hold state until clear

  if (next !== prev) {
    alertStates[key] = next;
    if (next === "critical") {
      addAlert("critical", `${label} reached ${value}°C — exceeds critical threshold (${crit}°C)`);
      notify("THERM_OS — Critical", `${label} is ${value}°C (critical: ${crit}°C)`);
    } else if (next === "warning") {
      addAlert("warning", `${label} reached ${value}°C — exceeds warning threshold (${warn}°C)`);
      notify("THERM_OS — Warning", `${label} is ${value}°C (warning: ${warn}°C)`);
    } else if (next === "ok" && prev !== "ok") {
      addAlert("info", `${label} returned to normal: ${value}°C`);
      // No notification on resolve — only log it
    }
    lastNotif[key] = now;
    return;
  }

  if (next !== "ok" && now - (lastNotif[key] || 0) >= NOTIF_REPEAT) {
    const sev = next === "critical" ? "Critical" : "Warning";
    notify(`THERM_OS — ${sev}`, `${label} still at ${value}°C (threshold: ${next === "critical" ? crit : warn}°C)`);
    lastNotif[key] = now;
  }
}

export default function Dashboard({ data, connected }: Props) {
  const { colors } = useTheme();
  const [settings, setSettings] = useState<AppSettings>(loadSettings);
  useEffect(() => { const i = setInterval(() => setSettings(loadSettings()), 2000); return () => clearInterval(i); }, []);

  const cpuTemp = data.cpu_temp != null ? Math.round(data.cpu_temp) : null;
  const gpuTemp = data.gpu_temp != null ? Math.round(data.gpu_temp) : null;
  const fanCount = data.fan_speeds.length;
  const avgRpm = fanCount > 0 ? Math.round(data.fan_speeds.reduce((s, f) => s + f.rpm, 0) / fanCount) : 0;
  const ramUsage = data.ram_usage != null ? Math.round(data.ram_usage) : null;

  useEffect(() => {
    if (!connected) return;
    checkMetric("cpu", cpuTemp, settings.cpuWarnThreshold, settings.cpuCritThreshold, "CPU temperature", settings.enableNotifications);
    checkMetric("gpu", gpuTemp, settings.gpuWarnThreshold, settings.gpuCritThreshold, "GPU temperature", settings.enableNotifications);
  }, [cpuTemp, gpuTemp, settings, connected]);

  const getStatus = () => {
    if (!connected) return { text: "Agent disconnected", level: "disconnected" as const };
    const cs = alertStates["cpu"] || "ok", gs = alertStates["gpu"] || "ok";
    if (cs === "critical") return { text: `CPU critical: ${cpuTemp}°C`, level: "critical" as const };
    if (gs === "critical") return { text: `GPU critical: ${gpuTemp}°C`, level: "critical" as const };
    if (cs === "warning") return { text: `CPU warm: ${cpuTemp}°C`, level: "warning" as const };
    if (gs === "warning") return { text: `GPU warm: ${gpuTemp}°C`, level: "warning" as const };
    return { text: "All systems nominal", level: "ok" as const };
  };
  const status = getStatus();
  const statusBg = status.level === "critical" ? colors.dangerSoft : status.level === "warning" ? colors.warnSoft : status.level === "disconnected" ? colors.dangerSoft : colors.badgeBg;
  const statusColor = status.level === "critical" ? colors.danger : status.level === "warning" ? colors.warn : status.level === "disconnected" ? colors.danger : colors.badgeText;
  const statusDot = status.level === "critical" ? colors.danger : status.level === "warning" ? colors.warn : status.level === "disconnected" ? colors.danger : colors.accent;
  const cpuC = (alertStates["cpu"]||"ok") === "critical" ? colors.danger : (alertStates["cpu"]||"ok") === "warning" ? colors.warn : colors.accent;
  const gpuC = (alertStates["gpu"]||"ok") === "critical" ? colors.danger : (alertStates["gpu"]||"ok") === "warning" ? colors.warn : colors.accent;
  const storageMap = new Map<string,{drive:string;temp:number}>();
  for (const s of data.storage_temps) { const e = storageMap.get(s.drive); if (!e || s.temp > e.temp) storageMap.set(s.drive, {drive:s.drive,temp:s.temp}); }
  const storageTemps = Array.from(storageMap.values());

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <div style={{ flex: 1, overflowY: "auto", padding: "24px 28px", display: "flex", flexDirection: "column", gap: 18 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
          <div>
            <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 10, fontWeight: 500, color: colors.accent, letterSpacing: 2, textTransform: "uppercase", marginBottom: 6 }}>Live telemetry</div>
            <div style={{ fontFamily: "'Space Grotesk', sans-serif", fontSize: 26, fontWeight: 700, color: colors.text0, lineHeight: 1.15 }}>System Dashboard</div>
          </div>
          <div style={{ display: "inline-flex", alignItems: "center", gap: 6, padding: "6px 14px", borderRadius: 20, background: statusBg, color: statusColor, fontSize: 12, fontWeight: 600 }}>
            <span style={{ width: 6, height: 6, borderRadius: "50%", background: statusDot }} />{status.text}
          </div>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 14 }}>
          <HeroCard label="CPU Temperature" value={cpuTemp !== null ? cpuTemp.toString() : "—"} unit="°C" subText={cpuTemp!==null?((alertStates["cpu"]||"ok")==="critical"?"Critical!":(alertStates["cpu"]||"ok")==="warning"?"Warning":"Normal"):"No data"} trend={(alertStates["cpu"]||"ok")!=="ok"?"up":"flat"} iconBg={(alertStates["cpu"]||"ok")==="critical"?colors.dangerSoft:(alertStates["cpu"]||"ok")==="warning"?colors.warnSoft:colors.accentSoft} iconColor={cpuC} icon={<CpuIcon/>}/>
          <HeroCard label="GPU Temperature" value={gpuTemp !== null ? gpuTemp.toString() : "—"} unit="°C" subText={gpuTemp!==null?((alertStates["gpu"]||"ok")==="critical"?"Critical!":(alertStates["gpu"]||"ok")==="warning"?"Warning":"Normal"):"No data"} trend={(alertStates["gpu"]||"ok")!=="ok"?"up":"flat"} iconBg={(alertStates["gpu"]||"ok")==="critical"?colors.dangerSoft:(alertStates["gpu"]||"ok")==="warning"?colors.warnSoft:colors.accentSoft} iconColor={gpuC} icon={<GpuIcon/>}/>
          <HeroCard label="Fan Speed" value={fanCount>0?avgRpm.toLocaleString():"—"} unit="RPM" subText={fanCount>0?`${fanCount} fan${fanCount>1?"s":""} active`:"No fans"} trend={avgRpm>4000?"up":"flat"} iconBg={colors.cyanSoft} iconColor={colors.cyan} icon={<FanIcon/>}/>
          <HeroCard label="RAM Usage" value={ramUsage!==null?ramUsage.toString():"—"} unit="%" subText={ramUsage!==null?(ramUsage<60?"Normal":ramUsage<85?"Moderate":"High"):"No data"} trend={ramUsage!==null&&ramUsage>80?"up":"flat"} iconBg={colors.warnSoft} iconColor={colors.warn} icon={<RamIcon/>}/>
        </div>
        <FanModeCard />
        <div style={{ display: "grid", gridTemplateColumns: "1.5fr 1fr", gap: 16 }}>
          <Card label="Temperature History" subLabel="CPU and GPU temperature over time" rightHeader={<div style={{display:"flex",gap:12}}><LegendDot label="CPU" color={colors.accent} textColor={colors.text2}/><LegendDot label="GPU" color={colors.warn} textColor={colors.text2}/></div>}>
            <ThermalChart data={data} colors={colors} settings={settings}/>
          </Card>
          <Card label="Fan Speeds" subLabel={fanCount>0?`${fanCount} fans detected`:"No fans"}>
            <div style={{display:"flex",flexDirection:"column",gap:14}}>
              {data.fan_speeds.map((fan,i)=><FanBar key={i} name={fan.name} rpm={fan.rpm} percent={fanPercent(fan)}/>)}
              {fanCount===0&&<div style={{color:colors.text3,fontSize:12,textAlign:"center",padding:20}}>No fan data</div>}
            </div>
            {data.active_profile&&(<div style={{background:colors.bg0,borderRadius:8,padding:"12px 16px",marginTop:16,display:"flex",justifyContent:"space-between",alignItems:"center"}}><span style={{fontFamily:"'JetBrains Mono', monospace",fontSize:10,color:colors.text3,textTransform:"uppercase",letterSpacing:0.8}}>Active profile</span><span style={{fontFamily:"'Space Grotesk', sans-serif",fontSize:13,fontWeight:600,color:colors.accent}}>{data.active_profile}</span></div>)}
          </Card>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
          <Card label="GPU Details" subLabel={data.gpu_name||"No GPU detected"}>
            <div style={{display:"flex",flexDirection:"column",gap:10}}>
              <InfoRow label="Temperature" value={gpuTemp!==null?`${gpuTemp}°C`:"—"} colors={colors}/>
              <InfoRow label="Hot spot" value={data.gpu_hot_spot!=null?`${Math.round(data.gpu_hot_spot)}°C`:"—"} colors={colors}/>
              <InfoRow label="Load" value={data.gpu_load!=null?`${Math.round(data.gpu_load)}%`:"—"} colors={colors}/>
              <InfoRow label="Core clock" value={data.gpu_clock_core!=null?`${data.gpu_clock_core} MHz`:"—"} colors={colors}/>
              <InfoRow label="Memory clock" value={data.gpu_clock_mem!=null?`${data.gpu_clock_mem} MHz`:"—"} colors={colors}/>
              <InfoRow label="VRAM" value={data.gpu_mem_used!=null&&data.gpu_mem_total!=null?`${data.gpu_mem_used} / ${data.gpu_mem_total} MB`:"—"} colors={colors}/>
            </div>
          </Card>
          <Card label="Hardware Info" subLabel="Detected components">
            <div style={{display:"flex",flexDirection:"column",gap:8}}>
              {data.cpu_name&&<InfoPill label="CPU" value={data.cpu_name} colors={colors}/>}
              {data.gpu_name&&<InfoPill label="GPU" value={data.gpu_name} colors={colors}/>}
              <InfoPill label="CPU Load" value={data.cpu_load!=null?`${Math.round(data.cpu_load)}%`:"—"} colors={colors}/>
              <InfoPill label="RAM" value={ramUsage!==null?`${ramUsage}%`:"—"} colors={colors}/>
              {storageTemps.map((s,i)=><InfoPill key={i} label={s.drive} value={`${Math.round(s.temp)}°C`} colors={colors}/>)}
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}

function CpuIcon(){return <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6"/><path d="M15 2v2M15 20v2M2 15h2M20 15h2M9 2v2M9 20v2M2 9h2M20 9h2"/></svg>;}
function GpuIcon(){return <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="2" y="6" width="20" height="12" rx="2"/><path d="M6 12h.01M10 12h.01M14 12h.01M18 12h.01"/></svg>;}
function FanIcon(){return <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 12c-3-2.5-3-7 0-7s3 4.5 0 7zM12 12c2.5 3 7 3 7 0s-4.5-3-7 0zM12 12c3 2.5 3 7 0 7s-3-4.5 0-7zM12 12c-2.5-3-7-3-7 0s4.5 3 7 0z"/><circle cx="12" cy="12" r="1"/></svg>;}
function RamIcon(){return <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M6 19v2M10 19v2M14 19v2M18 19v2"/><rect x="3" y="7" width="18" height="12" rx="2"/><path d="M7 11h2v4H7zM11 11h2v4h-2zM15 11h2v4h-2z"/></svg>;}
function InfoRow({label,value,colors}:{label:string;value:string;colors:any}){return <div style={{display:"flex",justifyContent:"space-between",alignItems:"center"}}><span style={{fontSize:12,color:colors.text2}}>{label}</span><span style={{fontFamily:"'JetBrains Mono', monospace",fontSize:12,fontWeight:500,color:colors.text0}}>{value}</span></div>;}
function InfoPill({label,value,colors}:{label:string;value:string;colors:any}){return <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",padding:"8px 12px",background:colors.bg0,borderRadius:8}}><span style={{fontSize:12,color:colors.text2}}>{label}</span><span style={{fontFamily:"'JetBrains Mono', monospace",fontSize:11,fontWeight:500,color:colors.text0,maxWidth:200,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{value}</span></div>;}
function LegendDot({label,color,textColor}:{label:string;color:string;textColor:string}){return <div style={{display:"flex",alignItems:"center",gap:5}}><span style={{width:6,height:6,borderRadius:"50%",background:color}}/><span style={{fontSize:10,color:textColor}}>{label}</span></div>;}
function ThermalChart({data,colors,settings}:{data:SensorData;colors:any;settings:AppSettings}){
  const historyRef=useRef<{cpu:number;gpu:number}[]>([]);const canvasRef=useRef<HTMLCanvasElement>(null);
  useEffect(()=>{historyRef.current.push({cpu:data.cpu_temp||0,gpu:data.gpu_temp||0});if(historyRef.current.length>60)historyRef.current=historyRef.current.slice(-60);drawChart();},[data.cpu_temp,data.gpu_temp,settings]);
  const drawChart=()=>{const canvas=canvasRef.current;if(!canvas)return;const ctx=canvas.getContext("2d");if(!ctx)return;const dpr=window.devicePixelRatio||1;const rect=canvas.getBoundingClientRect();canvas.width=rect.width*dpr;canvas.height=rect.height*dpr;ctx.scale(dpr,dpr);const w=rect.width,h=rect.height,history=historyRef.current;const pad={top:8,bottom:20,left:35,right:12};const chartW=w-pad.left-pad.right,chartH=h-pad.top-pad.bottom;ctx.clearRect(0,0,w,h);if(history.length<2)return;const minY=20,maxY=100;const toX=(i:number)=>pad.left+(i/(history.length-1))*chartW;const toY=(val:number)=>pad.top+chartH-((val-minY)/(maxY-minY))*chartH;ctx.setLineDash([4,4]);ctx.strokeStyle=colors.warn+"60";ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(pad.left,toY(settings.cpuWarnThreshold));ctx.lineTo(w-pad.right,toY(settings.cpuWarnThreshold));ctx.stroke();ctx.strokeStyle=colors.danger+"60";ctx.beginPath();ctx.moveTo(pad.left,toY(settings.cpuCritThreshold));ctx.lineTo(w-pad.right,toY(settings.cpuCritThreshold));ctx.stroke();ctx.setLineDash([]);ctx.strokeStyle=colors.border2;ctx.lineWidth=0.5;for(const temp of[30,50,70,90]){const y=toY(temp);ctx.beginPath();ctx.moveTo(pad.left,y);ctx.lineTo(w-pad.right,y);ctx.stroke();ctx.fillStyle=colors.text3;ctx.font="9px 'JetBrains Mono', monospace";ctx.textAlign="right";ctx.fillText(`${temp}°`,pad.left-6,y+3);}const drawSeries=(key:"cpu"|"gpu",stroke:string,fill:string)=>{ctx.beginPath();ctx.moveTo(toX(0),toY(history[0][key]));for(let i=1;i<history.length;i++)ctx.lineTo(toX(i),toY(history[i][key]));ctx.lineTo(toX(history.length-1),pad.top+chartH);ctx.lineTo(toX(0),pad.top+chartH);ctx.closePath();ctx.fillStyle=fill;ctx.fill();ctx.beginPath();ctx.moveTo(toX(0),toY(history[0][key]));for(let i=1;i<history.length;i++)ctx.lineTo(toX(i),toY(history[i][key]));ctx.strokeStyle=stroke;ctx.lineWidth=1.5;ctx.stroke();};drawSeries("gpu",colors.warn,colors.warnSoft);drawSeries("cpu",colors.accent,colors.accentSoft);const last=history[history.length-1],x=toX(history.length-1);ctx.beginPath();ctx.arc(x,toY(last.cpu),3,0,Math.PI*2);ctx.fillStyle=colors.accent;ctx.fill();ctx.beginPath();ctx.arc(x,toY(last.gpu),3,0,Math.PI*2);ctx.fillStyle=colors.warn;ctx.fill();};
  return <canvas ref={canvasRef} style={{width:"100%",height:160,borderRadius:8,background:colors.bg1}}/>;
}