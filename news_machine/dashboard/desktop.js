/* Newsroom OS — desktop workspace shell.
   Keeps the existing API, stream.js, data IDs and audio event flow intact. */
(() => {
  if (!document.body.classList.contains("studio")) return;
  const $ = selector => document.querySelector(selector);
  const player = $("#audio-player");
  const main = $(".workspace-shell");
  const editor = $("#audio-editor");
  if (!player || !main || !editor) return;

  document.body.classList.add("daw-app");
  main.classList.add("daw-workspace");
  const settingsKey = "newsroom.desktop.v1";
  let settings = {};
  try { settings = JSON.parse(localStorage.getItem(settingsKey) || "{}"); } catch {}
  const save = () => { try { localStorage.setItem(settingsKey, JSON.stringify(settings)); } catch {} };
  const clamp = (n, min, max) => Math.min(max, Math.max(min, n));
  const fmt = seconds => {
    const value = Math.max(0, Math.floor(Number(seconds) || 0));
    return [Math.floor(value / 3600), Math.floor(value / 60) % 60, value % 60].map(v => String(v).padStart(2, "0")).join(":");
  };
  const icon = (name) => ({
    play:"▶", pause:"Ⅱ", start:"⏮", rewind:"↶", forward:"↷", mute:"♪",
    browser:"☷", panel:"▥", dock:"▤", record:"●", refresh:"↻"
  })[name] || name;

  const menubar = document.createElement("div");
  menubar.className = "daw-menubar";
  menubar.innerHTML = '<div class="daw-menugroup"><span class="daw-app-name">NEWSROOM <b>STUDIO</b></span><span class="daw-menusep"></span><button type="button" data-daw="home">Arquivo</button><button type="button" data-daw="left">Explorador</button><button type="button" data-daw="bottom">Painéis</button><button type="button" data-daw="right">Inspetor</button></div><div class="daw-menugroup"><span class="daw-workspace-label">EDIÇÃO DE ÁUDIO · MULTITRILHA</span><span class="daw-workspace-key">Ctrl+1/2/3: zoom de detalhe</span></div>';
  $(".topbar").insertAdjacentElement("afterend", menubar);

  const toolbar = document.createElement("div");
  toolbar.className = "daw-transport";
  toolbar.innerHTML = '<div class="daw-transport-primary"><button type="button" title="Voltar ao início (Home)" data-daw="start">'+icon("start")+'</button><button type="button" title="Voltar 5 segundos (J)" data-daw="rewind">'+icon("rewind")+'</button><button class="daw-play" type="button" title="Reproduzir/Pausar (Espaço)" data-daw="play" aria-label="Reproduzir">'+icon("play")+'</button><button type="button" title="Avançar 5 segundos (L)" data-daw="forward">'+icon("forward")+'</button><div class="daw-transport-clock" id="daw-position">00:00:00</div><span class="daw-clock-separator">/</span><div class="daw-transport-duration" id="daw-duration">00:00:00</div></div><div class="daw-transport-secondary"><label class="daw-rate">Velocidade <select id="daw-speed"><option value="0.75">0,75×</option><option value="1" selected>1×</option><option value="1.25">1,25×</option><option value="1.5">1,5×</option><option value="2">2×</option></select></label><label class="daw-volume">Volume <input id="daw-volume" type="range" min="0" max="100" value="100" aria-label="Volume"></label><span class="daw-transport-divider"></span><div id="daw-view-mount"></div><div id="daw-zoom-mount"></div></div>';
  menubar.insertAdjacentElement("afterend",toolbar);

  const stage = document.createElement("div"); stage.className = "daw-stage";
  const browser = document.createElement("aside"); browser.className = "daw-browser";
  const canvas = document.createElement("section"); canvas.className = "daw-canvas";
  const right = document.createElement("aside"); right.className = "daw-sidepanel";
  const dock = document.createElement("section"); dock.className = "daw-dock";
  const status = document.createElement("footer"); status.className = "daw-statusbar";
  stage.append(browser, canvas, right);
  main.prepend(stage, dock);

  browser.innerHTML = '<div class="daw-pane-heading"><span>PROJETO & FONTES</span><button type="button" title="Recolher explorador" data-daw="left">‹</button></div>';
  browser.append($(".workspace-hero"), $(".model-pipeline"));
  canvas.innerHTML = '<div class="daw-pane-heading daw-canvas-heading"><span>EDITOR / TIMELINE</span><span class="daw-inline-hint">Arraste a régua para navegar · Ctrl+roda para zoom</span></div>';
  canvas.append($(".audio-desk"), editor);
  right.innerHTML = '<div class="daw-pane-heading"><span>PROPRIEDADES</span><button type="button" title="Ocultar inspetor" data-daw="right">×</button></div><div class="daw-empty-selection" id="daw-empty-selection"><span class="daw-empty-symbol">⌗</span><b>Nenhum clipe selecionado</b><p>Selecione uma fala na timeline para corrigir texto, dividir o segmento ou solicitar uma nova diarização.</p></div><div class="daw-analytics-label">ANÁLISE DA TRANSMISSÃO</div>';
  right.insertBefore($("#clip-inspector"), $("#daw-empty-selection"));
  right.append($(".chart-grid"));
  dock.innerHTML = '<div class="daw-dock-top"><span>PAINÉIS DE TRABALHO</span><button type="button" data-daw="bottom" title="Recolher painéis">▾</button></div>';
  dock.append($(".tabs"), ...Array.from(main.querySelectorAll(".tab-panel")));
  const dialogs = Array.from(main.querySelectorAll("dialog"));
  dialogs.forEach(dialog => main.append(dialog));
  main.insertAdjacentElement("afterend", status);
  status.innerHTML = '<div class="daw-status-left"><i class="daw-status-led"></i><span>EDITOR PRONTO</span><span class="daw-status-divider">│</span><span id="daw-status-count">Aguardando segmentos</span></div><div class="daw-status-right"><span id="daw-status-media">Fonte: —</span><span class="daw-status-divider">│</span><span>44,1 / 16 kHz · timeline</span><span class="daw-status-divider">│</span><span>NEWSROOM OS</span></div>';

  const view = $(".editor-view-switch"), zoom = $(".editor-zoom-controls");
  if(view) $("#daw-view-mount").append(view);
  if(zoom) $("#daw-zoom-mount").append(zoom);

  const reflectSelection = () => {
    const inspector=$("#clip-inspector");
    const empty=$("#daw-empty-selection");
    const active=!inspector.hidden;
    empty.hidden=active;
    right.classList.toggle("daw-has-inspector",active);
  };
  new MutationObserver(reflectSelection).observe($("#clip-inspector"),{attributes:true,attributeFilter:["hidden"]});
  reflectSelection();

  function setVisible(region, visible) {
    document.body.classList.toggle("daw-hide-"+region,!visible);
    settings[region]=visible;
    save();
    if(region==="bottom" && visible) {
      const tab=$('.tabs button.active');
      if(tab && !document.querySelector('.tab-panel.active')) tab.click();
    }
    requestAnimationFrame(() => window.dispatchEvent(new Event("resize")));
  }
  for(const region of ["left","right","bottom"]) {
    if(settings[region]===false) setVisible(region,false);
  }
  const toggle = region => setVisible(region,document.body.classList.contains("daw-hide-"+region));

  const notify = message => {
    const target=$("#action-status");
    if(target) target.textContent=message;
  };
  const seek = time => {
    if (!Number.isFinite(player.duration) && player.readyState===0) {
      notify("Carregue o áudio antes de navegar.");
      return;
    }
    player.currentTime=clamp(time,0,Number.isFinite(player.duration)?player.duration:Math.max(time,0));
  };
  const play = async () => {
    if (!player.src || player.hidden) { notify("Áudio da transmissão ainda indisponível."); return; }
    if (player.paused) {
      try { await player.play(); } catch { notify("Reprodução bloqueada ou áudio indisponível."); }
    } else player.pause();
  };
  function updateTransport(){
    $("#daw-position").textContent=fmt(player.currentTime);
    $("#daw-duration").textContent=fmt(Number.isFinite(player.duration)?player.duration:0);
    const btn=$('[data-daw="play"]');
    btn.textContent=icon(player.paused?"play":"pause");
    btn.setAttribute("aria-label",player.paused?"Reproduzir":"Pausar");
    $("#daw-status-media").textContent=player.src&&!player.hidden?"Áudio carregado":"Aguardando áudio";
  }
  ["timeupdate","loadedmetadata","durationchange","play","pause","ended","emptied"].forEach(ev=>player.addEventListener(ev,updateTransport));
  updateTransport();
  $("#daw-speed").addEventListener("change",e=>{player.playbackRate=Number(e.target.value)});
  $("#daw-volume").addEventListener("input",e=>{player.volume=Number(e.target.value)/100});

  document.addEventListener("click", e => {
    const button=e.target.closest("[data-daw]");
    if(!button)return;
    const action=button.dataset.daw;
    if(action==="play") play();
    if(action==="rewind") seek(player.currentTime-5);
    if(action==="forward") seek(player.currentTime+5);
    if(action==="start") seek(0);
    if(action==="home") location.assign("/");
    if(["left","right","bottom"].includes(action))toggle(action);
  });
  document.addEventListener("keydown",e=>{
    const tag=e.target?.tagName;
    if(["INPUT","TEXTAREA","SELECT"].includes(tag)||e.target?.isContentEditable||document.querySelector("dialog[open]"))return;
    if((e.ctrlKey||e.metaKey)&&["1","2","3"].includes(e.key)){
      e.preventDefault();
      const v={1:"overview",2:"discourse",3:"detail"}[e.key];
      document.querySelector('[data-view="'+v+'"]')?.click();
      return;
    }
    if(e.ctrlKey||e.altKey||e.metaKey)return;
    if(e.code==="Space"){e.preventDefault();play()}
    if(e.key==="j"||e.key==="J")seek(player.currentTime-5);
    if(e.key==="l"||e.key==="L")seek(player.currentTime+5);
    if(e.key==="Home"){e.preventDefault();seek(0)}
    if(e.key==="Escape")$(".studio-sidebar button.active")?.blur();
  });

  // Re-open the dock when a module is opened from the sidebar.
  document.querySelectorAll(".studio-sidebar [data-open-tab]").forEach(button=>{
    button.addEventListener("click",()=>{ if(document.body.classList.contains("daw-hide-bottom"))setVisible("bottom",true); });
  });

  // Resizable inspector, project browser and lower panel (no backend state).
  const grip=(axis,name,target,side,min,max)=>{
    const el=document.createElement("div");
    el.className="daw-grip daw-grip-"+axis;
    el.setAttribute("aria-label","Redimensionar painel");
    target.appendChild(el);
    el.addEventListener("pointerdown",event=>{
      event.preventDefault();
      const from=axis==="horizontal"?event.clientX:event.clientY;
      const initial=parseFloat(getComputedStyle(main).getPropertyValue(name))||(axis==="vertical"?250:230);
      el.setPointerCapture(event.pointerId);
      const move=e=>{
        const delta=(axis==="horizontal"?e.clientX:e.clientY)-from;
        const val=clamp(initial+side*delta,min,max);
        main.style.setProperty(name,val+"px");
        settings[name]=val; save();
        window.dispatchEvent(new Event("resize"));
      };
      const end=()=>{el.removeEventListener("pointermove",move);el.removeEventListener("pointerup",end);};
      el.addEventListener("pointermove",move);
      el.addEventListener("pointerup",end);
    });
  };
  for(const [name,min,max] of [["--daw-browser",160,380],["--daw-inspector",260,540],["--daw-dock",145,520]]){
    if(Number.isFinite(Number(settings[name])))main.style.setProperty(name,clamp(Number(settings[name]),min,max)+"px");
  }
  grip("horizontal","--daw-browser",browser,1,160,380);
  grip("horizontal","--daw-inspector",right,-1,260,540);
  grip("vertical","--daw-dock",dock,-1,145,520);

  // Keep the status bar fresh without polling any additional API.
  const summary=$("#timeline-summary");
  if(summary){
    const sync=()=>$("#daw-status-count").textContent=summary.textContent||"Aguardando segmentos";
    new MutationObserver(sync).observe(summary,{childList:true,characterData:true,subtree:true});
    sync();
  }
})();