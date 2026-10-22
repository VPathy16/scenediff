"use strict";
(() => {
  const boot = JSON.parse(document.getElementById("boot").textContent);
  let data = boot.report;
  let fileIndex = 0, entityKey = null, tab = "properties", selectedProperty = null;
  let query = "", conflictsOnly = false, showUnchanged = false, mode = "git", rawMode = "diff";
  let decisions = data?.review?.decisions || {};
  const $ = id => document.getElementById(id);
  const escape = value => String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"}[char]));
  const labels = {unchanged:"Unchanged", left:"A changed", right:"B changed", both:"Both agree", combined:"Independent edits", conflict:"Conflict", added:"Added", removed:"Removed", modified:"Modified"};
  const kindLabels = {node:"Node", sub_resource:"Embedded resource", ext_resource:"External resource", gd_scene:"Scene metadata", gd_resource:"Resource metadata", connection:"Signal connection", resource:"Resource", order:"Child ordering", editable:"Editable instance"};
  const badge = (status, label) => `<span class="badge ${escape(status)}">${escape(label || labels[status] || status)}</span>`;
  const file = () => data?.files[fileIndex];
  const entity = () => file()?.entities.find(item => item.key === entityKey);
  const decisionKey = (f, e, p) => JSON.stringify([f.path, e?.key || "$file", p]);
  const empty = (title, message, icon="◇") => `<div class="empty"><span class="empty-icon">${icon}</span><strong>${escape(title)}</strong><p>${escape(message)}</p></div>`;
  const concise = value => value === null ? "Absent / no override" : value.length > 110 ? value.slice(0, 107) + "…" : value;

  function toast(message) {
    $("toast").textContent = message;
    $("toast").hidden = false;
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => $("toast").hidden = true, 3300);
  }
  function changedLabel(item) {
    if (data.three_way && !item.exists[0] && (item.exists[1] || item.exists[2])) return item.status === "conflict" ? "conflict" : "added";
    if (data.three_way && item.exists[0] && (!item.exists[1] || !item.exists[2])) return item.status === "conflict" ? "conflict" : "removed";
    return item.status;
  }
  function conflicts() {
    const list = [];
    for (const f of data?.files || []) {
      if (!f.entities.length && f.status === "conflict") list.push({f, e:null, prop:"$file", key:decisionKey(f,null,"$file")});
      for (const e of f.entities) {
        if (e.structural_conflict) list.push({f,e,prop:"$structure",key:decisionKey(f,e,"$structure")});
        for (const row of e.rows) if (row.status === "conflict") list.push({f,e,prop:row.property,key:decisionKey(f,e,row.property)});
      }
    }
    return list;
  }
  function selectFile(index) {
    fileIndex = index;
    const f = file();
    entityKey = f?.entities.find(e => e.status === "conflict")?.key || f?.entities.find(e => e.status !== "unchanged")?.key || f?.entities[0]?.key || null;
    selectedProperty = null;
    tab = f?.entities.length ? "properties" : "raw";
    render();
  }
  function selectEntity(key) {
    entityKey = key; selectedProperty = null; tab = "properties"; render();
  }
  function visibleEntity(e) {
    return (!conflictsOnly || e.status === "conflict") && (!query || (e.label + " " + e.path).toLowerCase().includes(query));
  }
  function renderSidebar() {
    $("file-count").textContent = data.files.length;
    $("file-list").innerHTML = data.files.map((f,i) => {
      if (conflictsOnly && f.status !== "conflict") return "";
      if (query && !f.path.toLowerCase().includes(query) && !f.entities.some(visibleEntity)) return "";
      const name = f.path.split("/").pop();
      const icon = f.kind === "scene" ? "▧" : f.kind === "resource" ? "◇" : f.kind === "binary" ? "◩" : "{ }";
      return `<button class="file-row ${i === fileIndex ? "active" : ""}" data-file="${i}" title="${escape(f.path)}" aria-pressed="${i === fileIndex}"><span class="file-icon">${icon}</span><span class="filename">${escape(name)}</span>${badge(changedLabel(f))}</button>`;
    }).join("") || `<p class="small muted">No files match this filter.</p>`;
    $("file-list").querySelectorAll("[data-file]").forEach(button => button.onclick = () => selectFile(Number(button.dataset.file)));
    const entities = file()?.entities || [];
    $("entity-count").textContent = entities.filter(e => e.kind === "node").length;
    const groups = [
      ["", entities.filter(e => e.kind === "node")],
      ["RESOURCES", entities.filter(e => ["sub_resource","ext_resource","resource"].includes(e.kind))],
      ["STRUCTURE & SIGNALS", entities.filter(e => !["node","sub_resource","ext_resource","resource"].includes(e.kind))]
    ];
    $("scene-tree").innerHTML = groups.map(([title,items]) => {
      items = items.filter(visibleEntity);
      if (!items.length) return "";
      return (title ? `<div class="tree-group">${title}</div>` : "") + items.map(e => {
        const indent = e.kind === "node" && e.path !== "." ? Math.min(e.path.split("/").length, 5)*13 : 0;
        const name = e.kind === "node" ? e.label.split("/").pop() : e.label;
        return `<button class="tree-row ${e.key === entityKey ? "active" : ""}" data-entity="${escape(e.key)}" style="padding-left:${10+indent}px" title="${escape(e.label)}" aria-pressed="${e.key === entityKey}"><span class="tree-icon">${e.kind === "node" ? "◇" : e.kind === "connection" ? "ϟ" : "▧"}</span><span class="tree-name">${escape(name)}</span><i class="dot ${changedLabel(e)}" aria-label="${escape(labels[e.status])}"></i></button>`;
      }).join("");
    }).join("") || `<p class="small muted" style="padding:10px">${entities.length ? "No entities match this filter." : "Open a scene or resource to explore its structure."}</p>`;
    $("scene-tree").querySelectorAll("[data-entity]").forEach(button => button.onclick = () => selectEntity(button.dataset.entity));
  }
  function displayValue(value) {
    if (value === null) return '<span class="absent">Absent / no override</span>';
    let swatch = "";
    const color = value.match(/^Color\(\s*([\d.eE+-]+),\s*([\d.eE+-]+),\s*([\d.eE+-]+),\s*([\d.eE+-]+)\s*\)$/);
    if (color) {
      const components = color.slice(1,4).map(v => Math.max(0,Math.min(255,Math.round(Number(v)*255))));
      const alpha = Math.max(0,Math.min(1,Number(color[4])));
      if (components.every(Number.isFinite) && Number.isFinite(alpha)) swatch = `<span class="value-color" style="background:rgba(${components.join(",")},${alpha})"></span>`;
    }
    return `${swatch}<span class="value">${escape(value)}</span>`;
  }
  function renderProperties() {
    const e = entity();
    if (!e) return empty("Choose a node or resource", "Select an item in the scene outline to inspect its properties.");
    const rows = e.rows.filter(row => (showUnchanged || row.status !== "unchanged") && (!conflictsOnly || row.status === "conflict"));
    if (!selectedProperty) selectedProperty = e.structural_conflict ? "$structure" : rows.find(r => r.status === "conflict")?.property || rows[0]?.property;
    let html = e.structural_conflict ? `<div class="inline-note">Structural conflict: ${escape(e.structural_conflict)}. Review the node or resource as a whole, including its dependencies.</div>` : "";
    if (!rows.length) return html + empty("No matching property changes", showUnchanged ? "Try turning off the conflict filter." : "Enable “Show unchanged” to inspect every serialized property.", "✓");
    const cols = data.three_way ? [0,1,2] : [1,2];
    html += `<table class="properties"><thead><tr><th>PROPERTY</th>${cols.map(c => `<th>${["BASE","A · LEFT","B · RIGHT"][c]}</th>`).join("")}</tr></thead><tbody>`;
    html += rows.map(row => `<tr tabindex="0" role="button" aria-label="Review ${escape(row.property)}" class="${selectedProperty === row.property ? "selected" : ""}" data-property="${escape(row.property)}"><td class="property-name">${escape(row.property.replace(/^@/,""))}${decisions[decisionKey(file(),e,row.property)] ? ' <span title="Decision recorded" style="color:var(--purple)">✓</span>' : ""}</td>${cols.map(c => {
      const changed = c > 0 && row.status !== "unchanged" && (data.three_way ? row.values[c] !== row.values[0] : c === 2);
      return `<td class="${changed ? row.status === "conflict" ? "clash" : "changed" : ""}">${displayValue(row.values[c])}</td>`;
    }).join("")}</tr>`).join("") + "</tbody></table>";
    return html;
  }
  function renderEntityList(kinds) {
    const items = file()?.entities.filter(e => kinds.includes(e.kind) && visibleEntity(e)) || [];
    return items.map(e => `<button class="entity-card" data-select="${escape(e.key)}"><span class="tree-icon">◇</span><span class="label">${escape(e.label)}<small>${e.rows.filter(r => r.status !== "unchanged").length} changed properties · ${escape(kindLabels[e.kind] || e.kind)}</small></span>${badge(changedLabel(e))}</button>`).join("") || empty("Nothing here", "No matching items in this file.");
  }
  function renderRaw() {
    const f = file();
    if (!f) return "";
    const options = [["diff","A ↔ B diff"],...(data.three_way ? [["0","Base source"]] : []),["1","A source"],["2","B source"]];
    const controls = `<div class="raw-controls">${options.map(([id,label]) => `<button data-raw="${id}" class="${rawMode === id ? "active" : ""}">${label}</button>`).join("")}</div>`;
    if (f.kind === "binary") return `<div class="file-status-card">Binary asset fingerprints${f.sources.map((s,i) => `<p><b>${["Base","A","B"][i]}</b><br><code>${escape(s || "Absent")}</code></p>`).join("")}</div>`;
    const content = rawMode === "diff" ? f.raw_diff : f.sources[Number(rawMode)];
    if (!content) return controls + empty(content === null ? "File absent" : "No text differences", "Select another version to inspect its source.", "▤");
    return controls + `<pre class="code-diff"><code>${content.split("\n").map(line => `<span class="code-line ${rawMode === "diff" ? line.startsWith("+") ? "add" : line.startsWith("-") ? "del" : line.startsWith("@@") ? "hunk" : "" : ""}">${escape(line)}</span>`).join("")}</code></pre>`;
  }
  function reviewTarget() {
    const f = file(), e = entity();
    if (!f || !data.three_way) return null;
    if (!f.entities.length) return f.status !== "unchanged" ? {f,e:null,prop:"$file",values:f.sources} : null;
    if (selectedProperty === "$structure" && e?.structural_conflict) return {f,e,prop:"$structure",values:e.exists.map((exists,i) => exists ? `Keep ${["base","A","B"][i]} structure` : "Remove entity")};
    const row = e?.rows.find(r => r.property === selectedProperty);
    return row && row.status !== "unchanged" ? {f,e,prop:row.property,values:row.values} : null;
  }
  function renderResolution() {
    const target = reviewTarget();
    if (!target) { $("resolution").innerHTML = ""; return; }
    const key = decisionKey(target.f,target.e,target.prop);
    const decision = decisions[key];
    const title = target.prop === "$file" ? "Review file" : target.prop === "$structure" ? "Review structural conflict" : `Review: ${target.prop.replace(/^@/, "")}`;
    $("resolution").innerHTML = `<div class="resolution-card"><h3>${escape(title)} ${decision ? badge("chosen","Decision recorded") : ""}</h3><p>Choose a version for the review plan. Applying a scene merge requires a separate edit and validation in Godot.</p><div class="choice-list">${[1,2].map(i => `<button class="choice ${decision?.choice === ["base","left","right"][i] ? "selected" : ""}" data-choice="${["base","left","right"][i]}"><span class="radio"></span><span>Use ${i === 1 ? "A" : "B"}<small>${escape(target.prop === "$file" ? target.values[i] === null ? "Delete file" : "Whole file version" : concise(target.values[i]))}</small></span></button>`).join("")}</div>${target.prop.startsWith("$") ? "" : `<div class="custom-choice"><input id="custom-value" aria-label="Custom Godot property value" placeholder='Custom value, e.g. Vector2(100, 50)' value="${escape(decision?.choice === "custom" ? decision.value : "")}"><button id="choose-custom">Use custom</button></div>`}<button class="clear-choice" id="clear-choice">${decision ? "Clear decision" : "Leave unresolved"}</button>${target.e?.structural_conflict && target.prop !== "$structure" ? '<button id="review-structure" class="quiet">Review structure →</button>' : ""}</div>`;
    $("resolution").querySelectorAll("[data-choice]").forEach(button => button.onclick = () => choose(button.dataset.choice));
    if ($("choose-custom")) $("choose-custom").onclick = () => {
      const value = $("custom-value").value;
      if (!value.trim()) { toast("Enter a Godot value, or choose A/B to remove an override."); return; }
      choose("custom",value);
    };
    $("clear-choice").onclick = () => { delete decisions[key]; render(); };
    if ($("review-structure")) $("review-structure").onclick = () => { selectedProperty = "$structure"; render(); };
  }
  function choose(choice,value) {
    const target = reviewTarget();
    if (!target) return;
    decisions[decisionKey(target.f,target.e,target.prop)] = {file:target.f.path, entity:target.e?.key || null, property:target.prop, choice,
      value:choice === "custom" ? value : target.prop === "$file" ? undefined : target.values[choice === "left" ? 1 : 2]};
    render();
  }
  function renderInspector() {
    const all = conflicts();
    const resolved = all.filter(c => decisions[c.key]).length;
    $("progress-title").innerHTML = `${resolved}<small> / ${all.length} decisions</small>`;
    $("progress-fill").style.width = `${all.length ? resolved/all.length*100 : 100}%`;
    $("progress-description").textContent = all.length ? `${all.length-resolved} conflict decisions remain. Choices are recorded for review.` : data.three_way ? "No serialized property conflicts found. Review notes and behavior before merging." : "Two-way comparison. Add a common base to detect conflicts.";
    $("next-conflict").disabled = !all.some(c => !decisions[c.key]);
    const f = file(), e = entity();
    if (!f) { $("impact").innerHTML = '<p class="small muted">No changed files.</p>'; $("warnings").innerHTML = ""; return; }
    const existence = e?.exists || f.exists;
    let impact = `<div class="impact-versions">${(data.three_way ? [0,1,2] : [1,2]).map(i => `<span class="${existence[i] ? "" : "missing"}">${["Base","A","B"][i]} ${existence[i] ? "✓" : "—"}</span>`).join("")}</div>`;
    if (e?.lines.some(Boolean)) impact += `<div class="impact-item"><b>SOURCE LOCATION</b>${e.lines.map((line,i) => line ? `${["Base","A","B"][i]}: line ${line}` : "").filter(Boolean).join(" · ")}</div>`;
    if (e?.used_by.some(users => users.length)) impact += `<div class="impact-item"><b>RESOURCE REFERENCES</b>${e.used_by.map((users,i) => users.length ? `<p><b>${["Base","A","B"][i]}</b>${users.map(u => `<code>${escape(u)}</code>`).join("<br>")}</p>` : "").join("")}</div>`;
    if (f.referenced_by?.length) impact += `<div class="impact-item"><b>REFERENCED BY · BRANCH B</b>${f.referenced_by.map(p => `<code>${escape(p)}</code>`).join("<br>")}</div>`;
    if (!e?.used_by.some(users => users.length) && !f.referenced_by?.length) impact += '<p class="small muted">Inspect the serialized properties and raw source to understand the scope of this change.</p>';
    $("impact").innerHTML = impact;
    const warnings = [...f.warnings,...(e?.notes || [])];
    if (e?.kind === "order") warnings.push("Child order can affect UI layout and processing. Review the complete order when both branches add children.");
    if (e?.rows.some(row => row.property === "@instance")) warnings.push("Inherited values and runtime behavior are not expanded here.");
    $("warnings").innerHTML = warnings.map(w => `<div class="warning">${escape(w)}</div>`).join("") || '<p class="small muted">No parser or reference warnings for this file. Runtime behavior has not been validated in Godot.</p>';
    $("footer-status").textContent = `${data.summary.changed_files} changed files · ${all.length-resolved} pending conflict decisions`;
  }
  function render() {
    if (!data) return;
    $("project-name").textContent = data.context.repository || "Local comparison";
    $("base-label").textContent = data.three_way ? data.labels[0] : "Not provided";
    $("a-label").textContent = data.labels[1]; $("b-label").textContent = data.labels[2];
    $("source-badge").textContent = data.context.source === "demo" ? "EXAMPLE PROJECT" : data.context.source === "git" ? "COMMITTED SNAPSHOTS" : "LOCAL FILES";
    $("new-comparison").textContent = boot.live ? "＋ New comparison" : "Open local app";
    renderSidebar();
    const f = file(), e = entity();
    const additions = data.files.flatMap(f => f.entities).filter(e => changedLabel(e) === "added" && e.kind === "node").length;
    $("summary").innerHTML = `<span><strong>${data.summary.changed_files}</strong> changed files</span><span class="red"><strong>${data.summary.conflicts}</strong> conflict items</span><span class="green"><strong>${additions}</strong> added nodes</span>`;
    $("breadcrumb").textContent = f?.path || "Project comparison";
    $("entity-title").textContent = e?.label || f?.path.split("/").pop() || "Everything is in sync";
    $("entity-subtitle").innerHTML = f ? `${escape(e ? kindLabels[e.kind] || e.kind : f.kind === "text" ? "Text file" : f.kind)} ${badge(changedLabel(e || f))}${data.three_way ? "" : '<span class="muted">Two-way comparison</span>'}` : "No committed differences between these snapshots.";
    document.querySelectorAll("[data-tab]").forEach(button => button.setAttribute("aria-selected",button.dataset.tab === tab));
    $("unchanged-toggle").hidden = tab !== "properties";
    if (!f) $("comparison").innerHTML = empty("No changes to review", "These snapshots contain the same committed files.", "✓");
    else if (tab === "properties") $("comparison").innerHTML = renderProperties();
    else if (tab === "resources") $("comparison").innerHTML = renderEntityList(["ext_resource","sub_resource","resource"]);
    else if (tab === "signals") $("comparison").innerHTML = renderEntityList(["connection"]);
    else $("comparison").innerHTML = renderRaw();
    $("comparison").querySelectorAll("[data-property]").forEach(row => {
      const activate = () => { selectedProperty = row.dataset.property; render(); };
      row.onclick = activate; row.onkeydown = event => { if (["Enter"," "].includes(event.key)) { event.preventDefault(); activate(); } };
    });
    $("comparison").querySelectorAll("[data-select]").forEach(button => button.onclick = () => selectEntity(button.dataset.select));
    $("comparison").querySelectorAll("[data-raw]").forEach(button => button.onclick = () => { rawMode = button.dataset.raw; render(); });
    renderResolution(); renderInspector();
  }
  function download(name,contents,type) {
    const url = URL.createObjectURL(new Blob([contents],{type}));
    const anchor = document.createElement("a"); anchor.href=url; anchor.download=name; anchor.click();
    setTimeout(() => URL.revokeObjectURL(url),1000);
  }
  function reviewPlan() {
    return {format:"scenediff-review-plan",version:1,report_id:data.id,created_at:new Date().toISOString(),labels:data.labels,context:data.context,
      note:"Review decisions only. No scene files have been merged or validated in Godot. Custom values are not syntax-checked.",
      decisions:Object.values(decisions),unresolved:conflicts().filter(c => !decisions[c.key]).map(c => ({file:c.f.path,entity:c.e?.key || null,property:c.prop}))};
  }
  $("export-plan").onclick = () => download("scenediff-review.json",JSON.stringify(reviewPlan(),null,2),"application/json");
  $("save-report").onclick = () => {
    const clone = document.documentElement.cloneNode(true);
    clone.querySelector("#boot").textContent = JSON.stringify({live:false,token:"",report:{...data,review:{decisions}}}).replace(/</g,"\\u003c").replace(/>/g,"\\u003e").replace(/&/g,"\\u0026");
    clone.querySelector("#compare-dialog").removeAttribute("open");
    clone.querySelector("#toast").hidden = true;
    // Do not retain local paths or branch inputs from the comparison dialog.
    clone.querySelectorAll("#compare-form input").forEach(input => input.removeAttribute("value"));
    download("scenediff-report.html","<!doctype html>\n"+clone.outerHTML,"text/html");
    toast("Saved a self-contained report with your review decisions.");
  };
  $("next-conflict").onclick = () => {
    const next = conflicts().find(c => !decisions[c.key]);
    if (!next) return;
    fileIndex=data.files.indexOf(next.f); entityKey=next.e?.key || null; selectedProperty=next.prop;
    tab=next.e ? "properties" : "raw"; query=""; $("search").value=""; render();
    $("resolution").scrollIntoView({behavior:"smooth",block:"nearest"});
  };
  $("search").oninput = event => { query=event.target.value.toLowerCase(); renderSidebar(); };
  $("conflicts-only").onchange = event => { conflictsOnly=event.target.checked; render(); };
  $("show-unchanged").onchange = event => { showUnchanged=event.target.checked; render(); };
  document.querySelectorAll("[data-tab]").forEach(button => button.onclick = () => { tab=button.dataset.tab; render(); });
  document.querySelectorAll("[data-mode]").forEach(button => button.onclick = () => {
    mode=button.dataset.mode;
    $("git-fields").hidden=mode !== "git"; $("file-fields").hidden=mode !== "files";
    document.querySelectorAll("[data-mode]").forEach(b => b.classList.toggle("active",b.dataset.mode === mode));
    $("form-error").textContent="";
  });
  $("new-comparison").onclick = () => {
    if (!boot.live) { toast("Run “scenediff serve” on your computer to start a new comparison."); return; }
    $("compare-dialog").showModal();
  };
  $("close-dialog").onclick = () => $("compare-dialog").close();
  $("compare-form").onsubmit = async event => {
    event.preventDefault(); $("form-error").textContent=""; $("submit-compare").disabled=true; $("submit-compare").textContent="Comparing…";
    try {
      let payload;
      if (mode === "git") {
        payload=Object.fromEntries(new FormData(event.target));
        if (!payload.repo || !payload.left || !payload.right) throw Error("Enter the repository path and both branches.");
      } else {
        const a=$("file-a").files[0], b=$("file-b").files[0], base=$("file-base").files[0];
        if (!a || !b) throw Error("Choose version A and version B.");
        if ([a,b,base].some(f => f && f.size > 8*1024*1024)) throw Error("Each file must be 8 MiB or smaller.");
        payload={left:await a.text(),right:await b.text(),base:base ? await base.text() : null,name:b.name};
      }
      const response=await fetch(`/api/${mode}`,{method:"POST",headers:{"Content-Type":"application/json","X-SceneDiff-Token":boot.token},body:JSON.stringify(payload)});
      const result=await response.json(); if (!response.ok) throw Error(result.error || "Comparison failed");
      data=result; decisions={}; query=""; $("search").value=""; conflictsOnly=false; $("conflicts-only").checked=false;
      selectFile(0); $("compare-dialog").close();
    } catch (error) { $("form-error").textContent=error.message; }
    finally { $("submit-compare").disabled=false; $("submit-compare").textContent="Compare changes →"; }
  };
  document.addEventListener("keydown",event => {
    if (event.ctrlKey || event.metaKey || event.altKey || $("compare-dialog").open || ["INPUT","TEXTAREA","SELECT"].includes(document.activeElement.tagName)) return;
    if (event.key.toLowerCase() === "a") choose("left");
    if (event.key.toLowerCase() === "b") choose("right");
  });
  selectFile(data?.files.findIndex(f => f.status === "conflict") >= 0 ? data.files.findIndex(f => f.status === "conflict") : 0);
})();
